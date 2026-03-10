"""Benchmarks for cache download: old sequential vs new parallel+streaming.

Run with:
    uv run pytest tests/test_cache_benchmark.py -v --benchmark-only
    uv run pytest tests/test_cache_benchmark.py -v --benchmark-compare

These benchmarks use mocked SDK responses to isolate the caching logic
from network latency. They measure:
- File I/O (JSONL write + dedup read)
- Serialization (Run.model_dump)
- Threading overhead (parallel workers)
"""

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
from langsmith.schemas import Run

from conftest import make_run_id
from langsmith_cli.cache import (
    append_runs_streaming,
    append_runs_to_cache,
    get_existing_run_ids,
)


def _make_runs(count: int, project: str = "test") -> list[Run]:
    """Create N test runs with realistic data."""
    runs = []
    for i in range(1, count + 1):
        runs.append(
            Run(
                id=UUID(make_run_id(i)),
                name=f"run-{i}",
                run_type="llm",
                start_time=datetime(2026, 3, 9, 12, i % 60, 0, tzinfo=timezone.utc),
                total_tokens=1000 * i,
                prompt_tokens=700 * i,
                completion_tokens=300 * i,
                total_cost=Decimal(f"0.{i:04d}"),
                extra={
                    "metadata": {
                        "ls_model_name": "gpt-4",
                        "channel_id": f"room-{i % 10}",
                        "session_id": f"sess-{i % 5}",
                    }
                },
                inputs={"prompt": f"Test prompt {i}" * 10},
                outputs={"response": f"Test response {i}" * 20},
            )
        )
    return runs


# Parameterize with different run counts
RUN_COUNTS = [100, 500, 1000]
PROJECT_COUNTS = [1, 4, 8]


class TestCacheWriteBenchmark:
    """Benchmark: writing runs to cache (old vs streaming)."""

    @pytest.mark.parametrize("num_runs", RUN_COUNTS)
    def test_old_append_runs_to_cache(
        self, benchmark: Any, tmp_path: Any, monkeypatch: Any, num_runs: int
    ) -> None:
        """Old approach: list() all runs, then append_runs_to_cache."""
        monkeypatch.setattr("langsmith_cli.cache.get_cache_dir", lambda: tmp_path)
        runs = _make_runs(num_runs)

        def do_append() -> None:
            # Clear between rounds
            cache_file = tmp_path / "test.jsonl"
            meta_file = tmp_path / "test.meta.json"
            if cache_file.exists():
                cache_file.unlink()
            if meta_file.exists():
                meta_file.unlink()
            append_runs_to_cache("test", runs)

        benchmark(do_append)

    @pytest.mark.parametrize("num_runs", RUN_COUNTS)
    def test_new_append_runs_streaming(
        self, benchmark: Any, tmp_path: Any, monkeypatch: Any, num_runs: int
    ) -> None:
        """New approach: streaming iterator with batch writes."""
        monkeypatch.setattr("langsmith_cli.cache.get_cache_dir", lambda: tmp_path)
        runs = _make_runs(num_runs)

        def do_stream() -> None:
            cache_file = tmp_path / "test.jsonl"
            meta_file = tmp_path / "test.meta.json"
            if cache_file.exists():
                cache_file.unlink()
            if meta_file.exists():
                meta_file.unlink()
            _meta, count = append_runs_streaming("test", iter(runs))
            assert count == num_runs

        benchmark(do_stream)


