"""Tests for the cache module and cache commands."""

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from langsmith.schemas import Run

from conftest import make_run_id, strip_ansi
from langsmith_cli.cache import (
    CacheMetadata,
    append_runs_to_cache,
    clear_cache,
    list_cached_projects,
    load_runs_from_cache,
    read_cache_metadata,
    read_cached_runs,
    sanitize_project_name,
    write_cache_metadata,
)
from langsmith_cli.main import cli


def _make_run(n: int, hour: int = 16, minute: int = 0, project: str = "test") -> Run:
    """Create a run for cache tests."""
    return Run(
        id=UUID(make_run_id(n)),
        name=f"run-{n}",
        run_type="llm",
        start_time=datetime(2026, 3, 9, hour, minute, 0, tzinfo=timezone.utc),
        total_tokens=1000 * n,
        prompt_tokens=700 * n,
        completion_tokens=300 * n,
        total_cost=Decimal(f"0.00{n}"),
        extra={"metadata": {"ls_model_name": "gpt-4", "channel_id": f"room-{n}"}},
    )


class TestSanitizeProjectName:
    def test_simple_name(self):
        assert sanitize_project_name("my-project") == "my-project"

    def test_slash_replaced(self):
        assert sanitize_project_name("prd/video_service") == "prd_video_service"

    def test_special_chars(self):
        assert sanitize_project_name('a:b*c?"d<e>f|g') == "a_b_c__d_e_f_g"

    def test_truncation(self):
        long_name = "x" * 300
        assert len(sanitize_project_name(long_name)) == 200


class TestCacheMetadata:
    def test_roundtrip(self, tmp_path, monkeypatch):
        monkeypatch.setattr("langsmith_cli.cache.get_cache_dir", lambda: tmp_path)

        meta = CacheMetadata(
            project_name="test-project",
            run_count=42,
            oldest_run_start_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
            newest_run_start_time=datetime(2026, 3, 9, tzinfo=timezone.utc),
        )
        write_cache_metadata("test-project", meta)
        loaded = read_cache_metadata("test-project")
        assert loaded is not None
        assert loaded.project_name == "test-project"
        assert loaded.run_count == 42

    def test_read_nonexistent(self, tmp_path, monkeypatch):
        monkeypatch.setattr("langsmith_cli.cache.get_cache_dir", lambda: tmp_path)
        assert read_cache_metadata("nonexistent") is None


class TestCacheReadWrite:
    def test_append_and_read(self, tmp_path, monkeypatch):
        monkeypatch.setattr("langsmith_cli.cache.get_cache_dir", lambda: tmp_path)

        runs = [_make_run(1), _make_run(2), _make_run(3)]
        meta = append_runs_to_cache("test-project", runs)

        assert meta.run_count == 3
        assert meta.oldest_run_start_time is not None
        assert meta.newest_run_start_time is not None

        loaded = read_cached_runs("test-project")
        assert len(loaded) == 3
        assert loaded[0].name == "run-1"

    def test_incremental_append(self, tmp_path, monkeypatch):
        monkeypatch.setattr("langsmith_cli.cache.get_cache_dir", lambda: tmp_path)

        # First batch
        append_runs_to_cache("test-project", [_make_run(1), _make_run(2)])
        # Second batch
        meta = append_runs_to_cache("test-project", [_make_run(3), _make_run(4)])

        assert meta.run_count == 4
        loaded = read_cached_runs("test-project")
        assert len(loaded) == 4

    def test_deduplication(self, tmp_path, monkeypatch):
        monkeypatch.setattr("langsmith_cli.cache.get_cache_dir", lambda: tmp_path)

        runs = [_make_run(1), _make_run(2)]
        append_runs_to_cache("test-project", runs)
        # Append same runs again
        append_runs_to_cache("test-project", runs)

        # Should not duplicate
        loaded = read_cached_runs("test-project")
        assert len(loaded) == 2

    def test_read_with_time_filter(self, tmp_path, monkeypatch):
        monkeypatch.setattr("langsmith_cli.cache.get_cache_dir", lambda: tmp_path)

        runs = [
            _make_run(1, hour=10),
            _make_run(2, hour=14),
            _make_run(3, hour=18),
        ]
        append_runs_to_cache("test-project", runs)

        since = datetime(2026, 3, 9, 12, 0, 0, tzinfo=timezone.utc)
        filtered = read_cached_runs("test-project", since=since)
        assert len(filtered) == 2
        assert all(r.start_time >= since for r in filtered)

    def test_read_nonexistent_project(self, tmp_path, monkeypatch):
        monkeypatch.setattr("langsmith_cli.cache.get_cache_dir", lambda: tmp_path)
        assert read_cached_runs("nonexistent") == []


