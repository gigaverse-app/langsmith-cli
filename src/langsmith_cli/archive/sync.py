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
    ArchiveManifest,
    ArchivePhase,
    ArchiveProject,
    PhaseRecord,
    PhaseStatus,
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


# Canonical Parquet stores nested provider fields as JSON text. Runs API JSONL is
# inferred as STRUCT/LIST while Bulk Export v2 emits VARCHAR for the same fields;
# normalizing here keeps reconciliation and cross-day union schema-stable.
ARCHIVE_JSON_OBJECT_COLUMNS = (
    "extra",
    "inputs",
    "outputs",
    "feedback_stats",
)
ARCHIVE_JSON_LIST_COLUMNS = (
    "events",
    "tags",
    "parent_run_ids",
)
ARCHIVE_JSON_COLUMNS = ARCHIVE_JSON_OBJECT_COLUMNS + ARCHIVE_JSON_LIST_COLUMNS


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
        schema_version=1,
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

_READ_JSON_SOURCE = "read_json_auto({path}, maximum_object_size=104857600)"


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
    line = json.dumps(payload, ensure_ascii=False, default=_archive_json_default)
    return line.encode("utf-8") + b"\n"


def _combine_parquet_parts(part_paths: list[Path], target: Path) -> None:
    """
    Concatenate Parquet parts into one file, one row group at a time.

    Pieces are inferred independently, so a column that is all-null in one piece may
    carry a narrower type there; the unified schema promotes such columns and every
    row group is cast to it before writing. Working memory is bounded by one decoded
    row group (parts are written with byte-bounded row groups), not by the day.
    """
    import pyarrow
    import pyarrow.parquet

    part_files = [pyarrow.parquet.ParquetFile(str(part)) for part in part_paths]
    unified = pyarrow.unify_schemas(
        [part.schema_arrow for part in part_files], promote_options="permissive"
    )
    writer = pyarrow.parquet.ParquetWriter(str(target), unified, compression="zstd")
    try:
        for part in part_files:
            for group_index in range(part.num_row_groups):
                table = part.read_row_group(group_index)
                writer.write_table(table.select(unified.names).cast(unified))
    finally:
        writer.close()
        for part in part_files:
            part.close()


def _write_runs_parquet(
    client: RunsExportClient, manifest: ArchiveManifest, target: Path
) -> int:
    filter_ = f'lt(start_time, "{manifest.window_end.isoformat()}")'
    piece_paths: list[Path] = []
    part_paths: list[Path] = []
    run_count = 0
    piece = None
    piece_bytes = 0
    try:
        for run in client.list_runs(
            project_id=manifest.project_id,
            start_time=manifest.window_start,
            filter=filter_,
            limit=None,
        ):
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
                piece_bytes = 0
            piece.write(encoded)
            piece_bytes += len(encoded)
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
                source = _READ_JSON_SOURCE.format(path=_sql_string(str(piece_paths[0])))
                connection.execute(
                    f"COPY (SELECT * FROM {source}) "
                    f"TO {_sql_string(str(target))} " + ARCHIVE_PARQUET_COPY_OPTIONS
                )
            else:
                for piece_path in piece_paths:
                    part_path = piece_path.with_suffix(".part")
                    source = _READ_JSON_SOURCE.format(path=_sql_string(str(piece_path)))
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


def _canonicalize(store: ArchiveStore, manifest: ArchiveManifest, target: Path) -> int:
    sources: list[tuple[str, int]] = []
    if manifest.primary is not None:
        sources.append((store.object_uri(manifest.primary.raw_key), 1))
    if manifest.reconciliation is not None:
        sources.append((store.object_uri(manifest.reconciliation.raw_key), 2))
    if not sources:
        raise ValueError("Cannot canonicalize a manifest without snapshots")

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
            if json_columns:
                excluded = ", ".join(json_columns)
                normalized = ", ".join(
                    f"CASE WHEN typeof({column}) = 'VARCHAR' "
                    f"THEN CAST({column} AS VARCHAR) "
                    f"ELSE CAST(to_json({column}) AS VARCHAR) END AS {column}"
                    for column in json_columns
                )
                selects.append(
                    f"SELECT * EXCLUDE ({excluded}), {normalized} FROM {view}"
                )
            else:
                selects.append(f"SELECT * FROM {view}")

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
