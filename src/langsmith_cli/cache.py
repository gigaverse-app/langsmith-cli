"""Compatibility facade over the Parquet local trace inventory.

The active cache has one implementation: ``LocalTraceRepository``. Existing
analysis commands keep these function names while sharing the same DuckDB query and
atomic publication paths as ``runs --source local``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import NAMESPACE_URL, uuid5

from pydantic import BaseModel, ConfigDict

from langsmith_cli.local_traces.models import TracePullRequest, TraceSource
from langsmith_cli.project_resolution import FetchResult
from langsmith_cli.time_parsing import ensure_aware_datetime

if TYPE_CHECKING:
    from langsmith.schemas import Run


class CacheMetadata(BaseModel):
    """Project summary retained for compatibility with cache analysis commands."""

    model_config = ConfigDict(extra="forbid")

    project_name: str
    project_id: str | None = None
    oldest_run_start_time: datetime | None = None
    newest_run_start_time: datetime | None = None
    run_count: int = 0
    last_updated: datetime
    filters_used: str | None = None
    fragment_count: int = 0
    origins: tuple[TraceSource, ...] = ()


def get_cache_dir() -> Path:
    from langsmith_cli.local_traces.service import default_local_trace_directory

    return default_local_trace_directory()


def _repository():
    from langsmith_cli.local_traces.repository import LocalTraceRepository

    return LocalTraceRepository(get_cache_dir())


def read_cached_runs(
    project_name: str,
    *,
    since: datetime | None = None,
    until: datetime | None = None,
) -> list[Run]:
    """Read one project through the same typed DuckDB backend as `runs list`."""
    from langsmith_cli.trace_query import RunQuery

    return _repository().query(
        RunQuery(
            project=project_name,
            since=ensure_aware_datetime(since),
            before=ensure_aware_datetime(until),
            limit=None,
        )
    )


def get_existing_run_ids(project_name: str) -> set[str]:
    return {str(run.id) for run in read_cached_runs(project_name)}


def append_runs_streaming(
    project_name: str,
    runs_iter: Iterator[Run],
    *,
    existing_ids: set[str] | None = None,
    on_progress: Callable[[int], None] | None = None,
    batch_size: int = 100,
) -> tuple[CacheMetadata, int]:
    """Publish streamed SDK Runs as bounded immutable Parquet fragments.

    ``existing_ids`` remains accepted for source compatibility, but logical
    deduplication is enforced by the repository rather than trusted caller state.
    """
    if batch_size <= 0:
        raise ValueError("Cache batch_size must be positive")
    del existing_ids
    observed_at = datetime.now(timezone.utc)
    total_added = 0
    batch: list[Run] = []

    def publish_batch() -> None:
        nonlocal total_added
        if not batch:
            return
        project_id = _project_id(project_name, batch)
        request = TracePullRequest(
            source=TraceSource.CLOUD,
            project_id=project_id,
            project_name=project_name,
            requested_at=observed_at,
        )
        result = _repository().add_runs(request, batch)
        total_added += result.added_run_count
        batch.clear()
        if on_progress is not None:
            on_progress(total_added)

    for run in runs_iter:
        batch.append(run)
        if len(batch) >= batch_size:
            publish_batch()
    publish_batch()
    return _metadata_for(project_name), total_added


def append_runs_to_cache(project_name: str, runs: list[Run]) -> CacheMetadata:
    metadata, _added = append_runs_streaming(project_name, iter(runs))
    return metadata


def read_cache_metadata(project_name: str) -> CacheMetadata | None:
    for metadata in list_cached_projects():
        if metadata.project_name == project_name:
            return metadata
    return None


def list_cached_projects() -> list[CacheMetadata]:
    return [
        CacheMetadata(
            project_name=summary.project_name,
            project_id=summary.project_id,
            oldest_run_start_time=summary.oldest_run_start_time,
            newest_run_start_time=summary.newest_run_start_time,
            run_count=summary.run_count,
            last_updated=summary.last_updated,
            fragment_count=summary.fragment_count,
            origins=summary.origins,
        )
        for summary in _repository().list_projects()
    ]


def clear_cache(project_name: str | None = None) -> int:
    """Atomically evict logical cache entries and return removed fragment count."""
    return _repository().evict(project_name).removed_fragment_count


def sample_cached_rows(project_name: str, n: int = 20) -> list[dict[str, object]]:
    if n < 0:
        raise ValueError("Cache sample size must be non-negative")
    metadata = read_cache_metadata(project_name)
    if metadata is None:
        raise FileNotFoundError(f"No cache found for {project_name!r}")
    return [run.model_dump(mode="json") for run in read_cached_runs(project_name)[:n]]


def load_runs_from_cache(
    project_names: list[str],
    *,
    since: datetime | None = None,
    until: datetime | None = None,
) -> FetchResult[Run]:
    """Load projects through one backend for usage/pricing compatibility."""
    all_runs: list[Run] = []
    successful: list[str] = []
    failed: list[tuple[str, str]] = []
    source_map: dict[str, str] = {}
    available = {metadata.project_name for metadata in list_cached_projects()}
    for name in project_names:
        if name not in available:
            failed.append((name, "Not cached. Run 'runs pull --to local' first."))
            continue
        runs = read_cached_runs(name, since=since, until=until)
        for run in runs:
            source_map[str(run.id)] = name
        all_runs.extend(runs)
        successful.append(name)
    return FetchResult(
        items=all_runs,
        successful_sources=successful,
        failed_sources=failed,
        item_source_map=source_map,
    )


def _metadata_for(project_name: str) -> CacheMetadata:
    metadata = read_cache_metadata(project_name)
    if metadata is not None:
        return metadata
    return CacheMetadata(
        project_name=project_name,
        last_updated=datetime.now(timezone.utc),
    )


def _project_id(project_name: str, runs: list[Run]) -> str:
    project_ids = {str(run.session_id) for run in runs if run.session_id is not None}
    if len(project_ids) > 1:
        raise ValueError("Cached batch contains multiple project identities")
    if project_ids:
        return project_ids.pop()
    # Compatibility callers historically supplied only a project name. A stable
    # synthetic scope keeps those SDK Runs queryable without weakening identity.
    return str(uuid5(NAMESPACE_URL, f"langsmith-cli-project:{project_name}"))
