"""Local JSONL cache for LangSmith runs.

Provides offline storage and fast re-analysis of runs without hitting the API.
Each project gets its own JSONL file with a metadata sidecar for incremental updates.
"""

import json
import re
from collections.abc import Callable, Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, overload

from langsmith.schemas import Run
from platformdirs import user_cache_dir
from pydantic import BaseModel, Field, model_validator

from langsmith_cli.time_parsing import ensure_aware_datetime
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


@overload
def strip_binary_data(obj: dict[str, Any]) -> dict[str, Any]: ...


@overload
def strip_binary_data(obj: list[Any]) -> list[Any]: ...


@overload
def strip_binary_data(
    obj: str | int | float | bool | None,
) -> str | int | float | bool | None: ...


def strip_binary_data(
    obj: dict[str, Any] | list[Any] | str | int | float | bool | None,
) -> dict[str, Any] | list[Any] | str | int | float | bool | None:
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

    @model_validator(mode="after")
    def _normalize_datetimes(self) -> "CacheMetadata":
        """Normalize naive datetimes to UTC-aware on load.

        Old cache files may contain naive datetime strings (no timezone offset).
        Without normalization, comparing them against newly-fetched UTC-aware
        datetimes raises TypeError. This validator fixes all such values at
        deserialization time, making the model safe to use regardless of cache age.
        """
        self.oldest_run_start_time = ensure_aware_datetime(self.oldest_run_start_time)
        self.newest_run_start_time = ensure_aware_datetime(self.newest_run_start_time)
        return self


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

    import logging

    logger = logging.getLogger(__name__)
    runs: list[Run] = []
    for line in cache_path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            run = Run.model_validate(json.loads(line))
        except Exception as e:
            logger.warning("Skipping corrupt cache line in %s: %s", cache_path, e)
            continue
        start = ensure_aware_datetime(run.start_time)
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


def _update_meta_times(meta: CacheMetadata, t: datetime) -> None:
    """Update oldest/newest run time bounds in metadata in-place."""
    t = ensure_aware_datetime(t)
    if meta.oldest_run_start_time is None or t < meta.oldest_run_start_time:
        meta.oldest_run_start_time = t
    if meta.newest_run_start_time is None or t > meta.newest_run_start_time:
        meta.newest_run_start_time = t


def append_runs_streaming(
    project_name: str,
    runs_iter: Iterator[Run],
    *,
    existing_ids: set[str] | None = None,
    on_progress: Callable[[int], None] | None = None,
    batch_size: int = 100,
) -> tuple[CacheMetadata, int]:
    """Stream runs from an iterator into cache, writing in batches.

    Metadata is flushed to disk after every batch so an interrupted download
    always leaves the sidecar consistent with what was actually written.

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
    initial_count = meta.run_count

    total_new = 0
    batch: list[Run] = []

    def flush_batch() -> None:
        nonlocal total_new
        if not batch:
            return
        with open(cache_path, "a") as f:
            for run in batch:
                data = run.model_dump(mode="json")
                data = strip_binary_data(data)
                f.write(json.dumps(data, default=str) + "\n")
                _update_meta_times(meta, run.start_time)
        total_new += len(batch)
        batch.clear()
        # Write metadata after every batch — crash-safe
        meta.run_count = initial_count + total_new
        meta.last_updated = datetime.now(timezone.utc)
        write_cache_metadata(project_name, meta)
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
    return meta, total_new


def append_runs_to_cache(project_name: str, runs: list[Run]) -> CacheMetadata:
    """Append runs to a project's JSONL cache and update metadata.

    Delegates to append_runs_streaming for consistent crash-safe behaviour.

    Args:
        project_name: Project name
        runs: List of Run objects to append

    Returns:
        Updated CacheMetadata
    """
    meta, _ = append_runs_streaming(project_name, iter(runs))
    return meta


def find_orphaned_cache_files() -> list[str]:
    """Return sanitized names of JSONL cache files that have no metadata sidecar.

    An orphaned file is a .jsonl with no matching .meta.json — typically caused
    by a download that was interrupted before the final metadata write.
    """
    cache_dir = get_cache_dir()
    if not cache_dir.exists():
        return []
    orphaned = []
    for jsonl_file in sorted(cache_dir.glob("*.jsonl")):
        meta_path = jsonl_file.parent / (jsonl_file.stem + ".meta.json")
        if not meta_path.exists():
            orphaned.append(jsonl_file.stem)
    return orphaned


def repair_cache_metadata(project_name: str) -> CacheMetadata:
    """Regenerate cache metadata by scanning the JSONL file.

    Use this to fix an orphaned cache file (JSONL present, no .meta.json),
    or to reconcile a stale metadata count after a partial download.

    Args:
        project_name: Project name (or sanitized stem from find_orphaned_cache_files)

    Returns:
        Regenerated CacheMetadata written to disk

    Raises:
        FileNotFoundError: If no JSONL cache exists for the project
    """
    import logging

    logger = logging.getLogger(__name__)

    cache_path = get_cache_path(project_name)
    if not cache_path.exists():
        raise FileNotFoundError(f"No cache found for {project_name!r}")

    meta = CacheMetadata(project_name=project_name)

    for line in cache_path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except Exception:
            continue
        t_str = data.get("start_time")
        if t_str:
            try:
                t = datetime.fromisoformat(str(t_str).replace("Z", "+00:00"))
                _update_meta_times(meta, t)
            except Exception as e:
                logger.debug("Could not parse start_time %r: %s", t_str, e)
        meta.run_count += 1

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


def sample_raw_json_lines(
    project_name: str,
    n: int = 20,
) -> list[dict[str, Any]]:
    """Read up to N raw JSON lines from a project's JSONL cache file.

    Returns parsed dicts (not Run objects) to preserve all fields and
    avoid validation overhead.

    Raises:
        FileNotFoundError: If no cache file exists for the project.
    """
    cache_path = get_cache_path(project_name)
    if not cache_path.exists():
        raise FileNotFoundError(f"No cache found for {project_name!r}")

    samples: list[dict[str, Any]] = []
    for line in cache_path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            samples.append(json.loads(line))
        except Exception:
            continue
        if len(samples) >= n:
            break
    return samples


def list_cached_projects() -> list[CacheMetadata]:
    """List all cached projects that have a metadata sidecar."""
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
