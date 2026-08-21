"""LangSmith-managed Bulk Export orchestration."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from datetime import date, datetime, time as datetime_time, timedelta, timezone
from enum import Enum
import os
import time
from typing import Protocol, TypedDict, cast
from urllib.parse import urlparse
from uuid import UUID


class BulkExportStatus(str, Enum):
    CANCELLED = "Cancelled"
    COMPLETED = "Completed"
    CREATED = "Created"
    FAILED = "Failed"
    INTERVAL_SCHEDULED = "IntervalScheduled"
    RUNNING = "Running"
    TIMED_OUT = "TimedOut"


class BulkExportFailedError(RuntimeError):
    """A managed export reached a terminal state without completing."""


class BulkExportTimeoutError(TimeoutError):
    """A managed export did not finish within the operator's wait budget."""


class BulkExportCreateDict(TypedDict):
    bulk_export_destination_id: str
    session_id: str
    start_time: str
    end_time: str
    format_version: str
    compression: str
    export_fields: list[str]


class BulkExportListParams(TypedDict):
    limit: int
    offset: int


JsonRequest = Callable[
    [str, str, dict[str, object] | None, dict[str, object] | None], object
]


BULK_EXPORT_FIELDS = (
    "completion_cost",
    "completion_tokens",
    "dotted_order",
    "end_time",
    "error",
    "events",
    "extra",
    "feedback_stats",
    "first_token_time",
    "id",
    "inputs",
    "is_root",
    "name",
    "outputs",
    "parent_run_id",
    "parent_run_ids",
    "prompt_cost",
    "prompt_tokens",
    "reference_example_id",
    "run_type",
    "session_id",
    "start_time",
    "status",
    "tags",
    "tenant_id",
    "total_cost",
    "total_tokens",
    "trace_id",
    "trace_tier",
)


@dataclass(frozen=True)
class BulkExportJob:
    export_id: str
    destination_id: str
    project_id: str
    start_time: datetime
    end_time: datetime
    status: BulkExportStatus
    created_at: datetime
    format_version: str
    compression: str
    interval_hours: int | None
    filter: str | None
    export_fields: tuple[str, ...] | None
    all_experiments: bool


@dataclass(frozen=True)
class BulkExportSnapshot:
    export_id: str
    start_time: datetime
    end_time: datetime
    run_count: int
    file_uris: tuple[str, ...]
    partitions: tuple[BulkExportPartition, ...] = ()

    def for_utc_date(self, trace_date: date) -> BulkExportSnapshot:
        start = datetime.combine(trace_date, datetime_time.min, tzinfo=timezone.utc)
        end = start + timedelta(days=1)
        if start < self.start_time or end > self.end_time:
            raise ValueError("Bulk export snapshot does not cover requested UTC date")
        partitions = tuple(
            partition
            for partition in self.partitions
            if partition.start_time >= start and partition.end_time <= end
        )
        _validate_partition_coverage(
            [(partition.start_time, partition.end_time) for partition in partitions],
            start,
            end,
        )
        return BulkExportSnapshot(
            export_id=self.export_id,
            start_time=start,
            end_time=end,
            run_count=sum(partition.run_count for partition in partitions),
            file_uris=tuple(
                sorted(uri for partition in partitions for uri in partition.file_uris)
            ),
            partitions=partitions,
        )


@dataclass(frozen=True)
class BulkExportPartition:
    start_time: datetime
    end_time: datetime
    run_count: int
    file_uris: tuple[str, ...]


class BulkWindowExporter(Protocol):
    def export_window(
        self,
        *,
        project_id: str,
        start_time: datetime,
        end_time: datetime,
        excluded_export_ids: frozenset[str],
    ) -> BulkExportSnapshot: ...


@dataclass(frozen=True)
class _S3Destination:
    bucket: str
    prefix: str


