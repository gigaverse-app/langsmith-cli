"""Managed LangSmith Bulk Export contracts and archive integration."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timezone
import json
from pathlib import Path
import time
from typing import Any, Callable, Iterator, cast

import pytest
from conftest import create_run, parse_json_output
from langsmith import Client
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
from langsmith_cli.archive.query import (
    ArchiveRunQuery,
    _validated_archive_run,
    query_archive_runs,
    read_archived_run,
)
from langsmith_cli.archive.query import _normalize_run_payload
from langsmith_cli.archive.storage import create_store
from langsmith_cli.archive.sync import sync_project_day
from langsmith_cli.main import cli


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


def test_bulk_json_normalization_matches_live_run_shape() -> None:
    """INVARIANT: provider representation does not change CLI run JSON."""
    payload = _normalize_run_payload(
        {
            "inputs": json.dumps(
                {
                    "input": json.dumps({"question": "hello"}),
                    "schema_padding": None,
                }
            ),
            "outputs": json.dumps({"answer": "world", "schema_padding": None}),
            "extra": json.dumps({"metadata": {"model": "gpt", "schema_padding": None}}),
            "events": json.dumps(
                [
                    {
                        "name": "start",
                        "time": "2026-08-19T21:59:53.948395",
                        "schema_padding": None,
                    }
                ]
            ),
        }
    )

    assert payload == {
        "inputs": {"input": {"question": "hello"}, "schema_padding": None},
        "outputs": {"answer": "world", "schema_padding": None},
        "extra": {"metadata": {"model": "gpt", "schema_padding": None}},
        "events": [
            {
                "name": "start",
                "time": "2026-08-19T21:59:53.948395+00:00",
                "schema_padding": None,
            }
        ],
    }


def test_bulk_json_normalization_preserves_non_json_values_and_normalizes_datetimes() -> (
    None
):
    payload = _normalize_run_payload(
        {
            "inputs": json.dumps({"input": "plain text"}),
            "events": json.dumps([{"name": "custom", "time": "not-an-iso-time"}]),
        }
    )
    assert payload == {
        "inputs": {"input": "plain text"},
        "events": [{"name": "custom", "time": "not-an-iso-time"}],
    }

    run_payload = create_run().model_dump(mode="python")
    run_payload["events"] = [
        {"name": "start", "time": datetime(2026, 8, 19, 21, 59, 53, 948395)},
        {"name": "custom", "time": "not-an-iso-time"},
    ]
    run = _validated_archive_run(run_payload)
    assert run.model_dump(mode="json")["events"] == [
        {"name": "start", "time": "2026-08-19T21:59:53.948395+00:00"},
        {"name": "custom", "time": "not-an-iso-time"},
    ]


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


def test_bulk_export_batch_harvests_completed_jobs_without_head_of_line_blocking() -> (
    None
):
    queued_export_id = "82345678-1234-5678-1234-567812345678"
    ready_export_id = "92345678-1234-5678-1234-567812345678"
    queued_polls = 0

    def request(
        method: str,
        path: str,
        params: dict[str, object] | None,
        payload: dict[str, object] | None,
    ) -> object:
        nonlocal queued_polls
        if path.startswith("/api/v1/bulk-exports/destinations/"):
            return _destination_request(method, path, params, payload)
        if path == f"/api/v1/bulk-exports/{queued_export_id}":
            queued_polls += 1
            status = "Created" if queued_polls == 1 else "Completed"
            return _job_payload(queued_export_id, status, TRACE_START.isoformat())
        if path == f"/api/v1/bulk-exports/{ready_export_id}/runs":
            return [_partition_payload(rows_written=0, exported_files=[])]
        if path == f"/api/v1/bulk-exports/{queued_export_id}/runs":
            return [_partition_payload(rows_written=0, exported_files=[])]
        raise AssertionError((method, path))

    queued = replace(_job(BulkExportStatus.CREATED), export_id=queued_export_id)
    ready = replace(_job(BulkExportStatus.COMPLETED), export_id=ready_export_id)

    completed = list(_exporter(request_json=request).complete_exports((queued, ready)))

    assert [snapshot.export_id for snapshot in completed] == [
        ready_export_id,
        queued_export_id,
    ]


def test_bulk_export_batch_harvests_ready_peer_before_reporting_failure() -> None:
    failed = replace(
        _job(BulkExportStatus.FAILED),
        export_id="a2345678-1234-5678-1234-567812345678",
    )
    ready = replace(
        _job(BulkExportStatus.COMPLETED),
        export_id="b2345678-1234-5678-1234-567812345678",
    )

    def request(
        method: str,
        path: str,
        params: dict[str, object] | None,
        payload: dict[str, object] | None,
    ) -> object:
        if path.startswith("/api/v1/bulk-exports/destinations/"):
            return _destination_request(method, path, params, payload)
        if path == f"/api/v1/bulk-exports/{ready.export_id}/runs":
            return [_partition_payload(rows_written=0, exported_files=[])]
        raise AssertionError((method, path))

    snapshots = _exporter(request_json=request).complete_exports((failed, ready))

    assert next(snapshots).export_id == ready.export_id
    with pytest.raises(
        BulkExportFailedError, match=f"{failed.export_id} ended as Failed"
    ):
        next(snapshots)


def test_bulk_export_batch_rejects_duplicate_export_ids() -> None:
    duplicate = _job(BulkExportStatus.COMPLETED)

    with pytest.raises(ValueError, match="Duplicate bulk export ID"):
        list(_exporter().complete_exports((duplicate, duplicate)))


def test_bulk_export_empty_batch_performs_no_destination_request() -> None:
    def unexpected_request(
        method: str,
        path: str,
        params: dict[str, object] | None,
        payload: dict[str, object] | None,
    ) -> object:
        raise AssertionError((method, path))

    assert list(_exporter(request_json=unexpected_request).complete_exports(())) == []


def test_bulk_export_batch_rejects_polled_request_identity_drift() -> None:
    queued = _job(BulkExportStatus.CREATED)

    def request(
        method: str,
        path: str,
        params: dict[str, object] | None,
        payload: dict[str, object] | None,
    ) -> object:
        if path.startswith("/api/v1/bulk-exports/destinations/"):
            return _destination_request(method, path, params, payload)
        changed = _job_payload(queued.export_id, "Running", TRACE_START.isoformat())
        changed["session_id"] = "c2345678-1234-5678-1234-567812345678"
        return changed

    with pytest.raises(ValueError, match="changed request identity"):
        list(_exporter(request_json=request).complete_exports((queued,)))


@pytest.mark.parametrize(
    ("destination_override", "expected_error"),
    [
        ({"id": "62345678-1234-5678-1234-567812345678"}, "changed identity"),
        (
            {"config": {"bucket_name": "traces-dev", "prefix": "elsewhere/bulk"}},
            "prefix is outside archive URI",
        ),
        (
            {"config": {"bucket_name": "traces-prd", "prefix": "langsmith/bulk"}},
            "bucket does not match",
        ),
    ],
)
def test_bulk_export_rejects_destination_outside_archive(
    destination_override: dict[str, object], expected_error: str
) -> None:
    def wrong_destination(
        method: str,
        path: str,
        params: dict[str, object] | None,
        payload: dict[str, object] | None,
    ) -> object:
        response = cast(
            dict[str, object], _destination_request(method, path, params, payload)
        )
        response.update(destination_override)
        return response

    with pytest.raises(ValueError, match=expected_error):
        _exporter(request_json=wrong_destination).begin_window(
            project_id=PROJECT_ID,
            start_time=TRACE_START,
            end_time=TRACE_END,
            excluded_export_ids=frozenset(),
        )


def test_bulk_export_creates_exact_v2_job_when_none_can_be_adopted() -> None:
    created_payload: dict[str, object] = {}

    def request(
        method: str,
        path: str,
        params: dict[str, object] | None,
        payload: dict[str, object] | None,
    ) -> object:
        if path.startswith("/api/v1/bulk-exports/destinations/"):
            return _destination_request(method, path, params, payload)
        if method == "GET" and path == "/api/v1/bulk-exports":
            assert params == {"limit": 1000, "offset": 0}
            return []
        if method == "POST" and path == "/api/v1/bulk-exports":
            assert payload is not None
            created_payload.update(payload)
            return _job_payload(NEW_EXPORT_ID, "Created", TRACE_START.isoformat())
        raise AssertionError((method, path))

    job = _exporter(request_json=request).begin_window(
        project_id=PROJECT_ID,
        start_time=TRACE_START,
        end_time=TRACE_END,
        excluded_export_ids=frozenset(),
    )

    assert job.export_id == NEW_EXPORT_ID
    assert created_payload == {
        "bulk_export_destination_id": DESTINATION_ID,
        "session_id": PROJECT_ID,
        "start_time": TRACE_START.isoformat(),
        "end_time": TRACE_END.isoformat(),
        "format_version": "v2_beta",
        "compression": "zstandard",
        "export_fields": list(BULK_EXPORT_FIELDS),
    }


def test_bulk_export_http_adapter_sends_workspace_headers_and_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    class FakeResponse:
        def __init__(self, payload: object) -> None:
            self.payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> object:
            return self.payload

    def request(method: str, url: str, **kwargs: object) -> FakeResponse:
        calls.append({"method": method, "url": url, **kwargs})
        if url.endswith(f"/destinations/{DESTINATION_ID}"):
            return FakeResponse(
                {
                    "id": DESTINATION_ID,
                    "config": {
                        "bucket_name": "traces-dev",
                        "prefix": "langsmith/bulk",
                    },
                }
            )
        if method == "GET":
            return FakeResponse([])
        return FakeResponse(
            _job_payload(NEW_EXPORT_ID, "Created", TRACE_START.isoformat())
        )

    monkeypatch.setattr("httpx.request", request)
    exporter = LangSmithBulkExporter(
        api_url="https://self-hosted.example/api",
        api_key="test-key",
        workspace_id="workspace-id",
        destination_id=DESTINATION_ID,
        archive_uri="s3://traces-dev/langsmith",
    )
    exporter.begin_window(
        project_id=PROJECT_ID,
        start_time=TRACE_START,
        end_time=TRACE_END,
        excluded_export_ids=frozenset(),
    )

    assert [call["method"] for call in calls] == ["GET", "GET", "POST"]
    assert all(
        call["headers"] == {"X-API-Key": "test-key", "X-Tenant-Id": "workspace-id"}
        for call in calls
    )
    assert calls[1]["params"] == {"limit": "1000", "offset": "0"}
    assert calls[2]["json"] is not None
    assert all(
        str(call["url"]).startswith("https://self-hosted.example/api/v1")
        for call in calls
    )


@pytest.mark.parametrize(
    ("api_key", "poll_interval_seconds", "timeout_seconds", "message"),
    (
        ("", 10, 5 * 60 * 60, "API key must not be empty"),
        ("test-key", -1, 5 * 60 * 60, "poll interval must be non-negative"),
        ("test-key", 10, 0, "timeout must be positive"),
    ),
)
def test_bulk_export_rejects_invalid_operator_configuration(
    api_key: str,
    poll_interval_seconds: float,
    timeout_seconds: float,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        LangSmithBulkExporter(
            api_url="https://api.smith.langchain.com",
            api_key=api_key,
            workspace_id=None,
            destination_id=DESTINATION_ID,
            archive_uri="s3://traces-dev/langsmith",
            poll_interval_seconds=poll_interval_seconds,
            timeout_seconds=timeout_seconds,
        )


def test_bulk_export_builds_from_langsmith_client() -> None:
    client = Client(
        api_url="https://api.smith.langchain.com",
        api_key="test-key",
    )
    exporter = LangSmithBulkExporter.from_langsmith_client(
        client,
        destination_id=DESTINATION_ID,
        archive_uri="s3://traces-dev/langsmith",
    )
    assert isinstance(exporter, LangSmithBulkExporter)
    with pytest.raises(TypeError, match="requires a LangSmith Client"):
        LangSmithBulkExporter.from_langsmith_client(
            object(),
            destination_id=DESTINATION_ID,
            archive_uri="s3://traces-dev/langsmith",
        )


@pytest.mark.parametrize(
    ("api_url", "destination_id", "archive_uri", "message"),
    (
        (
            "ftp://api.smith.langchain.com",
            DESTINATION_ID,
            "s3://traces-dev/langsmith",
            "must use HTTP or HTTPS",
        ),
        (
            "https://api.smith.langchain.com",
            "{" + PROJECT_ID + "}",
            "s3://traces-dev/langsmith",
            "canonical UUID format",
        ),
        (
            "https://api.smith.langchain.com",
            DESTINATION_ID,
            "https://traces-dev/langsmith",
            "requires an s3:// archive URI",
        ),
        (
            "https://api.smith.langchain.com",
            DESTINATION_ID,
            "s3://traces-dev",
            "requires a bucket prefix",
        ),
        (
            "https://api.smith.langchain.com",
            DESTINATION_ID,
            "s3://traces-dev/langsmith/../private",
            "prefix must be normalized",
        ),
    ),
)
def test_bulk_export_rejects_unsafe_identity_and_storage_configuration(
    api_url: str, destination_id: str, archive_uri: str, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        LangSmithBulkExporter(
            api_url=api_url,
            api_key="test-key",
            workspace_id=None,
            destination_id=destination_id,
            archive_uri=archive_uri,
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("end_time", 1, "end_time must be a string"),
        ("interval_hours", "1", "interval_hours must be an integer or null"),
        ("filter", 1, "filter must be a string or null"),
        ("all_experiments", "false", "all_experiments must be a boolean"),
    ),
)
def test_bulk_export_rejects_malformed_adoption_jobs(
    field: str, value: object, message: str
) -> None:
    malformed = _job_payload(NEW_EXPORT_ID, "Created", TRACE_START.isoformat())
    malformed[field] = value

    def request(
        method: str,
        path: str,
        params: dict[str, object] | None,
        payload: dict[str, object] | None,
    ) -> object:
        if path.startswith("/api/v1/bulk-exports/destinations/"):
            return _destination_request(method, path, params, payload)
        return [malformed]

    with pytest.raises(ValueError, match=message):
        _exporter(request_json=request).begin_window(
            project_id=PROJECT_ID,
            start_time=TRACE_START,
            end_time=TRACE_END,
            excluded_export_ids=frozenset(),
        )


def _partition_payload(
    *, rows_written: int, exported_files: list[str], status: str = "Completed"
) -> dict[str, object]:
    return {
        "id": "62345678-1234-5678-1234-567812345678",
        "status": status,
        "metadata": {
            "start_time": TRACE_START.isoformat(),
            "end_time": TRACE_END.isoformat(),
            "result": {
                "rows_written": rows_written,
                "exported_files": exported_files,
            },
        },
    }


def test_bulk_export_polls_then_accepts_complete_empty_partition() -> None:
    polled: list[str] = []

    def request(
        method: str,
        path: str,
        params: dict[str, object] | None,
        payload: dict[str, object] | None,
    ) -> object:
        if path.startswith("/api/v1/bulk-exports/destinations/"):
            return _destination_request(method, path, params, payload)
        if path == f"/api/v1/bulk-exports/{NEW_EXPORT_ID}":
            polled.append(path)
            return _job_payload(NEW_EXPORT_ID, "Completed", TRACE_START.isoformat())
        if path == f"/api/v1/bulk-exports/{NEW_EXPORT_ID}/runs":
            return [_partition_payload(rows_written=0, exported_files=[])]
        raise AssertionError((method, path))

    snapshot = _exporter(request_json=request).complete_export(
        _job(BulkExportStatus.CREATED)
    )

    assert polled == [f"/api/v1/bulk-exports/{NEW_EXPORT_ID}"]
    assert snapshot.run_count == 0
    assert snapshot.file_uris == ()
    assert len(snapshot.partitions) == 1


@pytest.mark.parametrize(
    ("partition", "message"),
    (
        (
            _partition_payload(rows_written=1, exported_files=[]),
            "did not publish Parquet files",
        ),
        (
            _partition_payload(
                rows_written=1,
                exported_files=["traces-prd/langsmith/bulk/out.parquet"],
            ),
            "outside the configured destination",
        ),
        (
            _partition_payload(
                rows_written=1,
                exported_files=["traces-dev/langsmith/bulk/./out.parquet"],
            ),
            "path must be normalized",
        ),
        (
            _partition_payload(rows_written=0, exported_files=[], status="Failed"),
            "partition .* is Failed",
        ),
    ),
)
def test_bulk_export_rejects_invalid_completed_partitions(
    partition: dict[str, object], message: str
) -> None:
    def request(
        method: str,
        path: str,
        params: dict[str, object] | None,
        payload: dict[str, object] | None,
    ) -> object:
        if path.startswith("/api/v1/bulk-exports/destinations/"):
            return _destination_request(method, path, params, payload)
        if path == f"/api/v1/bulk-exports/{NEW_EXPORT_ID}/runs":
            return [partition]
        raise AssertionError((method, path))

    error = BulkExportFailedError if partition["status"] == "Failed" else ValueError
    with pytest.raises(error, match=message):
        _exporter(request_json=request).complete_export(
            _job(BulkExportStatus.COMPLETED)
        )


@pytest.mark.parametrize(
    ("result", "message"),
    (
        (None, "partition has no result"),
        ({"rows_written": -1, "exported_files": []}, "must be non-negative"),
        (
            {"rows_written": 1, "exported_files": [123]},
            "file path must be a string",
        ),
    ),
)
def test_bulk_export_rejects_malformed_partition_results(
    result: object, message: str
) -> None:
    partition = _partition_payload(rows_written=0, exported_files=[])
    metadata = cast(dict[str, object], partition["metadata"])
    metadata["result"] = result

    def request(
        method: str,
        path: str,
        params: dict[str, object] | None,
        payload: dict[str, object] | None,
    ) -> object:
        if path.startswith("/api/v1/bulk-exports/destinations/"):
            return _destination_request(method, path, params, payload)
        return [partition]

    with pytest.raises(ValueError, match=message):
        _exporter(request_json=request).complete_export(
            _job(BulkExportStatus.COMPLETED)
        )


def test_bulk_export_rejects_missing_partition_coverage() -> None:
    def request(
        method: str,
        path: str,
        params: dict[str, object] | None,
        payload: dict[str, object] | None,
    ) -> object:
        if path.startswith("/api/v1/bulk-exports/destinations/"):
            return _destination_request(method, path, params, payload)
        return []

    with pytest.raises(ValueError, match="has no partition runs"):
        _exporter(request_json=request).complete_export(
            _job(BulkExportStatus.COMPLETED)
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
    tmp_path: Path, monkeypatch, runner
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
    ).model_copy(
        update={
            "events": [{"name": "start", "time": "2026-08-19T21:59:53.948395+00:00"}]
        }
    )
    json_rows = tmp_path / "bulk-mixed.json"
    payloads: list[dict[str, object]] = []
    for run in (reconciled_run, late_child):
        payload = run.model_dump(mode="json")
        if run.id == late_child.id:
            events = cast(list[dict[str, object]], payload["events"])
            events[0]["time"] = "2026-08-19T21:59:53.948395"
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
    archived_root, archived_children = read_archived_run(
        str(primary_run.id),
        follow_children=True,
        project="dev/agent",
    )
    assert [child.id for child in archived_children] == [late_child.id]
    assert archived_root.child_run_ids == [late_child.id]
    assert archived_children[0].model_dump(mode="json")["events"] == [
        {"name": "start", "time": "2026-08-19T21:59:53.948395+00:00"}
    ]

    class FullTraceClient:
        def read_run(self, run_id: str) -> Run:
            assert run_id == str(primary_run.id)
            return reconciled_run.model_copy(update={"child_run_ids": [late_child.id]})

        def list_runs(self, **kwargs: object) -> Iterator[Run]:
            assert kwargs == {"trace_id": str(primary_run.id)}
            return iter((reconciled_run, late_child))

    from langsmith_cli.commands.runs import get_cmd

    monkeypatch.setattr(get_cmd, "get_or_create_client", lambda ctx: FullTraceClient())
    command = ["--json", "runs", "get", str(primary_run.id), "--follow-children"]
    api_result = runner.invoke(cli, command)
    archive_result = runner.invoke(
        cli, [*command, "--archive", "--project", "dev/agent"]
    )

    assert api_result.exit_code == 0, api_result.output
    assert archive_result.exit_code == 0, archive_result.output
    assert parse_json_output(api_result.output) == parse_json_output(
        archive_result.output
    )


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