class TestClearCache:
    def test_clear_specific_project(self, tmp_path, monkeypatch):
        monkeypatch.setattr("langsmith_cli.cache.get_cache_dir", lambda: tmp_path)

        append_runs_to_cache("project-a", [_make_run(1)])
        append_runs_to_cache("project-b", [_make_run(2)])

        deleted = clear_cache("project-a")
        assert deleted == 2  # .jsonl + .meta.json

        assert read_cached_runs("project-a") == []
        assert len(read_cached_runs("project-b")) == 1

    def test_clear_all(self, tmp_path, monkeypatch):
        monkeypatch.setattr("langsmith_cli.cache.get_cache_dir", lambda: tmp_path)

        append_runs_to_cache("project-a", [_make_run(1)])
        append_runs_to_cache("project-b", [_make_run(2)])

        deleted = clear_cache()
        assert deleted == 4  # 2 projects * 2 files each

    def test_clear_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr("langsmith_cli.cache.get_cache_dir", lambda: tmp_path)
        assert clear_cache() == 0


class TestListCachedProjects:
    def test_list_projects(self, tmp_path, monkeypatch):
        monkeypatch.setattr("langsmith_cli.cache.get_cache_dir", lambda: tmp_path)

        append_runs_to_cache("project-a", [_make_run(1)])
        append_runs_to_cache("project-b", [_make_run(2), _make_run(3)])

        projects = list_cached_projects()
        assert len(projects) == 2
        names = {p.project_name for p in projects}
        assert names == {"project-a", "project-b"}

    def test_list_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr("langsmith_cli.cache.get_cache_dir", lambda: tmp_path)
        assert list_cached_projects() == []


class TestLoadRunsFromCache:
    def test_load_multiple_projects(self, tmp_path, monkeypatch):
        monkeypatch.setattr("langsmith_cli.cache.get_cache_dir", lambda: tmp_path)

        append_runs_to_cache("proj-a", [_make_run(1)])
        append_runs_to_cache("proj-b", [_make_run(2), _make_run(3)])

        result = load_runs_from_cache(["proj-a", "proj-b"])
        assert len(result.items) == 3
        assert len(result.successful_sources) == 2
        assert not result.has_failures

    def test_load_missing_project(self, tmp_path, monkeypatch):
        monkeypatch.setattr("langsmith_cli.cache.get_cache_dir", lambda: tmp_path)

        append_runs_to_cache("proj-a", [_make_run(1)])

        result = load_runs_from_cache(["proj-a", "proj-missing"])
        assert len(result.items) == 1
        assert result.has_failures
        assert "proj-missing" in result.failed_sources[0][0]

    def test_load_with_time_filter(self, tmp_path, monkeypatch):
        monkeypatch.setattr("langsmith_cli.cache.get_cache_dir", lambda: tmp_path)

        runs = [_make_run(1, hour=10), _make_run(2, hour=14), _make_run(3, hour=18)]
        append_runs_to_cache("proj", runs)

        since = datetime(2026, 3, 9, 12, 0, 0, tzinfo=timezone.utc)
        result = load_runs_from_cache(["proj"], since=since)
        assert len(result.items) == 2


class TestCacheCommands:
    """Tests for runs cache CLI commands."""

    def test_cache_download(self, runner, mock_client, tmp_path, monkeypatch):
        """Download command caches runs to JSONL."""
        monkeypatch.setattr("langsmith_cli.cache.get_cache_dir", lambda: tmp_path)

        runs = [_make_run(1), _make_run(2)]
        mock_client.list_runs.return_value = runs

        result = runner.invoke(
            cli,
            ["runs", "cache", "download", "--last", "24h"],
        )

        assert result.exit_code == 0
        assert (
            "cached" in result.output.lower() or "no new runs" in result.output.lower()
        )

    def test_cache_list_empty(self, runner, tmp_path, monkeypatch):
        """List command shows empty state."""
        monkeypatch.setattr("langsmith_cli.cache.get_cache_dir", lambda: tmp_path)

        result = runner.invoke(cli, ["runs", "cache", "list"])

        assert result.exit_code == 0
        assert "No cached" in result.output

    def test_cache_list_with_data(self, runner, tmp_path, monkeypatch):
        """List command shows cached project info."""
        monkeypatch.setattr("langsmith_cli.cache.get_cache_dir", lambda: tmp_path)

        append_runs_to_cache("test-project", [_make_run(1), _make_run(2)])

        result = runner.invoke(cli, ["runs", "cache", "list"])

        assert result.exit_code == 0
        output = strip_ansi(result.output)
        assert "test-project" in output

    def test_cache_clear_with_yes(self, runner, tmp_path, monkeypatch):
        """Clear command removes cache with --yes flag."""
        monkeypatch.setattr("langsmith_cli.cache.get_cache_dir", lambda: tmp_path)

        append_runs_to_cache("test-project", [_make_run(1)])

        result = runner.invoke(cli, ["runs", "cache", "clear", "--yes"])

        assert result.exit_code == 0
        assert list_cached_projects() == []

    def test_cache_clear_specific_project(self, runner, tmp_path, monkeypatch):
        """Clear command can target a specific project."""
        monkeypatch.setattr("langsmith_cli.cache.get_cache_dir", lambda: tmp_path)

        append_runs_to_cache("proj-a", [_make_run(1)])
        append_runs_to_cache("proj-b", [_make_run(2)])

        result = runner.invoke(cli, ["runs", "cache", "clear", "--project", "proj-a"])

        assert result.exit_code == 0
        projects = list_cached_projects()
        assert len(projects) == 1
        assert projects[0].project_name == "proj-b"
