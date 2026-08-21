"""Managed LangSmith Bulk Export contracts and archive integration."""

from __future__ import annotations

from datetime import date, datetime, timezone
import json
from pathlib import Path
import time
from typing import Any, Callable, Iterator, cast

import pytest
from conftest import create_run
from langsmith.schemas import Run
from langsmith_cli.archive.backfill import import_backfill_snapshot
from langsmith_cli.archive.bulk import (
    BULK_EXPORT_FIELDS,
    BulkExportFailedError,
    BulkExportJob,
    BulkExportPartition,
    BulkExportSnapshot,
    BulkExportStatus,
    BulkExportTimeoutError,
    JsonRequest,
    LangSmithBulkExporter,
)
from langsmith_cli.archive.models import ArchivePhase
from langsmith_cli.archive.query import ArchiveRunQuery, query_archive_runs
from langsmith_cli.archive.query import _normalize_run_payload
from langsmith_cli.archive.storage import create_store
from langsmith_cli.archive.sync import sync_project_day


PROJECT_ID = "f47ac10b-58cc-4372-a567-0e02b2c3d479"
DESTINATION_ID = "22345678-1234-5678-1234-567812345678"
PRIMARY_EXPORT_ID = "32345678-1234-5678-1234-567812345678"
NEW_EXPORT_ID = "42345678-1234-5678-1234-567812345678"
TRACE_START = datetime(2026, 8, 19, tzinfo=timezone.utc)
TRACE_END = datetime(2026, 8, 20, tzinfo=timezone.utc)


def test_bulk_json_strings_normalize_to_run_contracts() -> None:
    payload = _normalize_run_payload(
        {
            "inputs": '{"prompt":"hello"}',
            "outputs": '{"answer":"world"}',
            "extra": '{"metadata":{"model":"gpt"}}',
            "events": '[{"name":"start"}]',
            "tags": '["production"]',
            "parent_run_ids": "[]",
            "feedback_stats": '{"quality":{"avg":1}}',
            "error": "plain error string",
        }
    )
    assert payload["inputs"] == {"prompt": "hello"}
    assert payload["events"] == [{"name": "start"}]
    assert payload["tags"] == ["production"]
    assert payload["error"] == "plain error string"


class FakeBulkExporter:
    def __init__(
        self, source: Path, export_id: str = NEW_EXPORT_ID, run_count: int = 1
    ) -> None:
        self.source = source
        self.export_id = export_id
        self.run_count = run_count
        self.excluded_export_ids: frozenset[str] | None = None

    def export_window(
        self,
        *,
        project_id: str,
        start_time: datetime,
        end_time: datetime,
        excluded_export_ids: frozenset[str],
    ) -> BulkExportSnapshot:
        self.excluded_export_ids = excluded_export_ids
        return BulkExportSnapshot(
            export_id=self.export_id,
            start_time=start_time,
            end_time=end_time,
            run_count=self.run_count,
            file_uris=(str(self.source),),
        )


class SingleRunClient:
    def __init__(self, run: Run) -> None:
        self.run = run

    def list_runs(self, **kwargs: Any) -> Iterator[Run]:
        return iter([self.run])


def _job_payload(export_id: str, status: str, created_at: str) -> dict[str, object]:
    return {
        "bulk_export_destination_id": DESTINATION_ID,
        "session_id": PROJECT_ID,
        "all_experiments": False,
        "start_time": TRACE_START.isoformat(),
        "end_time": TRACE_END.isoformat(),
        "filter": None,
        "format": "Parquet",
        "format_version": "v2_beta",
        "compression": "zstandard",
        "interval_hours": None,
        "export_fields": list(BULK_EXPORT_FIELDS),
        "id": export_id,
        "tenant_id": "52345678-1234-5678-1234-567812345678",
        "status": status,
        "created_at": created_at,
        "updated_at": created_at,
        "finished_at": created_at if status == "Completed" else None,
        "source_bulk_export_id": None,
    }


def _job(status: BulkExportStatus) -> BulkExportJob:
    return BulkExportJob(
        export_id=NEW_EXPORT_ID,
        destination_id=DESTINATION_ID,
        project_id=PROJECT_ID,
        start_time=TRACE_START,
        end_time=TRACE_END,
        status=status,
        created_at=TRACE_START,
        format_version="v2_beta",
        compression="zstandard",
        interval_hours=None,
        filter=None,
        export_fields=BULK_EXPORT_FIELDS,
        all_experiments=False,
    )


