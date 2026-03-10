"""Local JSONL cache for LangSmith runs.

Provides offline storage and fast re-analysis of runs without hitting the API.
Each project gets its own JSONL file with a metadata sidecar for incremental updates.
"""

import json
import re
from collections.abc import Callable, Iterator
from datetime import datetime, timezone
from pathlib import Path

from langsmith.schemas import Run
from platformdirs import user_cache_dir
from pydantic import BaseModel, Field

from langsmith_cli.utils import FetchResult


# Threshold for stripping large base64/binary strings from cache.
# Strings longer than this that look like base64 are replaced with a placeholder.
_BINARY_STRIP_THRESHOLD = 10_000  # 10KB

# Base64 alphabet: A-Z, a-z, 0-9, +, /, = (padding), and optional whitespace
_BASE64_CHARS = set(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=\n\r "
)


def _is_likely_base64(s: str) -> bool:
    """Check if a string is likely base64-encoded binary data."""
    # Quick check: must be long and start with base64 chars
    if len(s) < _BINARY_STRIP_THRESHOLD:
        return False
    # Check first 200 chars — base64 data is uniform
    sample = s[:200]
    return all(c in _BASE64_CHARS for c in sample)


def _is_data_uri(s: str) -> bool:
    """Check if a string is a data: URI with embedded binary."""
    return s.startswith("data:") and ";base64," in s[:100]


def strip_binary_data(
    obj: dict | list | str | int | float | bool | None,
) -> dict | list | str | int | float | bool | None:
    """Recursively strip large base64/binary strings from a JSON-like structure.

    Replaces them with a placeholder that records the original size and type.
    This reduces cache size dramatically for runs containing inline images/videos.

    Optimization: returns the original object unchanged (identity) when no
    stripping is needed, avoiding unnecessary dict/list reconstruction.
    """
    if isinstance(obj, dict):
        changed = False
        new_dict: dict[str, dict | list | str | int | float | bool | None] = {}
        for k, v in obj.items():
            new_v = strip_binary_data(v)
            if new_v is not v:
                changed = True
            new_dict[k] = new_v
        return new_dict if changed else obj
    elif isinstance(obj, list):
        changed = False
        new_list: list[dict | list | str | int | float | bool | None] = []
        for item in obj:
            new_item = strip_binary_data(item)
            if new_item is not item:
                changed = True
            new_list.append(new_item)
        return new_list if changed else obj
    elif isinstance(obj, str):
        if _is_data_uri(obj):
            media_type = obj.split(";")[0].replace("data:", "")
            return f"[binary:{media_type}:{len(obj)}bytes:stored_in_langsmith]"
        if _is_likely_base64(obj):
            return f"[binary:base64:{len(obj)}bytes:stored_in_langsmith]"
    return obj


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
        start = run.start_time
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        if since and start < since:
            continue
        if until and start > until:
            continue
        runs.append(run)
    return runs


def get_existing_run_ids(project_name: str) -> set[str]:
    """Load existing run IDs from the cache file for deduplication.

    Reads only the 'id' field from each JSON line, avoiding full deserialization.
    """
    cache_path = get_cache_path(project_name)
    ids: set[str] = set()
    if not cache_path.exists():
        return ids
    for line in cache_path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        data = json.loads(line)
        if "id" in data:
            ids.add(str(data["id"]))
    return ids


def append_runs_streaming(
    project_name: str,
    runs_iter: Iterator[Run],
    *,
    existing_ids: set[str] | None = None,
    on_progress: Callable[[int], None] | None = None,
    batch_size: int = 100,
) -> tuple[CacheMetadata, int]:
    """Stream runs from an iterator into cache, writing in batches.

    Args:
        project_name: Project name
        runs_iter: Iterator of Run objects (from SDK list_runs)
        existing_ids: Pre-loaded set of existing run IDs for dedup.
            If None, loads from cache file.
        on_progress: Callback called with cumulative count after each batch write
        batch_size: Number of runs to buffer before flushing to disk

    Returns:
        Tuple of (updated CacheMetadata, number of new runs written)
    """
    cache_path = get_cache_path(project_name)
    cache_path.parent.mkdir(parents=True, exist_ok=True)

    if existing_ids is None:
        existing_ids = get_existing_run_ids(project_name)

    meta = read_cache_metadata(project_name) or CacheMetadata(project_name=project_name)

    total_new = 0
    batch: list[Run] = []
    min_time = meta.oldest_run_start_time
    max_time = meta.newest_run_start_time

    def flush_batch() -> None:
        nonlocal total_new, min_time, max_time
        if not batch:
            return
        with open(cache_path, "a") as f:
            for run in batch:
                data = run.model_dump(mode="json")
                data = strip_binary_data(data)
                f.write(json.dumps(data, default=str) + "\n")
                t = run.start_time
                if min_time is None or t < min_time:
                    min_time = t
                if max_time is None or t > max_time:
                    max_time = t
        total_new += len(batch)
        batch.clear()
        if on_progress:
            on_progress(total_new)

    for run in runs_iter:
        run_id = str(run.id)
        if run_id in existing_ids:
            continue
        existing_ids.add(run_id)
        batch.append(run)
        if len(batch) >= batch_size:
            flush_batch()

    flush_batch()  # Final partial batch

    # Update metadata
    if min_time:
        meta.oldest_run_start_time = min_time
    if max_time:
        meta.newest_run_start_time = max_time
    meta.run_count += total_new
    meta.last_updated = datetime.now(timezone.utc)
    write_cache_metadata(project_name, meta)

    return meta, total_new


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
                data = run.model_dump(mode="json")
                data = strip_binary_data(data)
                f.write(json.dumps(data, default=str) + "\n")

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
    source_map: dict[str, str] = {}

    for name in project_names:
        cache_path = get_cache_path(name)
        if not cache_path.exists():
            failed.append((name, "Not cached. Run 'runs cache download' first."))
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