class LangSmithBulkExporter:
    """Create/adopt exact-window exports and validate their S3 artifacts."""

    def __init__(
        self,
        *,
        api_url: str,
        api_key: str,
        workspace_id: str | None,
        destination_id: str,
        archive_uri: str,
        request_json: JsonRequest | None = None,
        poll_interval_seconds: float = 10,
        timeout_seconds: float = 5 * 60 * 60,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        _require_uuid(destination_id, "bulk export destination ID")
        if not api_key:
            raise ValueError("LangSmith API key must not be empty")
        if poll_interval_seconds < 0:
            raise ValueError("Bulk export poll interval must be non-negative")
        if timeout_seconds <= 0:
            raise ValueError("Bulk export timeout must be positive")
        self._api_root = _api_root(api_url)
        self._api_key = api_key
        self._workspace_id = workspace_id
        self._destination_id = destination_id
        self._archive_destination = _parse_s3_uri(archive_uri)
        self._request_override = request_json
        self._poll_interval_seconds = poll_interval_seconds
        self._timeout_seconds = timeout_seconds
        self._sleep = sleep
        self._monotonic = monotonic
        self._validated_destination: _S3Destination | None = None
        self._known_jobs: list[BulkExportJob] | None = None

    @classmethod
    def from_langsmith_client(
        cls,
        client: object,
        *,
        destination_id: str,
        archive_uri: str,
        timeout_seconds: float = 5 * 60 * 60,
    ) -> LangSmithBulkExporter:
        # These are public LangSmith Client properties. Direct access is
        # deliberate so SDK contract changes fail instead of silently degrading.
        from langsmith import Client

        if not isinstance(client, Client):
            raise TypeError("Bulk Export requires a LangSmith Client")
        api_key = client.api_key
        if api_key is None:
            raise ValueError("Bulk Export requires a LangSmith API key")
        return cls(
            api_url=client.api_url,
            api_key=api_key,
            workspace_id=client.workspace_id
            or os.environ.get("LANGSMITH_WORKSPACE_ID"),
            destination_id=destination_id,
            archive_uri=archive_uri,
            timeout_seconds=timeout_seconds,
        )

    def export_window(
        self,
        *,
        project_id: str,
        start_time: datetime,
        end_time: datetime,
        excluded_export_ids: frozenset[str],
    ) -> BulkExportSnapshot:
        _require_uuid(project_id, "bulk export project ID")
        _require_utc_range(start_time, end_time)
        for export_id in excluded_export_ids:
            _require_uuid(export_id, "excluded bulk export ID")
        job = self.begin_window(
            project_id=project_id,
            start_time=start_time,
            end_time=end_time,
            excluded_export_ids=excluded_export_ids,
        )
        return self.complete_export(job)

    def begin_window(
        self,
        *,
        project_id: str,
        start_time: datetime,
        end_time: datetime,
        excluded_export_ids: frozenset[str],
    ) -> BulkExportJob:
        _require_uuid(project_id, "bulk export project ID")
        _require_utc_range(start_time, end_time)
        for export_id in excluded_export_ids:
            _require_uuid(export_id, "excluded bulk export ID")
        self._get_validated_destination()
        candidates = [
            job
            for job in self._list_exports()
            if self._matches_window(job, project_id, start_time, end_time)
            and job.export_id not in excluded_export_ids
            and job.status
            not in {
                BulkExportStatus.CANCELLED,
                BulkExportStatus.FAILED,
                BulkExportStatus.TIMED_OUT,
            }
        ]
        if candidates:
            return max(candidates, key=lambda candidate: candidate.created_at)
        return self._create_export(project_id, start_time, end_time)

    def complete_export(self, job: BulkExportJob) -> BulkExportSnapshot:
        return next(self.complete_exports((job,)))

    def complete_exports(
        self, jobs: Iterable[BulkExportJob]
    ) -> Iterator[BulkExportSnapshot]:
        """Yield snapshots as jobs complete, independent of submission order."""
        job_list = tuple(jobs)
        pending = {job.export_id: job for job in job_list}
        if len(pending) != len(job_list):
            raise ValueError("Duplicate bulk export ID in completion batch")
        destination = self._get_validated_destination()
        if len(pending) == 0:
            return
        deadline = self._monotonic() + self._timeout_seconds
        while pending:
            made_progress = False
            terminal_failure: BulkExportJob | None = None
            for export_id, current in tuple(pending.items()):
                if current.status in {
                    BulkExportStatus.CANCELLED,
                    BulkExportStatus.FAILED,
                    BulkExportStatus.TIMED_OUT,
                }:
                    if terminal_failure is None:
                        terminal_failure = current
                    continue
                if current.status is not BulkExportStatus.COMPLETED:
                    continue

                # INVARIANT: completion order never controls harvest order. A
                # ready job is removed and yielded immediately, even when an
                # earlier submission remains queued.
                del pending[export_id]
                made_progress = True
                yield self._read_snapshot(current, destination)

            # Ready peers have already been yielded to the caller for durable
            # import; only then does a terminal peer fail the batch.
            if terminal_failure is not None:
                raise BulkExportFailedError(
                    f"LangSmith bulk export {terminal_failure.export_id} ended as "
                    f"{terminal_failure.status.value}"
                )
            if not pending:
                return
            if made_progress:
                deadline = self._monotonic() + self._timeout_seconds
            if self._monotonic() >= deadline:
                raise BulkExportTimeoutError(
                    f"{len(pending)} LangSmith bulk export(s) did not finish in time"
                )
            self._sleep(self._poll_interval_seconds)
            for export_id, previous in tuple(pending.items()):
                raw = self._request(
                    "GET", f"/api/v1/bulk-exports/{export_id}", None, None
                )
                current = _parse_job(raw)
                if current.export_id != export_id or not self._matches_window(
                    current,
                    previous.project_id,
                    previous.start_time,
                    previous.end_time,
                ):
                    raise ValueError("Polled bulk export changed request identity")
                pending[export_id] = current

    def _get_validated_destination(self) -> _S3Destination:
        if self._validated_destination is not None:
            return self._validated_destination
        raw = self._request(
            "GET",
            f"/api/v1/bulk-exports/destinations/{self._destination_id}",
            None,
            None,
        )
        payload = _require_dict(raw, "bulk export destination")
        if _require_string(payload, "id") != self._destination_id:
            raise ValueError("Bulk export destination response changed identity")
        config = _require_dict(payload["config"], "bulk export destination config")
        destination = _S3Destination(
            bucket=_require_string(config, "bucket_name"),
            prefix=_require_string(config, "prefix").strip("/"),
        )
        _require_normalized_s3_path(
            destination.prefix, "Bulk export destination prefix"
        )
        archive = self._archive_destination
        if destination.bucket != archive.bucket:
            raise ValueError("Bulk export destination bucket does not match archive")
        if destination.prefix != archive.prefix and not destination.prefix.startswith(
            archive.prefix.rstrip("/") + "/"
        ):
            raise ValueError("Bulk export destination prefix is outside archive URI")
        self._validated_destination = destination
        return destination

    def _list_exports(self) -> tuple[BulkExportJob, ...]:
        if self._known_jobs is not None:
            return tuple(self._known_jobs)
        jobs: list[BulkExportJob] = []
        offset = 0
        while True:
            params: BulkExportListParams = {"limit": 1000, "offset": offset}
            raw = self._request(
                "GET", "/api/v1/bulk-exports", cast(dict[str, object], params), None
            )
            items = _require_list(raw, "bulk export list")
            jobs.extend(_parse_job(item) for item in items)
            if len(items) < 1000:
                self._known_jobs = jobs
                return tuple(self._known_jobs)
            offset += len(items)

    def _create_export(
        self, project_id: str, start_time: datetime, end_time: datetime
    ) -> BulkExportJob:
        payload: BulkExportCreateDict = {
            "bulk_export_destination_id": self._destination_id,
            "session_id": project_id,
            "start_time": start_time.isoformat(),
            "end_time": end_time.isoformat(),
            "format_version": "v2_beta",
            "compression": "zstandard",
            "export_fields": list(BULK_EXPORT_FIELDS),
        }
        raw = self._request(
            "POST",
            "/api/v1/bulk-exports",
            None,
            cast(dict[str, object], payload),
        )
        job = _parse_job(raw)
        if not self._matches_window(job, project_id, start_time, end_time):
            raise ValueError("Created bulk export does not match requested window")
        if self._known_jobs is not None:
            self._known_jobs.append(job)
        return job

    def _read_snapshot(
        self, job: BulkExportJob, destination: _S3Destination
    ) -> BulkExportSnapshot:
        raw = self._request(
            "GET", f"/api/v1/bulk-exports/{job.export_id}/runs", None, None
        )
        run_payloads = _require_list(raw, "bulk export run list")
        intervals: list[tuple[datetime, datetime]] = []
        partitions: list[BulkExportPartition] = []
        run_count = 0
        file_uris: list[str] = []
        for raw_run in run_payloads:
            run = _require_dict(raw_run, "bulk export run")
            status = BulkExportStatus(_require_string(run, "status"))
            if status is not BulkExportStatus.COMPLETED:
                raise BulkExportFailedError(
                    f"Bulk export partition {_require_string(run, 'id')} is "
                    f"{status.value}"
                )
            metadata = _require_dict(run["metadata"], "bulk export run metadata")
            start = _parse_datetime(_require_string(metadata, "start_time"))
            end = _parse_datetime(_require_string(metadata, "end_time"))
            intervals.append((start, end))
            result_raw = metadata["result"]
            if result_raw is None:
                raise ValueError("Completed bulk export partition has no result")
            result = _require_dict(result_raw, "bulk export run result")
            rows_written = _require_integer(result, "rows_written")
            if rows_written < 0:
                raise ValueError("Bulk export rows_written must be non-negative")
            run_count += rows_written
            partition_uris: list[str] = []
            for exported_file in _require_list(
                result["exported_files"], "bulk export file list"
            ):
                if not isinstance(exported_file, str):
                    raise ValueError("Bulk export file path must be a string")
                uri = _exported_file_uri(exported_file, destination)
                file_uris.append(uri)
                partition_uris.append(uri)
            partitions.append(
                BulkExportPartition(
                    start_time=start,
                    end_time=end,
                    run_count=rows_written,
                    file_uris=tuple(sorted(partition_uris)),
                )
            )
        _validate_partition_coverage(intervals, job.start_time, job.end_time)
        if run_count and not file_uris:
            raise ValueError("Non-empty bulk export did not publish Parquet files")
        return BulkExportSnapshot(
            export_id=job.export_id,
            start_time=job.start_time,
            end_time=job.end_time,
            run_count=run_count,
            file_uris=tuple(sorted(file_uris)),
            partitions=tuple(sorted(partitions, key=lambda item: item.start_time)),
        )

    def _matches_window(
        self,
        job: BulkExportJob,
        project_id: str,
        start_time: datetime,
        end_time: datetime,
    ) -> bool:
        return (
            job.destination_id == self._destination_id
            and job.project_id == project_id
            and job.start_time == start_time
            and job.end_time == end_time
            and job.format_version == "v2_beta"
            and job.compression == "zstandard"
            and job.interval_hours is None
            and job.filter is None
            and job.export_fields == BULK_EXPORT_FIELDS
            and not job.all_experiments
        )

    def _request(
        self,
        method: str,
        path: str,
        params: dict[str, object] | None,
        payload: dict[str, object] | None,
    ) -> object:
        if self._request_override is not None:
            return self._request_override(method, path, params, payload)
        import httpx

        headers = {"X-API-Key": self._api_key}
        if self._workspace_id is not None:
            headers["X-Tenant-Id"] = self._workspace_id
        request_params = (
            {key: str(value) for key, value in params.items()}
            if params is not None
            else None
        )
        response = httpx.request(
            method,
            self._api_root + path,
            headers=headers,
            params=request_params,
            json=payload,
            timeout=60,
        )
        response.raise_for_status()
        raw: object = response.json()
        return raw


def _parse_job(raw: object) -> BulkExportJob:
    payload = _require_dict(raw, "bulk export")
    end_time_raw = payload["end_time"]
    if not isinstance(end_time_raw, str):
        raise ValueError("One-off bulk export end_time must be a string")
    interval_raw = payload["interval_hours"]
    if interval_raw is not None and type(interval_raw) is not int:
        raise ValueError("Bulk export interval_hours must be an integer or null")
    filter_raw = payload["filter"]
    if filter_raw is not None and not isinstance(filter_raw, str):
        raise ValueError("Bulk export filter must be a string or null")
    fields_raw = payload["export_fields"]
    fields: tuple[str, ...] | None
    if fields_raw is None:
        fields = None
    else:
        fields = tuple(_require_string_list(fields_raw, "bulk export fields"))
    all_experiments = payload["all_experiments"]
    if type(all_experiments) is not bool:
        raise ValueError("Bulk export all_experiments must be a boolean")
    job = BulkExportJob(
        export_id=_require_string(payload, "id"),
        destination_id=_require_string(payload, "bulk_export_destination_id"),
        project_id=_require_string(payload, "session_id"),
        start_time=_parse_datetime(_require_string(payload, "start_time")),
        end_time=_parse_datetime(end_time_raw),
        status=BulkExportStatus(_require_string(payload, "status")),
        created_at=_parse_datetime(_require_string(payload, "created_at")),
        format_version=_require_string(payload, "format_version"),
        compression=_require_string(payload, "compression"),
        interval_hours=interval_raw,
        filter=filter_raw,
        export_fields=fields,
        all_experiments=all_experiments,
    )
    _require_uuid(job.export_id, "bulk export ID")
    _require_uuid(job.destination_id, "bulk export destination ID")
    _require_uuid(job.project_id, "bulk export project ID")
    _require_utc_range(job.start_time, job.end_time)
    return job


def _validate_partition_coverage(
    intervals: list[tuple[datetime, datetime]],
    expected_start: datetime,
    expected_end: datetime,
) -> None:
    if not intervals:
        raise ValueError("Completed bulk export has no partition runs")
    ordered = sorted(intervals)
    cursor = expected_start
    for start, end in ordered:
        if start != cursor or end <= start:
            raise ValueError("Bulk export partitions do not exactly cover the window")
        cursor = end
    if cursor != expected_end:
        raise ValueError("Bulk export partitions do not exactly cover the window")


def _exported_file_uri(path: str, destination: _S3Destination) -> str:
    expected_prefix = f"{destination.bucket}/{destination.prefix.rstrip('/')}/"
    if not path.startswith(expected_prefix) or not path.endswith(".parquet"):
        raise ValueError("Bulk export file is outside the configured destination")
    _require_normalized_s3_path(path, "Bulk export file path")
    return "s3://" + path


def _api_root(api_url: str) -> str:
    root = api_url.rstrip("/")
    if not root.startswith(("https://", "http://")):
        raise ValueError("LangSmith API URL must use HTTP or HTTPS")
    if root.endswith("/api"):
        root = root.removesuffix("/api")
    return root


def _parse_s3_uri(uri: str) -> _S3Destination:
    parsed = urlparse(uri)
    if parsed.scheme != "s3" or not parsed.netloc:
        raise ValueError("Bulk Export requires an s3:// archive URI")
    prefix = parsed.path.strip("/")
    if not prefix:
        raise ValueError("Bulk Export archive URI requires a bucket prefix")
    _require_normalized_s3_path(prefix, "Bulk Export archive prefix")
    return _S3Destination(bucket=parsed.netloc, prefix=prefix)


def _require_normalized_s3_path(value: str, context: str) -> None:
    # These strings become DuckDB S3 URIs. Reject both traversal segments and
    # URI metacharacters instead of relying on every HTTP layer to preserve a
    # literal S3 key in exactly the same way.
    if (
        "\\" in value
        or any(character in value for character in ("%", "?", "#"))
        or any(part in ("", ".", "..") for part in value.split("/"))
    ):
        raise ValueError(f"{context} must be normalized")


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Bulk export datetime must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _require_utc_range(start: datetime, end: datetime) -> None:
    if (
        start.tzinfo is None
        or end.tzinfo is None
        or start.utcoffset() != timedelta(0)
        or end.utcoffset() != timedelta(0)
        or end <= start
    ):
        raise ValueError("Bulk export window must be a non-empty UTC range")


def _require_uuid(value: str, field: str) -> None:
    try:
        parsed = UUID(value)
    except ValueError as exc:
        raise ValueError(f"{field} must be a UUID") from exc
    if str(parsed) != value.lower():
        raise ValueError(f"{field} must use canonical UUID format")


def _require_dict(raw: object, context: str) -> dict[str, object]:
    if not isinstance(raw, dict) or not all(isinstance(key, str) for key in raw):
        raise ValueError(f"{context} must be an object")
    return cast(dict[str, object], raw)


def _require_list(raw: object, context: str) -> list[object]:
    if not isinstance(raw, list):
        raise ValueError(f"{context} must be a list")
    return cast(list[object], raw)


def _require_string(payload: dict[str, object], field: str) -> str:
    value = payload[field]
    if not isinstance(value, str):
        raise ValueError(f"Bulk export field must be a string: {field}")
    return value


def _require_integer(payload: dict[str, object], field: str) -> int:
    value = payload[field]
    if type(value) is not int:
        raise ValueError(f"Bulk export field must be an integer: {field}")
    return value


def _require_string_list(raw: object, context: str) -> list[str]:
    values = _require_list(raw, context)
    if not all(isinstance(value, str) for value in values):
        raise ValueError(f"{context} must contain only strings")
    return cast(list[str], values)