def _destination_request(
    method: str,
    path: str,
    params: dict[str, object] | None,
    payload: dict[str, object] | None,
) -> object:
    assert method == "GET"
    assert path == f"/api/v1/bulk-exports/destinations/{DESTINATION_ID}"
    return {
        "id": DESTINATION_ID,
        "config": {
            "bucket_name": "traces-dev",
            "prefix": "langsmith/bulk",
            "region": "us-east-1",
        },
    }


def _exporter(
    *,
    request_json: JsonRequest = _destination_request,
    timeout_seconds: float = 5 * 60 * 60,
    monotonic: Callable[[], float] = time.monotonic,
) -> LangSmithBulkExporter:
    return LangSmithBulkExporter(
        api_url="https://api.smith.langchain.com",
        api_key="test-key",
        workspace_id=None,
        destination_id=DESTINATION_ID,
        archive_uri="s3://traces-dev/langsmith",
        request_json=request_json,
        poll_interval_seconds=0,
        timeout_seconds=timeout_seconds,
        monotonic=monotonic,
    )


def test_bulk_export_fails_fast_on_terminal_failure() -> None:
    with pytest.raises(BulkExportFailedError, match="ended as Failed"):
        _exporter().complete_export(_job(BulkExportStatus.FAILED))


def test_bulk_export_wait_is_bounded() -> None:
    clock = iter((0.0, 2.0))
    exporter = _exporter(timeout_seconds=1, monotonic=lambda: next(clock))
    with pytest.raises(BulkExportTimeoutError, match="did not finish in time"):
        exporter.complete_export(_job(BulkExportStatus.CREATED))


def test_bulk_export_rejects_destination_outside_archive() -> None:
    def wrong_destination(
        method: str,
        path: str,
        params: dict[str, object] | None,
        payload: dict[str, object] | None,
    ) -> object:
        response = cast(
            dict[str, object], _destination_request(method, path, params, payload)
        )
        response["config"] = {
            "bucket_name": "traces-prd",
            "prefix": "langsmith/bulk",
        }
        return response

    with pytest.raises(ValueError, match="bucket does not match"):
        _exporter(request_json=wrong_destination).begin_window(
            project_id=PROJECT_ID,
            start_time=TRACE_START,
            end_time=TRACE_END,
            excluded_export_ids=frozenset(),
        )


def test_bulk_export_adopts_latest_matching_job_and_excludes_prior_phase() -> None:
    requests: list[tuple[str, str]] = []

    def request(
        method: str,
        path: str,
        params: dict[str, object] | None,
        payload: dict[str, object] | None,
    ) -> object:
        requests.append((method, path))
        if path == f"/api/v1/bulk-exports/destinations/{DESTINATION_ID}":
            return {
                "id": DESTINATION_ID,
                "config": {
                    "bucket_name": "traces-dev",
                    "prefix": "langsmith/bulk",
                    "region": "us-east-1",
                },
            }
        if path == "/api/v1/bulk-exports":
            return [
                _job_payload(
                    PRIMARY_EXPORT_ID, "Completed", "2026-08-20T01:00:00+00:00"
                ),
                _job_payload(NEW_EXPORT_ID, "Completed", "2026-08-21T01:00:00+00:00"),
            ]
        if path == f"/api/v1/bulk-exports/{NEW_EXPORT_ID}":
            return _job_payload(NEW_EXPORT_ID, "Completed", "2026-08-21T01:00:00+00:00")
        if path == f"/api/v1/bulk-exports/{NEW_EXPORT_ID}/runs":
            return [
                {
                    "bulk_export_id": NEW_EXPORT_ID,
                    "session_id": PROJECT_ID,
                    "id": "62345678-1234-5678-1234-567812345678",
                    "status": "Completed",
                    "retry_number": 0,
                    "errors": None,
                    "created_at": "2026-08-21T01:00:00+00:00",
                    "updated_at": "2026-08-21T01:01:00+00:00",
                    "finished_at": "2026-08-21T01:01:00+00:00",
                    "start_time": TRACE_START.isoformat(),
                    "end_time": TRACE_END.isoformat(),
                    "metadata": {
                        "prefix": "langsmith/bulk",
                        "start_time": TRACE_START.isoformat(),
                        "end_time": TRACE_END.isoformat(),
                        "execution_backend": "smithdb",
                        "result": {
                            "rows_written": 7,
                            "exported_files": [
                                "traces-dev/langsmith/bulk/export_id="
                                f"{NEW_EXPORT_ID}/tenant_id=t/session_id={PROJECT_ID}/"
                                "resource=runs/year=2026/month=8/day=19/runs.parquet"
                            ],
                            "export_path": "langsmith/bulk/export",
                            "latest_cursor": "cursor",
                            "pending_upload": None,
                        },
                    },
                }
            ]
        raise AssertionError((method, path, params, payload))

    exporter = LangSmithBulkExporter(
        api_url="https://api.smith.langchain.com",
        api_key="test-key",
        workspace_id=None,
        destination_id=DESTINATION_ID,
        archive_uri="s3://traces-dev/langsmith",
        request_json=request,
        poll_interval_seconds=0,
    )
    snapshot = exporter.export_window(
        project_id=PROJECT_ID,
        start_time=TRACE_START,
        end_time=TRACE_END,
        excluded_export_ids=frozenset({PRIMARY_EXPORT_ID}),
    )

    assert snapshot.export_id == NEW_EXPORT_ID
    assert snapshot.run_count == 7
    assert snapshot.file_uris == (
        "s3://traces-dev/langsmith/bulk/export_id="
        f"{NEW_EXPORT_ID}/tenant_id=t/session_id={PROJECT_ID}/"
        "resource=runs/year=2026/month=8/day=19/runs.parquet",
    )
    assert ("POST", "/api/v1/bulk-exports") not in requests