class TestCacheDedupBenchmark:
    """Benchmark: dedup performance with pre-existing cache."""

    @pytest.mark.parametrize("num_runs", RUN_COUNTS)
    def test_old_dedup_with_existing_cache(
        self, benchmark: Any, tmp_path: Any, monkeypatch: Any, num_runs: int
    ) -> None:
        """Old approach: dedup reads entire JSONL inside append."""
        monkeypatch.setattr("langsmith_cli.cache.get_cache_dir", lambda: tmp_path)
        # Pre-populate cache
        existing = _make_runs(num_runs)
        append_runs_to_cache("test", existing)

        # Now try to append the same runs (all should be deduped)
        def do_dedup() -> None:
            append_runs_to_cache("test", existing)

        benchmark(do_dedup)

    @pytest.mark.parametrize("num_runs", RUN_COUNTS)
    def test_new_dedup_with_preloaded_ids(
        self, benchmark: Any, tmp_path: Any, monkeypatch: Any, num_runs: int
    ) -> None:
        """New approach: pre-load IDs once, stream with dedup."""
        monkeypatch.setattr("langsmith_cli.cache.get_cache_dir", lambda: tmp_path)
        existing = _make_runs(num_runs)
        append_runs_to_cache("test", existing)
        # Pre-load IDs (done once before streaming)
        existing_ids = get_existing_run_ids("test")

        def do_dedup() -> None:
            _meta, count = append_runs_streaming(
                "test", iter(existing), existing_ids=set(existing_ids)
            )
            assert count == 0  # All deduped

        benchmark(do_dedup)


def _simulate_api_fetch(runs: list[Run], delay_per_batch: float = 0.01) -> list[Run]:
    """Simulate SDK list_runs with small delay to represent network latency."""
    time.sleep(delay_per_batch * (len(runs) // 100 + 1))
    return runs


class TestParallelVsSequentialBenchmark:
    """Benchmark: sequential vs parallel project fetching.

    Uses simulated API delay to represent realistic network conditions.
    """

    @pytest.mark.parametrize("num_projects", PROJECT_COUNTS)
    def test_sequential_projects(
        self, benchmark: Any, tmp_path: Any, monkeypatch: Any, num_projects: int
    ) -> None:
        """Old: fetch projects one by one."""
        monkeypatch.setattr("langsmith_cli.cache.get_cache_dir", lambda: tmp_path)
        runs_per_project = 200
        project_data = {
            f"proj-{i}": _make_runs(runs_per_project) for i in range(num_projects)
        }
        # Offset IDs so they don't collide across projects
        for i, (name, runs) in enumerate(project_data.items()):
            for j, run in enumerate(runs):
                new_id = UUID(make_run_id(i * 10000 + j + 1))
                object.__setattr__(run, "id", new_id)

        def do_sequential() -> None:
            for name, runs in project_data.items():
                # Clear
                cache_file = tmp_path / f"{name}.jsonl"
                meta_file = tmp_path / f"{name}.meta.json"
                if cache_file.exists():
                    cache_file.unlink()
                if meta_file.exists():
                    meta_file.unlink()

                fetched = _simulate_api_fetch(runs)
                append_runs_to_cache(name, fetched)

        benchmark(do_sequential)

    @pytest.mark.parametrize("num_projects", PROJECT_COUNTS)
    def test_parallel_projects(
        self, benchmark: Any, tmp_path: Any, monkeypatch: Any, num_projects: int
    ) -> None:
        """New: fetch projects in parallel with streaming."""
        monkeypatch.setattr("langsmith_cli.cache.get_cache_dir", lambda: tmp_path)
        runs_per_project = 200
        project_data = {
            f"proj-{i}": _make_runs(runs_per_project) for i in range(num_projects)
        }
        for i, (name, runs) in enumerate(project_data.items()):
            for j, run in enumerate(runs):
                new_id = UUID(make_run_id(i * 10000 + j + 1))
                object.__setattr__(run, "id", new_id)

        def download_one(name: str, runs: list[Run]) -> int:
            cache_file = tmp_path / f"{name}.jsonl"
            meta_file = tmp_path / f"{name}.meta.json"
            if cache_file.exists():
                cache_file.unlink()
            if meta_file.exists():
                meta_file.unlink()

            # Simulate fetch delay
            time.sleep(0.01 * (len(runs) // 100 + 1))
            _meta, count = append_runs_streaming(name, iter(runs))
            return count

        def do_parallel() -> None:
            workers = min(8, num_projects)
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {
                    executor.submit(download_one, name, runs): name
                    for name, runs in project_data.items()
                }
                for future in as_completed(futures):
                    future.result()

        benchmark(do_parallel)
