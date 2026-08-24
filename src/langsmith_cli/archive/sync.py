"""Idempotent project-day export and canonicalization."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from enum import Enum
import base64
from collections.abc import Iterator as IteratorABC
import json
from pathlib import Path
import tempfile
from typing import TYPE_CHECKING, Any, Iterator, Protocol
from uuid import uuid4
from uuid import UUID

from langsmith_cli.archive.models import (
    ARCHIVE_SCHEMA_VERSION,
    ArchiveManifest,
    ArchivePhase,
    ArchiveProject,
    PhaseRecord,
    PhaseStatus,
)
from langsmith_cli.archive.parquet import (
    ARCHIVE_DECIMAL_MAP_COLUMNS,
    ARCHIVE_INTEGER_MAP_COLUMNS,
    ARCHIVE_JSON_COLUMNS,
    ARCHIVE_JSON_LIST_COLUMNS,
    ARCHIVE_JSON_OBJECT_COLUMNS,
    ARCHIVE_METADATA_COLUMN,
    ARCHIVE_STRING_LIST_COLUMNS,
    ARCHIVE_TYPED_MAP_COLUMNS,
    BULK_EXPORT_JSON_COLUMNS,
)
from langsmith_cli.archive.duckdb import (
    ARCHIVE_PARQUET_COPY_OPTIONS,
    archive_duckdb_connection,
    configure_duckdb_s3,
)
from langsmith_cli.archive.repository import (
    ManifestSnapshot,
    ensure_project_record,
    manifest_key,
    read_manifest_snapshot,
    write_manifest,
)
from langsmith_cli.archive.storage import ArchiveStore

if TYPE_CHECKING:
    from langsmith.schemas import Run
    from langsmith_cli.archive.bulk import BulkWindowExporter


# The queryable-dimension taxonomy lives in archive.parquet, shared with the
# local trace backend. Staging-only column names stay private to this writer.
_STAGING_METADATA_KEYS = "_archive_metadata_keys"
_STAGING_METADATA_VALUES = "_archive_metadata_values"

# Re-exported for tests and the public sync surface (the taxonomy predates the
# shared parquet module and existing imports reference it from here).
__all__ = [
    "ARCHIVE_DECIMAL_MAP_COLUMNS",
    "ARCHIVE_INTEGER_MAP_COLUMNS",
    "ARCHIVE_JSON_COLUMNS",
    "ARCHIVE_JSON_LIST_COLUMNS",
    "ARCHIVE_JSON_OBJECT_COLUMNS",
    "ARCHIVE_METADATA_COLUMN",
    "ARCHIVE_STRING_LIST_COLUMNS",
    "ARCHIVE_TYPED_MAP_COLUMNS",
    "BULK_EXPORT_JSON_COLUMNS",
    "due_trace_dates",
    "sync_project_day",
    "write_runs_parquet",
]


class RunsExportClient(Protocol):
    def list_runs(self, **kwargs: Any) -> Iterator[Run]: ...


def _sql_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _archive_json_default(value: object) -> object:
    """Serialize strict SDK values while preserving arbitrary binary payloads."""
    if isinstance(value, bytes):
        return {
            "__langsmith_archive_encoding__": "base64",
            "data": base64.b64encode(value).decode("ascii"),
        }
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (set, frozenset)):
        return sorted(value, key=str)
    if isinstance(value, IteratorABC):
        return list(value)
    raise TypeError(f"Unsupported archive value type: {type(value).__name__}")


def due_trace_dates(
    today: date, retention_days: int
) -> tuple[tuple[date, ArchivePhase], ...]:
    if retention_days < 4:
        raise ValueError("Archive retention must be at least 4 days")
    return (
        (today - timedelta(days=2), ArchivePhase.PRIMARY),
        (today - timedelta(days=retention_days - 2), ArchivePhase.RECONCILIATION),
    )


def _new_manifest(
    project_id: str, project_name: str, trace_date: date
) -> ArchiveManifest:
    start = datetime.combine(trace_date, time.min, tzinfo=timezone.utc)
    return ArchiveManifest(
        schema_version=ARCHIVE_SCHEMA_VERSION,
        project_id=project_id,
        project_name=project_name,
        trace_date=trace_date,
        window_start=start,
        window_end=start + timedelta(days=1),
        primary=None,
        reconciliation=None,
        canonical_key=None,
        canonical_run_count=0,
        sealed=False,
        updated_at=datetime.now(timezone.utc),
    )


# Bounded staging: the JSON reader's reconstruction buffers and string chunks scale
# with the file it scans, so converting a whole project-day at once OOMed real days
# even at a raised 2 GiB DuckDB bound. One day is staged as JSONL pieces of at most
# this many bytes and converted piece-by-piece; the conversion working set then
# scales with this budget, not the day. A single document larger than the budget
# still gets its own piece (maximum_object_size bounds any one document).
# Tested by test_oversized_days_are_staged_and_converted_in_bounded_pieces.
STAGING_PIECE_MAX_BYTES = 128 * 1024 * 1024

# The JSON reader sizes reconstruction buffers to maximum_object_size PER THREAD, so
# a blanket 100 MiB ceiling costs hundreds of MiB whether or not a large document is
# present (observed in-cluster: 100.9 MiB allocations exhausting a 1 GiB bound). The
# CLI writes every staging line itself and therefore knows each piece's true largest
# document; the reader is told exactly that, with a floor for margin.
_READ_JSON_SOURCE = "read_json_auto({path}, maximum_object_size={max_object_size})"
_MIN_MAX_OBJECT_SIZE = 16 * 1024 * 1024


def _serialize_run_line(run: Run) -> bytes:
    payload = run.model_dump(mode="python")
    # Pre-serialize nested payload fields as JSON text so read_json_auto infers
    # flat VARCHAR columns — the same shape Bulk Export v2 produces and
    # canonicalization already unifies. STRUCT inference materializes
    # payload-shaped nested columns whose memory scales with payload complexity;
    # a real project-day OOMed a 2.5 GiB DuckDB bound here.
    # Tested by test_runs_snapshot_stores_payloads_as_text_not_inferred_structs.
    for column in ARCHIVE_JSON_COLUMNS:
        value = payload.get(column)
        if value is not None:
            payload[column] = json.dumps(
                value, ensure_ascii=False, default=_archive_json_default
            )
    for column in ARCHIVE_TYPED_MAP_COLUMNS:
        value = payload[column]
        if value is not None:
            payload[column] = json.dumps(
                value, ensure_ascii=False, default=_archive_json_default
            )
    extra = run.extra or {}
    metadata_value = extra["metadata"] if "metadata" in extra else None
    if metadata_value is not None and not isinstance(metadata_value, dict):
        raise ValueError("Run extra.metadata must be an object")
    metadata = metadata_value or {}
    if any(not isinstance(key, str) for key in metadata):
        raise ValueError("Run extra.metadata keys must be strings")
    payload[_STAGING_METADATA_KEYS] = list(metadata)
    payload[_STAGING_METADATA_VALUES] = [
        _metadata_text(value) for value in metadata.values()
    ]
    line = json.dumps(payload, ensure_ascii=False, default=_archive_json_default)
    return line.encode("utf-8") + b"\n"


def _metadata_text(value: object) -> str | None:
    """Stable textual map value; full-fidelity metadata remains in ``extra``."""
    if value is None:
        return None
    if isinstance(value, Enum):
        return _metadata_text(value.value)
    if isinstance(value, str):
        return value
    if isinstance(value, (UUID, Decimal)):
        return str(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        default=_archive_json_default,
    )


def _runs_api_snapshot_select(source: str) -> str:
    excluded = ", ".join(
        (
            *ARCHIVE_STRING_LIST_COLUMNS,
            *ARCHIVE_TYPED_MAP_COLUMNS,
            _STAGING_METADATA_KEYS,
            _STAGING_METADATA_VALUES,
        )
    )
    expressions = [
        *(
            f"CAST({column} AS VARCHAR[]) AS {column}"
            for column in ARCHIVE_STRING_LIST_COLUMNS
        ),
        *(
            f"CASE WHEN {column} IS NULL THEN NULL::MAP(VARCHAR, BIGINT) "
            f"ELSE CAST(CAST({column} AS JSON) AS MAP(VARCHAR, BIGINT)) END AS {column}"
            for column in ARCHIVE_INTEGER_MAP_COLUMNS
        ),
        *(
            f"CASE WHEN {column} IS NULL THEN NULL::MAP(VARCHAR, DECIMAL(38,18)) "
            f"ELSE CAST(CAST({column} AS JSON) AS MAP(VARCHAR, DECIMAL(38,18))) END AS {column}"
            for column in ARCHIVE_DECIMAL_MAP_COLUMNS
        ),
        f"map(CAST({_STAGING_METADATA_KEYS} AS VARCHAR[]), "
        f"CAST({_STAGING_METADATA_VALUES} AS VARCHAR[])) AS {ARCHIVE_METADATA_COLUMN}",
    ]
    return f"(SELECT * EXCLUDE ({excluded}), {', '.join(expressions)} FROM {source})"


def _bulk_snapshot_select(source: str, columns: set[str]) -> str:
    typed_columns = (
        *ARCHIVE_STRING_LIST_COLUMNS,
        *ARCHIVE_TYPED_MAP_COLUMNS,
        ARCHIVE_METADATA_COLUMN,
    )
    excluded = ", ".join(column for column in typed_columns if column in columns)
    expressions = [
        *(
            f"CASE WHEN {column} IS NULL THEN NULL::VARCHAR[] "
            f"ELSE CAST(CAST({column} AS JSON) AS VARCHAR[]) END AS {column}"
            if column in columns
            else f"NULL::VARCHAR[] AS {column}"
            for column in ARCHIVE_STRING_LIST_COLUMNS
        ),
        *(
            f"CAST(CAST({column} AS JSON) AS MAP(VARCHAR, BIGINT)) AS {column}"
            if column in columns
            else f"NULL::MAP(VARCHAR, BIGINT) AS {column}"
            for column in ARCHIVE_INTEGER_MAP_COLUMNS
        ),
        *(
            f"CAST(CAST({column} AS JSON) AS MAP(VARCHAR, DECIMAL(38,18))) AS {column}"
            if column in columns
            else f"NULL::MAP(VARCHAR, DECIMAL(38,18)) AS {column}"
            for column in ARCHIVE_DECIMAL_MAP_COLUMNS
        ),
    ]
    expressions.append(
        "coalesce(CAST(json_extract(CAST(extra AS JSON), '$.metadata') "
        "AS MAP(VARCHAR, VARCHAR)), map()::MAP(VARCHAR, VARCHAR)) "
        f"AS {ARCHIVE_METADATA_COLUMN}"
    )
    exclude_sql = f" EXCLUDE ({excluded})" if excluded else ""
    return f"(SELECT *{exclude_sql}, {', '.join(expressions)} FROM {source})"


def _uuid_text(value: Any) -> str | None:
    """Canonical text for an id however the source chunk yielded it.

    arrow.uuid extension chunks yield uuid.UUID instances from to_pylist(), plain
    fixed-binary chunks yield bytes, and text raw already yields strings.
    """
    import uuid

    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, uuid.UUID):
        return str(value)
    return str(uuid.UUID(bytes=bytes(value)))


def _storage_type(data_type: Any) -> Any:
    """Portable Parquet type for archive interchange.

    Extension types (DuckDB's arrow.json, arrow.uuid) collapse to their storage —
    recursively, because real trace fields nest them inside maps and structs
    (observed in-cluster: map<string, extension<arrow.json>> vs map<string, string>
    refused to unify). UUIDs (DuckDB infers them from UUID-shaped JSON strings and
    Parquet renders them as 16-byte fixed binary) become canonical text: a
    re-serialized binares column loses the UUID annotation, and readers then match
    nothing when comparing ids to strings.
    """
    import pyarrow

    if isinstance(data_type, pyarrow.BaseExtensionType):
        return _storage_type(data_type.storage_type)
    if pyarrow.types.is_fixed_size_binary(data_type) and data_type.byte_width == 16:
        return pyarrow.string()
    if pyarrow.types.is_list(data_type):
        return pyarrow.list_(_storage_type(data_type.value_type))
    if pyarrow.types.is_large_list(data_type):
        return pyarrow.large_list(_storage_type(data_type.value_type))
    if pyarrow.types.is_map(data_type):
        return pyarrow.map_(
            _storage_type(data_type.key_type), _storage_type(data_type.item_type)
        )
    if pyarrow.types.is_struct(data_type):
        return pyarrow.struct(
            [
                pyarrow.field(
                    child.name, _storage_type(child.type), nullable=child.nullable
                )
                for child in data_type
            ]
        )
    return data_type


def _storage_schema(schema: Any) -> Any:
    import pyarrow

    return pyarrow.schema(
        field.with_type(_storage_type(field.type)) for field in schema
    )


def _storage_column(column: Any, target_type: Any) -> Any:
    import pyarrow

    if column.type.equals(target_type):
        return column
    if pyarrow.types.is_string(target_type) and (
        pyarrow.types.is_fixed_size_binary(column.type)
        or (
            isinstance(column.type, pyarrow.BaseExtensionType)
            and pyarrow.types.is_fixed_size_binary(column.type.storage_type)
        )
    ):
        formatted = [_uuid_text(value) for value in column.to_pylist()]
        return pyarrow.chunked_array([pyarrow.array(formatted, type=target_type)])
    if isinstance(column.type, pyarrow.BaseExtensionType):
        column = pyarrow.chunked_array(
            [chunk.storage for chunk in column.chunks],
            type=column.type.storage_type,
        )
    return column.cast(target_type)


def _storage_table(table: Any) -> Any:
    import pyarrow

    schema = _storage_schema(table.schema)
    columns = [
        _storage_column(table.column(index), field.type)
        for index, field in enumerate(schema)
    ]
    return pyarrow.table(columns, schema=schema)


def _combine_parquet_parts(part_paths: list[Path], target: Path) -> None:
    """
    Concatenate Parquet parts into one file, one row group at a time.

    Pieces are inferred independently, so a column that is all-null in one piece may
    carry a different type there (DuckDB writes it as the arrow.json extension type
    while pieces with values carry plain string). Extension types are stripped to
    their storage type — lossless, the storage IS the JSON text — before permissive
    schema unification, and every row group is cast to the unified schema. Working
    memory is bounded by one decoded row group (parts are written with byte-bounded
    row groups), not by the day.
    """
    import contextlib

    import pyarrow
    import pyarrow.parquet

    with contextlib.ExitStack() as stack:
        part_files = [
            stack.enter_context(pyarrow.parquet.ParquetFile(str(part)))
            for part in part_paths
        ]
        unified = pyarrow.unify_schemas(
            [_storage_schema(part.schema_arrow) for part in part_files],
            promote_options="permissive",
        )
        writer = stack.enter_context(
            pyarrow.parquet.ParquetWriter(str(target), unified, compression="zstd")
        )
        for part in part_files:
            for group_index in range(part.num_row_groups):
                table = _storage_table(part.read_row_group(group_index))
                writer.write_table(table.select(unified.names).cast(unified))


def _read_json_source(piece_path: Path, max_line_bytes: int) -> str:
    return _READ_JSON_SOURCE.format(
        path=_sql_string(str(piece_path)),
        max_object_size=max(max_line_bytes * 2, _MIN_MAX_OBJECT_SIZE),
    )


def write_runs_parquet(runs: Iterator[Run], target: Path) -> int:
    """Write strict SDK Runs through the archive's byte-bounded staging path."""
    piece_paths: list[Path] = []
    piece_max_lines: list[int] = []
    part_paths: list[Path] = []
    run_count = 0
    piece = None
    piece_bytes = 0
    try:
        for run in runs:
            encoded = _serialize_run_line(run)
            if piece is None or (
                piece_bytes and piece_bytes + len(encoded) > STAGING_PIECE_MAX_BYTES
            ):
                if piece is not None:
                    piece.close()
                piece_path = target.with_suffix(f".{len(piece_paths)}.jsonl")
                # Binary mode: piece budgets are byte-exact, and DuckDB reopens the
                # file, which Windows only guarantees after our handle closes.
                piece = piece_path.open(mode="wb")
                piece_paths.append(piece_path)
                piece_max_lines.append(0)
                piece_bytes = 0
            piece.write(encoded)
            piece_bytes += len(encoded)
            piece_max_lines[-1] = max(piece_max_lines[-1], len(encoded))
            run_count += 1
        if piece is not None:
            piece.close()
            piece = None

        with archive_duckdb_connection(target.parent) as connection:
            if not run_count:
                connection.execute(
                    "COPY (SELECT CAST(NULL AS VARCHAR) AS id WHERE false) "
                    f"TO {_sql_string(str(target))} " + ARCHIVE_PARQUET_COPY_OPTIONS
                )
            elif len(piece_paths) == 1:
                source = _runs_api_snapshot_select(
                    _read_json_source(piece_paths[0], piece_max_lines[0])
                )
                connection.execute(
                    f"COPY (SELECT * FROM {source}) "
                    f"TO {_sql_string(str(target))} " + ARCHIVE_PARQUET_COPY_OPTIONS
                )
            else:
                for piece_path, max_line in zip(piece_paths, piece_max_lines):
                    part_path = piece_path.with_suffix(".part")
                    source = _runs_api_snapshot_select(
                        _read_json_source(piece_path, max_line)
                    )
                    connection.execute(
                        f"COPY (SELECT * FROM {source}) "
                        f"TO {_sql_string(str(part_path))} "
                        + ARCHIVE_PARQUET_COPY_OPTIONS
                    )
                    part_paths.append(part_path)
                    piece_path.unlink(missing_ok=True)
                # Deliberately NOT a DuckDB COPY: any SQL scan streams fixed
                # 2048-row chunks whose memory scales with row width, so combining
                # a whale day re-inflates the working set the pieces just bounded
                # (observed in-cluster: the combine OOMed a 1 GiB bound after every
                # piece converted cleanly). Row-group-wise concatenation decodes at
                # most one ROW_GROUP_SIZE_BYTES-bounded group at a time.
                _combine_parquet_parts(part_paths, target)
        return run_count
    finally:
        if piece is not None:
            piece.close()
        for piece_path in piece_paths:
            piece_path.unlink(missing_ok=True)
        for part_path in part_paths:
            part_path.unlink(missing_ok=True)


def _write_runs_parquet(
    client: RunsExportClient, manifest: ArchiveManifest, target: Path
) -> int:
    filter_ = f'lt(start_time, "{manifest.window_end.isoformat()}")'
    return write_runs_parquet(
        client.list_runs(
            project_id=manifest.project_id,
            start_time=manifest.window_start,
            filter=filter_,
            limit=None,
        ),
        target,
    )


def _write_bulk_parquet(
    exporter: BulkWindowExporter,
    manifest: ArchiveManifest,
    target: Path,
    excluded_export_ids: frozenset[str],
) -> tuple[str, int]:
    snapshot = exporter.export_window(
        project_id=manifest.project_id,
        start_time=manifest.window_start,
        end_time=manifest.window_end,
        excluded_export_ids=excluded_export_ids,
    )
    with archive_duckdb_connection(target.parent) as connection:
        if snapshot.run_count:
            if not snapshot.file_uris:
                raise ValueError("Non-empty bulk export has no Parquet files")
            configure_duckdb_s3(connection, list(snapshot.file_uris))
            source_list = (
                "[" + ", ".join(_sql_string(uri) for uri in snapshot.file_uris) + "]"
            )
            source = (
                f"read_parquet({source_list}, union_by_name=true, "
                "hive_partitioning=false)"
            )
            counts = connection.execute(
                f"SELECT count(*), count(DISTINCT id) FROM {source}"
            ).fetchone()
            if counts is None or counts[0] != snapshot.run_count:
                raise ValueError("Bulk export row count does not match Parquet")
            if counts[0] != counts[1]:
                raise ValueError("Bulk export contains duplicate run IDs")
            columns = {
                str(row[0])
                for row in connection.execute(
                    f"DESCRIBE SELECT * FROM {source}"
                ).fetchall()
            }
            source = _bulk_snapshot_select(source, columns)
            connection.execute(
                f"COPY (SELECT * FROM {source}) TO {_sql_string(str(target))} "
                + ARCHIVE_PARQUET_COPY_OPTIONS
            )
        else:
            if snapshot.file_uris:
                raise ValueError("Empty bulk export unexpectedly published files")
            connection.execute(
                "COPY (SELECT CAST(NULL AS VARCHAR) AS id WHERE false) "
                f"TO {_sql_string(str(target))} " + ARCHIVE_PARQUET_COPY_OPTIONS
            )
    return snapshot.export_id, snapshot.run_count


def _open_parquet_source(uri: str) -> Any:
    """ParquetFile over a local path or s3:// URI through pyarrow filesystems."""
    import pyarrow.fs
    import pyarrow.parquet

    if uri.startswith("s3://"):
        filesystem, path = pyarrow.fs.FileSystem.from_uri(uri)
        return pyarrow.parquet.ParquetFile(filesystem.open_input_file(path))
    path = uri[len("file://") :] if uri.startswith("file://") else uri
    return pyarrow.parquet.ParquetFile(path)


def _payload_columns_are_text(schema: Any) -> bool:
    import pyarrow

    for field in schema:
        if field.name not in ARCHIVE_JSON_COLUMNS:
            continue
        field_type = field.type
        if isinstance(field_type, pyarrow.BaseExtensionType):
            field_type = field_type.storage_type
        if not (
            pyarrow.types.is_string(field_type)
            or pyarrow.types.is_large_string(field_type)
            or pyarrow.types.is_null(field_type)
        ):
            return False
    return True


def _query_dimensions_are_typed(schema: Any) -> bool:
    """Return whether one raw generation already satisfies canonical v2 types."""
    import pyarrow

    fields = {field.name: field.type for field in schema}
    for column in ARCHIVE_STRING_LIST_COLUMNS:
        data_type = fields[column] if column in fields else None
        if data_type is None or not pyarrow.types.is_list(data_type):
            return False
        if not pyarrow.types.is_string(data_type.value_type):
            return False
    expected_maps = {
        **dict.fromkeys(ARCHIVE_INTEGER_MAP_COLUMNS, pyarrow.int64()),
        **dict.fromkeys(ARCHIVE_DECIMAL_MAP_COLUMNS, pyarrow.decimal128(38, 18)),
        ARCHIVE_METADATA_COLUMN: pyarrow.string(),
    }
    for column, value_type in expected_maps.items():
        data_type = fields[column] if column in fields else None
        if data_type is None or not pyarrow.types.is_map(data_type):
            return False
        if not pyarrow.types.is_string(data_type.key_type):
            return False
        if not data_type.item_type.equals(value_type):
            return False
    return True


def _typed_dimension_types() -> dict[str, Any]:
    """Canonical v2 Arrow type for every promoted queryable dimension."""
    import pyarrow

    return {
        **dict.fromkeys(ARCHIVE_STRING_LIST_COLUMNS, pyarrow.list_(pyarrow.string())),
        **dict.fromkeys(
            ARCHIVE_INTEGER_MAP_COLUMNS,
            pyarrow.map_(pyarrow.string(), pyarrow.int64()),
        ),
        **dict.fromkeys(
            ARCHIVE_DECIMAL_MAP_COLUMNS,
            pyarrow.map_(pyarrow.string(), pyarrow.decimal128(38, 18)),
        ),
        ARCHIVE_METADATA_COLUMN: pyarrow.map_(pyarrow.string(), pyarrow.string()),
    }


def _dimensions_are_streamable(schema: Any) -> bool:
    """Return whether dimension columns are v2-typed, v1 JSON text, or absent.

    Any such generation upgrades row-group-wise on the bounded streaming path;
    only inferred scalar shapes that neither parse nor cast (e.g. a dimension
    inferred as a bare number) still need SQL normalization.
    """
    import pyarrow

    fields = {field.name: field.type for field in schema}
    for column in (
        *ARCHIVE_STRING_LIST_COLUMNS,
        *ARCHIVE_TYPED_MAP_COLUMNS,
        ARCHIVE_METADATA_COLUMN,
    ):
        data_type = fields.get(column)
        if data_type is None:
            continue
        if isinstance(data_type, pyarrow.BaseExtensionType):
            data_type = data_type.storage_type
        if (
            pyarrow.types.is_null(data_type)
            or pyarrow.types.is_string(data_type)
            or pyarrow.types.is_large_string(data_type)
        ):
            continue
        if column in ARCHIVE_STRING_LIST_COLUMNS:
            if pyarrow.types.is_list(data_type) or pyarrow.types.is_large_list(
                data_type
            ):
                continue
            return False
        if pyarrow.types.is_map(data_type) or pyarrow.types.is_struct(data_type):
            continue
        return False
    return True


def _integer_map_value(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("Archived token detail value is not an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    raise ValueError("Archived token detail value is not an integer")


def _decimal_map_value(value: object) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return Decimal(value)
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, str):
        # The v1 writer serialized SDK Decimal costs as JSON strings.
        try:
            return Decimal(value)
        except ArithmeticError as error:
            raise ValueError("Archived cost detail value is not a number") from error
    raise ValueError("Archived cost detail value is not a number")


def _parsed_dimension_values(column: str, values: list[object]) -> list[object]:
    """Parse one row group's v1 JSON-text dimension values into typed shapes."""
    if column in ARCHIVE_STRING_LIST_COLUMNS:
        parsed_lists: list[object] = []
        for value in values:
            decoded = json.loads(value) if isinstance(value, str) else value
            if decoded is not None and not isinstance(decoded, list):
                raise ValueError(f"Archived JSON field has an invalid type: {column}")
            parsed_lists.append(decoded)
        return parsed_lists
    parsed_maps: list[object] = []
    decimal_valued = column in ARCHIVE_DECIMAL_MAP_COLUMNS
    for value in values:
        decoded = (
            json.loads(value, parse_float=Decimal) if isinstance(value, str) else value
        )
        if decoded is None:
            parsed_maps.append(None)
            continue
        if not isinstance(decoded, dict):
            raise ValueError(f"Archived JSON field has an invalid type: {column}")
        converter = _decimal_map_value if decimal_valued else _integer_map_value
        parsed_maps.append([(key, converter(item)) for key, item in decoded.items()])
    return parsed_maps


def _derived_metadata_values(table: Any) -> list[object]:
    """Derive the metadata accelerator map from v1 JSON-text ``extra`` rows."""
    if "extra" not in table.schema.names:
        return [[] for _ in range(table.num_rows)]
    derived: list[object] = []
    for extra_text in table.column("extra").to_pylist():
        extra = json.loads(extra_text) if isinstance(extra_text, str) else None
        metadata = (
            extra.get(ARCHIVE_METADATA_COLUMN) if isinstance(extra, dict) else None
        )
        if not isinstance(metadata, dict):
            derived.append([])
            continue
        derived.append(
            [(str(key), _metadata_text(item)) for key, item in metadata.items()]
        )
    return derived


def _typed_dimensions_table(table: Any) -> Any:
    """Materialize canonical v2 dimension columns on one decoded row group.

    v2 raw already carries these exact types and passes through untouched. v1
    raw carries the same dimensions as JSON text (or omits them entirely) and is
    parsed value-by-value; ``metadata`` is derived from ``extra`` exactly as the
    v2 writers do. The working set stays one byte-bounded row group, preserving
    the constant-memory invariant during v1→v2 migration of unsealed days.
    """
    import pyarrow

    for column, target_type in _typed_dimension_types().items():
        if column in table.schema.names:
            index = table.schema.get_field_index(column)
            existing = table.column(index)
            if existing.type.equals(target_type):
                continue
            if pyarrow.types.is_null(existing.type):
                array: Any = pyarrow.nulls(table.num_rows, type=target_type)
            elif pyarrow.types.is_string(
                existing.type
            ) or pyarrow.types.is_large_string(existing.type):
                array = pyarrow.array(
                    _parsed_dimension_values(column, existing.to_pylist()),
                    type=target_type,
                )
            elif pyarrow.types.is_struct(existing.type):
                array = pyarrow.array(
                    _parsed_dimension_values(column, existing.to_pylist()),
                    type=target_type,
                )
            else:
                array = existing.cast(target_type)
            table = table.set_column(index, pyarrow.field(column, target_type), array)
        elif column == ARCHIVE_METADATA_COLUMN:
            table = table.append_column(
                pyarrow.field(column, target_type),
                pyarrow.array(_derived_metadata_values(table), type=target_type),
            )
        else:
            table = table.append_column(
                pyarrow.field(column, target_type),
                pyarrow.nulls(table.num_rows, type=target_type),
            )
    return table


def _typed_schema(schema: Any) -> Any:
    """The schema `_typed_dimensions_table` produces for one source schema."""
    import pyarrow

    targets = _typed_dimension_types()
    fields = [
        pyarrow.field(field.name, targets[field.name])
        if field.name in targets
        else field
        for field in schema
    ]
    names = set(schema.names)
    fields.extend(
        pyarrow.field(column, target_type)
        for column, target_type in targets.items()
        if column not in names
    )
    return pyarrow.schema(fields)


def _conform_table(table: Any, unified: Any) -> Any:
    """Project onto the unified schema, adding null columns a source never had."""
    import pyarrow

    arrays = []
    for field in unified:
        if field.name in table.schema.names:
            arrays.append(_storage_column(table.column(field.name), field.type))
        else:
            arrays.append(pyarrow.nulls(table.num_rows, type=field.type))
    return pyarrow.table(arrays, schema=unified)


def _canonicalize_streaming(
    source_files: list[Any], source_uris: list[str], target: Path
) -> int:
    """
    Union-and-dedupe one project-day row-group-by-row-group.

    The SQL union+window canonicalization streams fixed 2048-row chunks whose
    memory scales with row width, so whale days OOMed a 1 GiB bound after raw
    conversion was already piece-bounded (in-cluster stack: _canonicalize). The
    dedup decision only needs run IDs: the highest-rank snapshot (reconciliation)
    is copied through, lower ranks drop rows whose IDs it already covers, and the
    working set is one decoded row group plus one day of IDs.
    """
    import pyarrow
    import pyarrow.compute
    import pyarrow.parquet

    ids_per_source: list[list[str]] = []
    for source_file, uri in zip(source_files, source_uris):
        ids: list[str] = []
        for group_index in range(source_file.num_row_groups):
            column = source_file.read_row_group(group_index, columns=["id"])
            ids.extend(
                text
                for text in (
                    _uuid_text(value) for value in column.column("id").to_pylist()
                )
                if text is not None
            )
        if len(ids) != len(set(ids)):
            raise ValueError(f"Snapshot contains duplicate run IDs: {uri}")
        ids_per_source.append(ids)

    winner_index = len(source_files) - 1
    winner_ids = (
        pyarrow.array(sorted(set(ids_per_source[winner_index])))
        if len(source_files) > 1
        else None
    )
    unified = pyarrow.unify_schemas(
        [
            _typed_schema(_storage_schema(source.schema_arrow))
            for source in source_files
        ],
        promote_options="permissive",
    )
    written = 0
    writer = pyarrow.parquet.ParquetWriter(str(target), unified, compression="zstd")
    try:
        for index, source_file in enumerate(source_files):
            for group_index in range(source_file.num_row_groups):
                table = _typed_dimensions_table(
                    _storage_table(source_file.read_row_group(group_index))
                )
                if winner_ids is not None and index != winner_index:
                    # pyarrow's bundled stubs omit several pyarrow.compute kernels.
                    contained = pyarrow.compute.is_in(  # pyright: ignore[reportAttributeAccessIssue]
                        table.column("id"), value_set=winner_ids
                    )
                    keep = pyarrow.compute.invert(  # pyright: ignore[reportAttributeAccessIssue]
                        contained
                    )
                    table = table.filter(pyarrow.compute.fill_null(keep, True))
                if table.num_rows:
                    writer.write_table(_conform_table(table, unified))
                    written += table.num_rows
    finally:
        writer.close()
    metadata_rows = pyarrow.parquet.ParquetFile(str(target)).metadata.num_rows
    if metadata_rows != written:
        raise ValueError("Canonical row count mismatch after write")
    return written


def _canonicalize(store: ArchiveStore, manifest: ArchiveManifest, target: Path) -> int:
    sources: list[tuple[str, int]] = []
    if manifest.primary is not None:
        sources.append((store.object_uri(manifest.primary.raw_key), 1))
    if manifest.reconciliation is not None:
        sources.append((store.object_uri(manifest.reconciliation.raw_key), 2))
    if not sources:
        raise ValueError("Cannot canonicalize a manifest without snapshots")

    import contextlib

    with contextlib.ExitStack() as stack:
        source_files = []
        for uri, _rank in sources:
            source_files.append(stack.enter_context(_open_parquet_source(uri)))
        # Empty snapshots are written id-only: they carry no payload or dimension
        # columns and must never veto the bounded path for the day's real data —
        # an empty phase paired with a whale phase is exactly the day that OOMs
        # the SQL union.
        occupied = [source for source in source_files if source.metadata.num_rows]
        if all(
            _payload_columns_are_text(source.schema_arrow)
            and _dimensions_are_streamable(source.schema_arrow)
            for source in occupied
        ):
            return _canonicalize_streaming(
                source_files, [uri for uri, _ in sources], target
            )
    # Raw generations that predate byte-bounded staging carry inferred payload
    # STRUCTs that still need SQL normalization; they are rare (re-canonicalizing
    # an already-sealed day), so the memory-heavy path is kept only for them.
    # v1 text-dimension raw upgrades on the streaming path above, never here.
    return _canonicalize_duckdb(sources, target)


def _canonicalize_duckdb(sources: list[tuple[str, int]], target: Path) -> int:
    with archive_duckdb_connection(target.parent) as connection:
        configure_duckdb_s3(connection, [uri for uri, _ in sources])
        selects: list[str] = []
        for index, (uri, rank) in enumerate(sources):
            view = f"archive_snapshot_{index}"
            connection.execute(
                f"CREATE TEMP VIEW {view} AS SELECT *, {rank} AS snapshot_rank "
                f"FROM read_parquet({_sql_string(uri)}, union_by_name=true)"
            )
            counts = connection.execute(
                f"SELECT count(*), count(DISTINCT id) FROM {view}"
            ).fetchone()
            if counts is None or counts[0] != counts[1]:
                raise ValueError(f"Snapshot contains duplicate run IDs: {uri}")
            columns = {
                str(row[1])
                for row in connection.execute(f"PRAGMA table_info('{view}')").fetchall()
            }
            json_columns = tuple(
                column for column in ARCHIVE_JSON_COLUMNS if column in columns
            )
            typed_columns = tuple(
                column
                for column in (
                    *ARCHIVE_STRING_LIST_COLUMNS,
                    *ARCHIVE_TYPED_MAP_COLUMNS,
                    ARCHIVE_METADATA_COLUMN,
                )
                if column in columns
            )
            excluded_columns = (*json_columns, *typed_columns)
            expressions = [
                *(
                    f"CASE WHEN typeof({column}) = 'VARCHAR' "
                    f"THEN CAST({column} AS VARCHAR) "
                    f"ELSE CAST(to_json({column}) AS VARCHAR) END AS {column}"
                    for column in json_columns
                ),
                *(
                    f"CAST(CAST({column} AS JSON) AS VARCHAR[]) AS {column}"
                    if column in columns
                    else f"NULL::VARCHAR[] AS {column}"
                    for column in ARCHIVE_STRING_LIST_COLUMNS
                ),
                *(
                    f"CAST(CAST({column} AS JSON) AS MAP(VARCHAR, BIGINT)) AS {column}"
                    if column in columns
                    else f"NULL::MAP(VARCHAR, BIGINT) AS {column}"
                    for column in ARCHIVE_INTEGER_MAP_COLUMNS
                ),
                *(
                    f"CAST(CAST({column} AS JSON) "
                    f"AS MAP(VARCHAR, DECIMAL(38,18))) AS {column}"
                    if column in columns
                    else f"NULL::MAP(VARCHAR, DECIMAL(38,18)) AS {column}"
                    for column in ARCHIVE_DECIMAL_MAP_COLUMNS
                ),
            ]
            if ARCHIVE_METADATA_COLUMN in columns:
                expressions.append(
                    f"CAST({ARCHIVE_METADATA_COLUMN} AS MAP(VARCHAR, VARCHAR)) "
                    f"AS {ARCHIVE_METADATA_COLUMN}"
                )
            elif "extra" in columns:
                expressions.append(
                    "coalesce(CAST(json_extract(CAST(extra AS JSON), '$.metadata') "
                    "AS MAP(VARCHAR, VARCHAR)), map()::MAP(VARCHAR, VARCHAR)) "
                    f"AS {ARCHIVE_METADATA_COLUMN}"
                )
            else:
                expressions.append(
                    f"map()::MAP(VARCHAR, VARCHAR) AS {ARCHIVE_METADATA_COLUMN}"
                )
            exclude_sql = (
                f" EXCLUDE ({', '.join(excluded_columns)})" if excluded_columns else ""
            )
            selects.append(
                f"SELECT *{exclude_sql}, {', '.join(expressions)} FROM {view}"
            )

        union_sql = " UNION ALL BY NAME ".join(selects)
        query = (
            "SELECT * EXCLUDE (snapshot_rank, archive_row_number) FROM ("
            "SELECT *, row_number() OVER (PARTITION BY id ORDER BY snapshot_rank DESC) "
            f"AS archive_row_number FROM ({union_sql}) snapshots) ranked "
            "WHERE archive_row_number = 1"
        )
        connection.execute(
            f"COPY ({query}) TO {_sql_string(str(target))} "
            + ARCHIVE_PARQUET_COPY_OPTIONS
        )
        # Verify the artifact we will upload instead of rerunning the remote union
        # and window function. This is one local scan and proves canonical IDs are
        # unique at the actual publication boundary.
        count_row = connection.execute(
            "SELECT count(*), count(DISTINCT id) FROM "
            f"read_parquet({_sql_string(str(target))})"
        ).fetchone()
        if count_row is None:
            raise ValueError("DuckDB did not return a canonical row count")
        if count_row[0] != count_row[1]:
            raise ValueError("Canonical snapshot contains duplicate run IDs")
        return int(count_row[0])


def sync_project_day(
    client: RunsExportClient | None,
    store: ArchiveStore,
    *,
    project_id: str,
    project_name: str,
    trace_date: date,
    phase: ArchivePhase,
    existing_snapshot: ManifestSnapshot | None = None,
    manifest_known_absent: bool = False,
    existing_project: ArchiveProject | None = None,
    project_record_checked: bool = False,
    bulk_exporter: BulkWindowExporter | None = None,
) -> ArchiveManifest:
    """Export one phase and conditionally publish a canonical generation.

    The manifest is the sole mutable publication pointer. Immutable raw/canonical
    objects are uploaded first; compare-and-swap publication then ensures a stale
    worker can leave only orphan objects, never clobber a newer verified manifest.
    """
    if project_record_checked and existing_project is not None:
        if (
            existing_project.project_id != project_id
            or existing_project.project_name != project_name
        ):
            raise ValueError(
                "Archived project identity changed; migrate it before syncing"
            )
    else:
        ensure_project_record(store, project_id, project_name)
    key = manifest_key(project_id, trace_date.isoformat())
    snapshot = existing_snapshot
    if snapshot is None and not manifest_known_absent:
        snapshot = read_manifest_snapshot(store, key)
    manifest = (
        snapshot.manifest
        if snapshot is not None
        else _new_manifest(project_id, project_name, trace_date)
    )
    if manifest.project_name != project_name:
        raise ValueError(
            "Archived project name changed; migrate its manifest before syncing"
        )
    existing = manifest.phase(phase)
    if existing is not None and existing.status is PhaseStatus.VERIFIED:
        return manifest
    if manifest.sealed:
        raise ValueError("A sealed archive day is immutable")
    # INVARIANT: every new canonical publication uses the current physical schema;
    # v1 remains readable, but resuming an unsealed day upgrades it atomically.
    manifest = replace(manifest, schema_version=ARCHIVE_SCHEMA_VERSION)
    excluded_export_ids = frozenset(
        record.generation_id
        for record in (manifest.primary, manifest.reconciliation)
        if record is not None
    )
    generation_id = str(uuid4())
    raw_key = (
        f"raw/project_id={project_id}/date={trace_date.isoformat()}/"
        f"phase={phase.value}/generation={generation_id}/runs.parquet"
    )
    now = datetime.now(timezone.utc)
    with tempfile.TemporaryDirectory(prefix="langsmith-archive-") as directory:
        staging = Path(directory)
        raw_file = staging / "raw.parquet"
        if bulk_exporter is not None:
            generation_id, run_count = _write_bulk_parquet(
                bulk_exporter,
                manifest,
                raw_file,
                excluded_export_ids,
            )
            raw_key = (
                f"raw/project_id={project_id}/date={trace_date.isoformat()}/"
                f"phase={phase.value}/generation={generation_id}/runs.parquet"
            )
        else:
            if client is None:
                raise ValueError("Runs API archive sync requires a LangSmith client")
            run_count = _write_runs_parquet(client, manifest, raw_file)
        store.put_file(raw_key, raw_file)

        record = PhaseRecord(
            status=PhaseStatus.VERIFIED,
            generation_id=generation_id,
            raw_key=raw_key,
            run_count=run_count,
            verified_at=now,
        )
        manifest = manifest.with_phase(phase, record)
        canonical_generation = str(uuid4())
        canonical_key = (
            f"canonical/project_id={project_id}/date={trace_date.isoformat()}/"
            f"generation={canonical_generation}/runs.parquet"
        )
        canonical_file = staging / "canonical.parquet"
        canonical_count = _canonicalize(store, manifest, canonical_file)
        store.put_file(canonical_key, canonical_file)

    published = replace(
        manifest,
        canonical_key=canonical_key,
        canonical_run_count=canonical_count,
        sealed=phase is ArchivePhase.RECONCILIATION,
        updated_at=now,
    )
    write_manifest(
        store,
        key,
        published,
        expected_version=snapshot.version if snapshot is not None else None,
    )
    return published