def test_bulk_snapshot_integrates_with_canonical_reconciliation(
    tmp_path: Path, monkeypatch
) -> None:
    import duckdb

    run = create_run(outputs={"source": "bulk"}, tags=["bulk-provider"])
    source = tmp_path / "bulk.parquet"
    rows = tmp_path / "bulk.json"
    payload = run.model_dump(mode="json")
    for field in (
        "extra",
        "inputs",
        "outputs",
        "feedback_stats",
        "events",
        "tags",
        "parent_run_ids",
    ):
        if field in payload and payload[field] is not None:
            payload[field] = json.dumps(payload[field])
    rows.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    connection = duckdb.connect()
    try:
        source_sql = str(source).replace("'", "''")
        connection.execute(
            f"COPY (SELECT * FROM read_json_auto(?)) TO '{source_sql}' "
            "(FORMAT PARQUET)",
            [str(rows)],
        )
    finally:
        connection.close()

    archive_uri = str(tmp_path / "archive")
    monkeypatch.setenv("LANGSMITH_ARCHIVE_URI", archive_uri)
    exporter = FakeBulkExporter(source)
    manifest = sync_project_day(
        None,
        create_store(archive_uri),
        project_id=PROJECT_ID,
        project_name="dev/agent",
        trace_date=date(2026, 8, 19),
        phase=ArchivePhase.RECONCILIATION,
        bulk_exporter=exporter,
    )

    assert manifest.reconciliation is not None
    assert manifest.reconciliation.generation_id == NEW_EXPORT_ID
    assert manifest.canonical_run_count == 1
    assert exporter.excluded_export_ids == frozenset()
    archived = query_archive_runs(ArchiveRunQuery(project="dev/agent", limit=0))
    assert archived[0].outputs == {"source": "bulk"}
    tagged = query_archive_runs(
        ArchiveRunQuery(project="dev/agent", tags=("bulk-provider",), limit=0)
    )
    assert len(tagged) == 1


