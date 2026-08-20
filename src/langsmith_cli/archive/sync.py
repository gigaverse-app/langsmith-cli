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
    PhaseRecord,
    PhaseStatus,
)
from langsmith_cli.archive.storage import (
    ArchiveStore,
    manifest_key,
    read_manifest,
    write_manifest,
)

if TYPE_CHECKING:
    from langsmith.schemas import Run


class DuckConnection(Protocol):
    def execute(self, query: str, parameters: object | None = None) -> Any: ...


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


def _write_runs_parquet(
    client: RunsExportClient, manifest: ArchiveManifest, target: Path
) -> int:
    import duckdb

    filter_ = f'lt(start_time, "{manifest.window_end.isoformat()}")'
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".jsonl", encoding="utf-8"
    ) as rows:
        run_count = 0
        for run in client.list_runs(
            project_id=manifest.project_id,
            start_time=manifest.window_start,
            filter=filter_,
            limit=None,
        ):
            rows.write(
                json.dumps(
                    run.model_dump(mode="python"),
                    ensure_ascii=False,
                    default=_archive_json_default,
                )
            )
            rows.write("\n")
            run_count += 1
        rows.flush()

        connection = duckdb.connect()
        try:
            if run_count:
                connection.execute(
                    "COPY (SELECT * FROM read_json_auto("
                    f"{_sql_string(rows.name)}, maximum_object_size=104857600)) "
                    f"TO {_sql_string(str(target))} "
                    "(FORMAT PARQUET, COMPRESSION ZSTD)"
                )
            else:
                connection.execute(
                    "COPY (SELECT CAST(NULL AS VARCHAR) AS id WHERE false) "
                    f"TO {_sql_string(str(target))} "
                    "(FORMAT PARQUET, COMPRESSION ZSTD)"
                )
        finally:
            connection.close()
    return run_count


def _configure_s3(connection: DuckConnection, uris: list[str]) -> None:
    if not any(uri.startswith("s3://") for uri in uris):
        return
    # DuckDB loads httpfs on first S3 access. The credential-chain secret delegates
    # authentication to the same workload identity used by boto3.
    connection.execute(
        "CREATE OR REPLACE SECRET langsmith_archive_s3 "
        "(TYPE s3, PROVIDER credential_chain)"
    )


def _canonicalize(store: ArchiveStore, manifest: ArchiveManifest, target: Path) -> int:
    import duckdb

    sources: list[tuple[str, int]] = []
    if manifest.primary is not None:
        sources.append((store.object_uri(manifest.primary.raw_key), 1))
    if manifest.reconciliation is not None:
        sources.append((store.object_uri(manifest.reconciliation.raw_key), 2))
    if not sources:
        raise ValueError("Cannot canonicalize a manifest without snapshots")

    connection = duckdb.connect()
    try:
        _configure_s3(connection, [uri for uri, _ in sources])
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
            "(FORMAT PARQUET, COMPRESSION ZSTD)"
        )
        count_row = connection.execute(f"SELECT count(*) FROM ({query})").fetchone()
        if count_row is None:
            raise ValueError("DuckDB did not return a canonical row count")
        return int(count_row[0])
    finally:
        connection.close()


def sync_project_day(
    client: RunsExportClient,
    store: ArchiveStore,
    *,
    project_id: str,
    project_name: str,
    trace_date: date,
    phase: ArchivePhase,
    existing_manifest: ArchiveManifest | None = None,
    manifest_checked: bool = False,
) -> ArchiveManifest:
    """Export one phase and publish a deduplicated canonical generation."""
    key = manifest_key(project_id, trace_date.isoformat())
    manifest = existing_manifest
    if not manifest_checked:
        manifest = read_manifest(store, key)
    manifest = manifest or _new_manifest(project_id, project_name, trace_date)
    existing = manifest.phase(phase)
    if existing is not None and existing.status is PhaseStatus.VERIFIED:
        return manifest

    generation_id = str(uuid4())
    raw_key = (
        f"raw/project_id={project_id}/date={trace_date.isoformat()}/"
        f"phase={phase.value}/generation={generation_id}/runs.parquet"
    )
    now = datetime.now(timezone.utc)
    with tempfile.TemporaryDirectory(prefix="langsmith-archive-") as directory:
        staging = Path(directory)
        raw_file = staging / "raw.parquet"
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
    write_manifest(store, key, published)
    return published
