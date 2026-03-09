"""Local JSONL cache for LangSmith runs.

Provides offline storage and fast re-analysis of runs without hitting the API.
Each project gets its own JSONL file with a metadata sidecar for incremental updates.
"""

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from langsmith.schemas import Run
from platformdirs import user_cache_dir
from pydantic import BaseModel, Field

from langsmith_cli.utils import FetchResult


class CacheMetadata(BaseModel):
    """Metadata sidecar for a cached project's runs."""

    project_name: str
    project_id: str | None = None
    oldest_run_start_time: datetime | None = None
    newest_run_start_time: datetime | None = None
    run_count: int = 0
    last_updated: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    filters_used: str | None = None


def get_cache_dir() -> Path:
    """Get the cache directory for langsmith-cli runs."""
    return Path(user_cache_dir("langsmith-cli", appauthor=False)) / "runs"


def sanitize_project_name(name: str) -> str:
    """Convert project name to a filesystem-safe filename."""
    safe = re.sub(r'[/\\:*?"<>|]', "_", name)
    return safe[:200]


def get_cache_path(project_name: str) -> Path:
    """Get the JSONL cache file path for a project."""
    return get_cache_dir() / f"{sanitize_project_name(project_name)}.jsonl"


def get_cache_meta_path(project_name: str) -> Path:
    """Get the metadata sidecar path for a project."""
    return get_cache_dir() / f"{sanitize_project_name(project_name)}.meta.json"


def read_cache_metadata(project_name: str) -> CacheMetadata | None:
    """Read cache metadata for a project, or None if not cached."""
    meta_path = get_cache_meta_path(project_name)
    if not meta_path.exists():
        return None
    return CacheMetadata.model_validate_json(meta_path.read_text())


def write_cache_metadata(project_name: str, meta: CacheMetadata) -> None:
    """Write cache metadata sidecar."""
    meta_path = get_cache_meta_path(project_name)
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(meta.model_dump_json(indent=2))


def read_cached_runs(
    project_name: str,
    *,
    since: datetime | None = None,
    until: datetime | None = None,
) -> list[Run]:
    """Read cached runs from a project's JSONL file.

    Args:
        project_name: Project name to read from cache
        since: Only return runs with start_time >= since
        until: Only return runs with start_time <= until
    """
    cache_path = get_cache_path(project_name)
    if not cache_path.exists():
        return []

    runs: list[Run] = []
    for line in cache_path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        run = Run.model_validate(json.loads(line))
        if since and run.start_time < since:
            continue
        if until and run.start_time > until:
            continue
        runs.append(run)
    return runs


def append_runs_to_cache(project_name: str, runs: list[Run]) -> CacheMetadata:
    """Append runs to a project's JSONL cache and update metadata.

    Args:
        project_name: Project name
        runs: List of Run objects to append

    Returns:
        Updated CacheMetadata
    """
    cache_path = get_cache_path(project_name)
    cache_path.parent.mkdir(parents=True, exist_ok=True)

    # Read existing metadata
    meta = read_cache_metadata(project_name) or CacheMetadata(project_name=project_name)

    # Deduplicate against existing run IDs
    existing_ids: set[str] = set()
    if cache_path.exists():
        for line in cache_path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            data = json.loads(line)
            if "id" in data:
                existing_ids.add(str(data["id"]))

    new_runs = [r for r in runs if str(r.id) not in existing_ids]

    if new_runs:
        with open(cache_path, "a") as f:
            for run in new_runs:
                f.write(json.dumps(run.model_dump(mode="json"), default=str) + "\n")

    # Update metadata
    all_start_times = []
    if meta.oldest_run_start_time:
        all_start_times.append(meta.oldest_run_start_time)
    if meta.newest_run_start_time:
        all_start_times.append(meta.newest_run_start_time)
    for r in new_runs:
        all_start_times.append(r.start_time)

    if all_start_times:
        meta.oldest_run_start_time = min(all_start_times)
        meta.newest_run_start_time = max(all_start_times)

    meta.run_count += len(new_runs)
    meta.last_updated = datetime.now(timezone.utc)

    write_cache_metadata(project_name, meta)
    return meta


def clear_cache(project_name: str | None = None) -> int:
    """Clear cache files. Returns number of files deleted."""
    cache_dir = get_cache_dir()
    if not cache_dir.exists():
        return 0

    deleted = 0
    if project_name:
        for path in [get_cache_path(project_name), get_cache_meta_path(project_name)]:
            if path.exists():
                path.unlink()
                deleted += 1
    else:
        for f in cache_dir.iterdir():
            if f.suffix in (".jsonl", ".json"):
                f.unlink()
                deleted += 1
    return deleted


def list_cached_projects() -> list[CacheMetadata]:
    """List all cached projects with their metadata."""
    cache_dir = get_cache_dir()
    if not cache_dir.exists():
        return []

    results: list[CacheMetadata] = []
    for meta_file in sorted(cache_dir.glob("*.meta.json")):
        meta = CacheMetadata.model_validate_json(meta_file.read_text())
        results.append(meta)
    return results


def load_runs_from_cache(
    project_names: list[str],
    *,
    since: datetime | None = None,
    until: datetime | None = None,
) -> FetchResult[Run]:
    """Load runs from cache for multiple projects.

    Returns a FetchResult compatible with the API fetch pipeline.
    """
    all_runs: list[Run] = []
    successful: list[str] = []
    failed: list[tuple[str, str]] = []

    for name in project_names:
        cache_path = get_cache_path(name)
        if not cache_path.exists():
            failed.append((name, "Not cached. Run 'runs cache download' first."))
            continue
        runs = read_cached_runs(name, since=since, until=until)
        all_runs.extend(runs)
        successful.append(name)

    return FetchResult(
        items=all_runs,
        successful_sources=successful,
        failed_sources=failed,
    )