def test_bulk_reconciliation_unifies_runs_api_and_v2_json_column_types(
    tmp_path: Path, monkeypatch
) -> None:
    """A Bulk v2 reconciliation must replace native Runs API rows by run ID."""
    import duckdb

    primary_run = create_run(outputs={"version": "runs-api"}, tags=["primary"])
    reconciled_run = primary_run.model_copy(
        update={"outputs": {"version": "bulk-v2"}, "tags": ["reconciled"]}
    )
    late_child = create_run(
        id_str="62345678-1234-5678-1234-567812345678",
        parent_run_id=str(primary_run.id),
        trace_id=str(primary_run.id),
        outputs={"late": True},
    )
    json_rows = tmp_path / "bulk-mixed.json"
    payloads: list[dict[str, object]] = []
    for run in (reconciled_run, late_child):
        payload = run.model_dump(mode="json")
        for field in (
            "extra",
            "inputs",
            "outputs",
            "feedback_stats",
            "events",
            "tags",
            "parent_run_ids",
        ):
            if field in payload and payload[field] is not None:
                payload[field] = json.dumps(payload[field])
        payloads.append(payload)
    json_rows.write_text(
        "".join(json.dumps(payload) + "\n" for payload in payloads),
        encoding="utf-8",
    )
    bulk_parquet = tmp_path / "bulk-mixed.parquet"
    connection = duckdb.connect()
    try:
        bulk_parquet_sql = str(bulk_parquet).replace("'", "''")
        connection.execute(
            f"COPY (SELECT * FROM read_json_auto(?)) TO '{bulk_parquet_sql}' "
            "(FORMAT PARQUET)",
            [str(json_rows)],
        )
    finally:
        connection.close()

    archive_uri = str(tmp_path / "archive")
    monkeypatch.setenv("LANGSMITH_ARCHIVE_URI", archive_uri)
    store = create_store(archive_uri)
    sync_project_day(
        SingleRunClient(primary_run),
        store,
        project_id=PROJECT_ID,
        project_name="dev/agent",
        trace_date=date(2026, 8, 19),
        phase=ArchivePhase.PRIMARY,
    )
    exporter = FakeBulkExporter(bulk_parquet, run_count=2)
    manifest = sync_project_day(
        None,
        store,
        project_id=PROJECT_ID,
        project_name="dev/agent",
        trace_date=date(2026, 8, 19),
        phase=ArchivePhase.RECONCILIATION,
        bulk_exporter=exporter,
    )

    assert manifest.canonical_run_count == 2
    archived = query_archive_runs(ArchiveRunQuery(project="dev/agent", limit=0))
    assert {str(run.id) for run in archived} == {
        str(primary_run.id),
        str(late_child.id),
    }
    root = next(run for run in archived if run.id == primary_run.id)
    assert root.outputs == {"version": "bulk-v2"}
    assert root.tags == ["reconciled"]


def test_range_backfill_publishes_daily_partitions_and_resumes(
    tmp_path: Path, monkeypatch
) -> None:
    import duckdb

    files: list[Path] = []
    partitions: list[BulkExportPartition] = []
    for offset, day in enumerate((19, 20)):
        run = create_run(
            id_str=f"{offset + 1}2345678-1234-5678-1234-567812345678",
            outputs={"day": day},
        )
        rows = tmp_path / f"day-{day}.json"
        parquet = tmp_path / f"day-{day}.parquet"
        rows.write_text(run.model_dump_json() + "\n", encoding="utf-8")
        connection = duckdb.connect()
        try:
            parquet_sql = str(parquet).replace("'", "''")
            connection.execute(
                f"COPY (SELECT * FROM read_json_auto(?)) TO '{parquet_sql}' "
                "(FORMAT PARQUET)",
                [str(rows)],
            )
        finally:
            connection.close()
        files.append(parquet)
        start = datetime(2026, 8, day, tzinfo=timezone.utc)
        partitions.append(
            BulkExportPartition(
                start_time=start,
                end_time=start.replace(day=day + 1),
                run_count=1,
                file_uris=(str(parquet),),
            )
        )

    snapshot = BulkExportSnapshot(
        export_id=NEW_EXPORT_ID,
        start_time=datetime(2026, 8, 19, tzinfo=timezone.utc),
        end_time=datetime(2026, 8, 21, tzinfo=timezone.utc),
        run_count=2,
        file_uris=tuple(str(path) for path in files),
        partitions=tuple(partitions),
    )
    archive_uri = str(tmp_path / "archive")
    monkeypatch.setenv("LANGSMITH_ARCHIVE_URI", archive_uri)
    store = create_store(archive_uri)

    first = import_backfill_snapshot(
        store,
        project_id=PROJECT_ID,
        project_name="dev/agent",
        snapshot=snapshot,
    )
    second = import_backfill_snapshot(
        store,
        project_id=PROJECT_ID,
        project_name="dev/agent",
        snapshot=snapshot,
    )

    assert first.imported_days == 2
    assert first.skipped_days == 0
    assert first.canonical_run_count == 2
    assert second.imported_days == 0
    assert second.skipped_days == 2
    archived = query_archive_runs(ArchiveRunQuery(project="dev/agent", limit=0))
    assert {cast(dict[str, object], run.outputs)["day"] for run in archived} == {19, 20}
