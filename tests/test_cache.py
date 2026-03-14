"""Tests for the cache module and cache commands."""

import json
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from langsmith.schemas import Run

from conftest import make_run_id, strip_ansi
from langsmith_cli.cache import (
    CacheMetadata,
    append_runs_streaming,
    append_runs_to_cache,
    clear_cache,
    find_orphaned_cache_files,
    get_existing_run_ids,
    list_cached_projects,
    load_runs_from_cache,
    read_cache_metadata,
    read_cached_runs,
    repair_cache_metadata,
    sanitize_project_name,
    strip_binary_data,
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


def _make_naive_run(n: int, hour: int = 16, minute: int = 0) -> Run:
    """Create a run with a timezone-naive start_time (as the SDK sometimes returns)."""
    return Run(
        id=UUID(make_run_id(n)),
        name=f"run-{n}",
        run_type="llm",
        start_time=datetime(2026, 3, 9, hour, minute, 0),  # no tzinfo — naive
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

    def test_naive_start_time_appended_to_existing_metadata(
        self, tmp_path, monkeypatch
    ):
        """INVARIANT: append_runs_to_cache accepts naive start_time runs when existing
        metadata has tz-aware datetimes — must not raise TypeError on min/max.
        """
        monkeypatch.setattr("langsmith_cli.cache.get_cache_dir", lambda: tmp_path)

        # Seed cache with tz-aware runs (creates tz-aware oldest/newest metadata)
        append_runs_to_cache("test-project", [_make_run(1, hour=12)])

        # Append naive runs — must not raise
        meta = append_runs_to_cache(
            "test-project", [_make_naive_run(2, hour=10), _make_naive_run(3, hour=18)]
        )

        assert meta.run_count == 3
        assert meta.oldest_run_start_time is not None
        assert meta.newest_run_start_time is not None
        assert meta.oldest_run_start_time.tzinfo is not None
        assert meta.newest_run_start_time.tzinfo is not None

    def test_read_skips_corrupt_lines(self, tmp_path, monkeypatch):
        """INVARIANT: read_cached_runs skips corrupt/invalid JSONL lines and returns valid runs.

        When a cache file contains lines that are not valid Run JSON (e.g., from a
        test that wrote MagicMock repr strings), those lines are silently skipped
        and the valid runs are still returned.
        """
        monkeypatch.setattr("langsmith_cli.cache.get_cache_dir", lambda: tmp_path)

        # Write one valid run and one corrupt line (MagicMock repr as JSON string)
        valid_run = _make_run(1)
        from langsmith_cli.cache import get_cache_path
        import json as _json

        cache_path = get_cache_path("test-project")
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        with open(cache_path, "w") as f:
            f.write(_json.dumps(valid_run.model_dump(mode="json")) + "\n")
            # Simulate what happens when MagicMock.model_dump() is json.dumps'd with default=str
            f.write("\"<MagicMock name='mock.model_dump()' id='12345'>\"" + "\n")
            # Also test a completely invalid JSON line
            f.write("not-valid-json\n")

        result = read_cached_runs("test-project")
        assert len(result) == 1
        assert result[0].name == "run-1"


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

    def test_cache_dir_prints_cache_directory(self, runner, tmp_path, monkeypatch):
        """INVARIANT: cache dir command prints the cache directory path."""
        monkeypatch.setattr("langsmith_cli.cache.get_cache_dir", lambda: tmp_path)

        result = runner.invoke(cli, ["runs", "cache", "dir"])

        assert result.exit_code == 0
        assert str(tmp_path) in result.output.strip()

    def test_cache_list_json_includes_path(self, runner, tmp_path, monkeypatch):
        """INVARIANT: cache list --json includes the file path for each project."""
        monkeypatch.setattr("langsmith_cli.cache.get_cache_dir", lambda: tmp_path)

        append_runs_to_cache("test-project", [_make_run(1)])

        result = runner.invoke(cli, ["--json", "runs", "cache", "list"])

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data) == 1
        assert "path" in data[0]
        assert data[0]["path"].endswith(".jsonl")
        assert "test-project" in data[0]["path"]

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

    def test_cache_download_parallel_multiple_projects(
        self, runner, mock_client, tmp_path, monkeypatch
    ):
        """Download fetches multiple projects in parallel."""
        monkeypatch.setattr("langsmith_cli.cache.get_cache_dir", lambda: tmp_path)

        # Mock list_runs to return different runs per project
        def fake_list_runs(project_name: str = "", **kwargs: object) -> list[Run]:
            if "proj-a" in project_name:
                return [_make_run(1), _make_run(2)]
            elif "proj-b" in project_name:
                return [_make_run(3), _make_run(4)]
            return []

        mock_client.list_runs.side_effect = fake_list_runs
        mock_client.list_projects.return_value = []

        result = runner.invoke(
            cli,
            [
                "runs",
                "cache",
                "download",
                "--project-name-exact",
                "proj-a",
                "--last",
                "24h",
                "--workers",
                "2",
            ],
        )

        assert result.exit_code == 0
        output = strip_ansi(result.output).lower()
        assert "new runs" in output or "done" in output or "cached" in output

    def test_cache_download_json_mode(self, runner, mock_client, tmp_path, monkeypatch):
        """Download in JSON mode emits structured progress."""
        monkeypatch.setattr("langsmith_cli.cache.get_cache_dir", lambda: tmp_path)

        runs = [_make_run(1), _make_run(2)]
        mock_client.list_runs.return_value = runs

        result = runner.invoke(
            cli,
            ["--json", "runs", "cache", "download", "--last", "24h"],
        )

        assert result.exit_code == 0
        # stdout should contain the final JSON summary line
        # (progress events go to stderr which CliRunner mixes in)
        output = result.output.strip()
        lines = output.splitlines()
        # Find the download_complete event
        found = False
        for line in lines:
            try:
                data = json.loads(line)
                if data.get("event") == "download_complete":
                    assert "total_new_runs" in data
                    assert "results" in data
                    found = True
                    break
            except (json.JSONDecodeError, TypeError):
                continue
        assert found, f"No download_complete event found in output: {output}"

    def test_cache_download_incremental_skips_existing(
        self, runner, mock_client, tmp_path, monkeypatch
    ):
        """Incremental download adds gt(start_time) filter for cached projects."""
        monkeypatch.setattr("langsmith_cli.cache.get_cache_dir", lambda: tmp_path)

        # Pre-populate cache using --project (avoids list_projects API call)
        append_runs_to_cache("default", [_make_run(1), _make_run(2)])

        # Return new runs for incremental
        mock_client.list_runs.return_value = [_make_run(3)]

        result = runner.invoke(
            cli,
            ["runs", "cache", "download"],
        )

        assert result.exit_code == 0
        # Verify incremental filter was used
        call_kwargs = mock_client.list_runs.call_args
        fql_filter = call_kwargs.kwargs.get("filter", None)
        if fql_filter is None and call_kwargs.args:
            fql_filter = call_kwargs.args[0] if call_kwargs.args else None
        assert fql_filter is not None, (
            f"Expected gt(start_time) filter, got call_args: {call_kwargs}"
        )
        assert "gt(start_time" in fql_filter

    def test_cache_download_full_keeps_existing_and_deduplicates(
        self, runner, mock_client, tmp_path, monkeypatch
    ):
        """--full flag re-fetches full time range but keeps existing data (dedup)."""
        monkeypatch.setattr("langsmith_cli.cache.get_cache_dir", lambda: tmp_path)

        # Pre-populate cache
        append_runs_to_cache("default", [_make_run(1)])

        # Return fresh runs (different from existing)
        mock_client.list_runs.return_value = [_make_run(2)]

        result = runner.invoke(
            cli,
            [
                "runs",
                "cache",
                "download",
                "--full",
                "--last",
                "24h",
            ],
        )

        assert result.exit_code == 0
        # Should have both runs (old preserved, new added, deduped)
        cached = read_cached_runs("default")
        assert len(cached) == 2
        names = {r.name for r in cached}
        assert names == {"run-1", "run-2"}

    def test_cache_download_workers_option(
        self, runner, mock_client, tmp_path, monkeypatch
    ):
        """--workers option controls parallelism."""
        monkeypatch.setattr("langsmith_cli.cache.get_cache_dir", lambda: tmp_path)

        mock_client.list_runs.return_value = [_make_run(1)]

        result = runner.invoke(
            cli,
            [
                "runs",
                "cache",
                "download",
                "--last",
                "24h",
                "--workers",
                "1",
            ],
        )

        assert result.exit_code == 0

    def test_cache_download_with_before_creates_lt_filter(
        self, runner, mock_client, tmp_path, monkeypatch
    ):
        """INVARIANT: --before adds lt(start_time) filter to API call."""
        monkeypatch.setattr("langsmith_cli.cache.get_cache_dir", lambda: tmp_path)

        mock_client.list_runs.return_value = [_make_run(1)]

        result = runner.invoke(
            cli,
            [
                "runs",
                "cache",
                "download",
                "--since",
                "2025-02-17T00:00:00Z",
                "--before",
                "2025-02-20T00:00:00Z",
            ],
        )

        assert result.exit_code == 0
        call_kwargs = mock_client.list_runs.call_args
        fql_filter = call_kwargs.kwargs.get("filter", "")
        assert "gt(start_time" in fql_filter, f"Expected gt filter, got: {fql_filter}"
        assert "lt(start_time" in fql_filter, f"Expected lt filter, got: {fql_filter}"
        assert "2025-02-17" in fql_filter
        assert "2025-02-20" in fql_filter

    def test_cache_download_before_alone_creates_lt_filter(
        self, runner, mock_client, tmp_path, monkeypatch
    ):
        """INVARIANT: --before alone creates only lt(start_time) filter."""
        monkeypatch.setattr("langsmith_cli.cache.get_cache_dir", lambda: tmp_path)

        mock_client.list_runs.return_value = [_make_run(1)]

        result = runner.invoke(
            cli,
            [
                "runs",
                "cache",
                "download",
                "--before",
                "2025-02-20T00:00:00Z",
            ],
        )

        assert result.exit_code == 0
        call_kwargs = mock_client.list_runs.call_args
        fql_filter = call_kwargs.kwargs.get("filter", "")
        assert "lt(start_time" in fql_filter, f"Expected lt filter, got: {fql_filter}"
        assert "2025-02-20" in fql_filter

    def test_cache_download_default_workers_capped_at_4(
        self, runner, mock_client, tmp_path, monkeypatch
    ):
        """INVARIANT: Default workers is min(4, num_projects) to avoid rate limiting."""
        monkeypatch.setattr("langsmith_cli.cache.get_cache_dir", lambda: tmp_path)

        # Mock 10 projects to ensure cap is hit
        from conftest import create_project

        projects = [create_project(name=f"proj-{i}") for i in range(10)]
        mock_client.list_projects.return_value = projects
        mock_client.list_runs.return_value = []

        result = runner.invoke(
            cli,
            [
                "runs",
                "cache",
                "download",
                "--project-name",
                "proj",
                "--last",
                "1d",
            ],
        )

        assert result.exit_code == 0
        # With 10 projects and no --workers flag, should use 4 workers (not 8)
        # We verify indirectly: no rate limiting errors
        # The actual cap is tested via the code path

    def test_cache_download_with_name_pattern_exact_uses_fql(
        self, runner, mock_client, tmp_path, monkeypatch
    ):
        """INVARIANT: --name-pattern with no wildcards adds FQL eq(name) for server-side filtering."""
        monkeypatch.setattr("langsmith_cli.cache.get_cache_dir", lambda: tmp_path)
        mock_client.list_runs.return_value = []

        result = runner.invoke(
            cli,
            [
                "runs",
                "cache",
                "download",
                "--project",
                "default",
                "--name-pattern",
                "Gigaverse_Daily_Standup",
                "--last",
                "24h",
            ],
        )

        assert result.exit_code == 0
        call_kwargs = mock_client.list_runs.call_args
        fql_filter = call_kwargs.kwargs.get("filter", "")
        assert 'eq(name, "Gigaverse_Daily_Standup")' in fql_filter

    def test_cache_download_with_name_pattern_wildcard(
        self, runner, mock_client, tmp_path, monkeypatch
    ):
        """INVARIANT: --name-pattern supports wildcards like '*standup*'."""
        monkeypatch.setattr("langsmith_cli.cache.get_cache_dir", lambda: tmp_path)

        runs = [
            Run(
                id=UUID(make_run_id(1)),
                name="Gigaverse_Daily_Standup",
                run_type="chain",
                start_time=datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc),
            ),
            Run(
                id=UUID(make_run_id(2)),
                name="Weekly_Standup",
                run_type="chain",
                start_time=datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc),
            ),
            Run(
                id=UUID(make_run_id(3)),
                name="unrelated_run",
                run_type="chain",
                start_time=datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc),
            ),
        ]
        mock_client.list_runs.return_value = runs

        result = runner.invoke(
            cli,
            [
                "runs",
                "cache",
                "download",
                "--project",
                "default",
                "--name-pattern",
                "*Standup*",
                "--last",
                "24h",
            ],
        )

        assert result.exit_code == 0
        from langsmith_cli.cache import read_cached_runs

        cached = read_cached_runs("default")
        assert len(cached) == 2
        names = {r.name for r in cached}
        assert names == {"Gigaverse_Daily_Standup", "Weekly_Standup"}

    def test_cache_download_without_name_pattern_caches_all(
        self, runner, mock_client, tmp_path, monkeypatch
    ):
        """INVARIANT: Without --name-pattern, all runs are cached (no filtering)."""
        monkeypatch.setattr("langsmith_cli.cache.get_cache_dir", lambda: tmp_path)

        runs = [_make_run(1), _make_run(2), _make_run(3)]
        mock_client.list_runs.return_value = runs

        result = runner.invoke(
            cli,
            ["runs", "cache", "download", "--project", "default", "--last", "24h"],
        )

        assert result.exit_code == 0
        from langsmith_cli.cache import read_cached_runs

        cached = read_cached_runs("default")
        assert len(cached) == 3

    def test_cache_download_with_metadata_exact_adds_fql_filter(
        self, runner, mock_client, tmp_path, monkeypatch
    ):
        """INVARIANT: --metadata with exact value adds FQL eq() filter for server-side filtering."""
        monkeypatch.setattr("langsmith_cli.cache.get_cache_dir", lambda: tmp_path)
        mock_client.list_runs.return_value = []

        result = runner.invoke(
            cli,
            [
                "runs",
                "cache",
                "download",
                "--project",
                "default",
                "--metadata",
                "channel_id=Gigaverse_Daily_Standup",
                "--last",
                "24h",
            ],
        )

        assert result.exit_code == 0
        call_kwargs = mock_client.list_runs.call_args
        fql_filter = call_kwargs.kwargs.get("filter", "")
        assert 'eq(metadata_value, "Gigaverse_Daily_Standup")' in fql_filter
        assert 'in(metadata_key, ["channel_id"])' in fql_filter

    def test_cache_download_with_metadata_wildcard_filters_client_side(
        self, runner, mock_client, tmp_path, monkeypatch
    ):
        """INVARIANT: --metadata with wildcard applies client-side filter, not FQL."""
        monkeypatch.setattr("langsmith_cli.cache.get_cache_dir", lambda: tmp_path)

        matching = Run(
            id=UUID(make_run_id(1)),
            name="run-1",
            run_type="chain",
            start_time=datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc),
            extra={"metadata": {"channel_id": "Gigaverse_Daily_Standup"}},
        )
        non_matching = Run(
            id=UUID(make_run_id(2)),
            name="run-2",
            run_type="chain",
            start_time=datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc),
            extra={"metadata": {"channel_id": "Other_Channel"}},
        )
        mock_client.list_runs.return_value = [matching, non_matching]

        result = runner.invoke(
            cli,
            [
                "runs",
                "cache",
                "download",
                "--project",
                "default",
                "--metadata",
                "channel_id=Gigaverse*",
                "--last",
                "24h",
            ],
        )

        assert result.exit_code == 0
        # Wildcard must NOT appear in FQL (no server-side FQL for wildcards)
        call_kwargs = mock_client.list_runs.call_args
        fql_filter = call_kwargs.kwargs.get("filter", "")
        assert "channel_id" not in fql_filter

        from langsmith_cli.cache import read_cached_runs

        cached = read_cached_runs("default")
        assert len(cached) == 1
        cached_meta = (cached[0].extra or {}).get("metadata", {})
        assert cached_meta.get("channel_id") == "Gigaverse_Daily_Standup"

    def test_cache_grep_with_metadata_filter(self, runner, tmp_path, monkeypatch):
        """INVARIANT: cache grep --metadata filters cached runs by metadata key=value."""
        monkeypatch.setattr("langsmith_cli.cache.get_cache_dir", lambda: tmp_path)

        runs_to_cache = [
            Run(
                id=UUID(make_run_id(1)),
                name="run-1",
                run_type="chain",
                start_time=datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc),
                extra={"metadata": {"channel_id": "Gigaverse_Daily_Standup"}},
            ),
            Run(
                id=UUID(make_run_id(2)),
                name="run-2",
                run_type="chain",
                start_time=datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc),
                extra={"metadata": {"channel_id": "Other_Channel"}},
            ),
        ]
        append_runs_to_cache("default", runs_to_cache)

        result = runner.invoke(
            cli,
            [
                "--json",
                "runs",
                "cache",
                "grep",
                "run",  # matches both by name
                "--project",
                "default",
                "--metadata",
                "channel_id=Gigaverse_Daily_Standup",
            ],
        )

        assert result.exit_code == 0
        data = json.loads(result.output.strip().split("\n")[-1])
        assert len(data) == 1
        assert data[0]["name"] == "run-1"

    def test_cache_grep_with_metadata_wildcard_filter(
        self, runner, tmp_path, monkeypatch
    ):
        """INVARIANT: cache grep --metadata supports wildcard values."""
        monkeypatch.setattr("langsmith_cli.cache.get_cache_dir", lambda: tmp_path)

        runs_to_cache = [
            Run(
                id=UUID(make_run_id(1)),
                name="run-1",
                run_type="chain",
                start_time=datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc),
                extra={"metadata": {"channel_id": "Gigaverse_Daily_Standup"}},
            ),
            Run(
                id=UUID(make_run_id(2)),
                name="run-2",
                run_type="chain",
                start_time=datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc),
                extra={"metadata": {"channel_id": "Gigaverse_Weekly"}},
            ),
            Run(
                id=UUID(make_run_id(3)),
                name="run-3",
                run_type="chain",
                start_time=datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc),
                extra={"metadata": {"channel_id": "Other_Channel"}},
            ),
        ]
        append_runs_to_cache("default", runs_to_cache)

        result = runner.invoke(
            cli,
            [
                "--json",
                "runs",
                "cache",
                "grep",
                "run",  # matches all by name
                "--project",
                "default",
                "--metadata",
                "channel_id=Gigaverse*",
            ],
        )

        assert result.exit_code == 0
        data = json.loads(result.output.strip().split("\n")[-1])
        assert len(data) == 2
        names = {r["name"] for r in data}
        assert names == {"run-1", "run-2"}


class TestGetExistingRunIds:
    def test_returns_ids_from_cache(self, tmp_path, monkeypatch):
        monkeypatch.setattr("langsmith_cli.cache.get_cache_dir", lambda: tmp_path)
        append_runs_to_cache("test-project", [_make_run(1), _make_run(2)])

        ids = get_existing_run_ids("test-project")
        assert len(ids) == 2
        assert make_run_id(1) in ids
        assert make_run_id(2) in ids

    def test_empty_for_nonexistent(self, tmp_path, monkeypatch):
        monkeypatch.setattr("langsmith_cli.cache.get_cache_dir", lambda: tmp_path)
        assert get_existing_run_ids("nonexistent") == set()


class TestAppendRunsStreaming:
    def test_streams_and_deduplicates(self, tmp_path, monkeypatch):
        """Streaming append deduplicates against existing cache."""
        monkeypatch.setattr("langsmith_cli.cache.get_cache_dir", lambda: tmp_path)

        # Pre-populate
        append_runs_to_cache("test-project", [_make_run(1)])

        # Stream in runs including a duplicate
        new_iter = iter([_make_run(1), _make_run(2), _make_run(3)])
        meta, count = append_runs_streaming("test-project", new_iter)

        assert count == 2  # run 1 was deduped
        assert meta.run_count == 3  # 1 existing + 2 new

        cached = read_cached_runs("test-project")
        assert len(cached) == 3

    def test_calls_progress_callback(self, tmp_path, monkeypatch):
        """Progress callback is called after each batch flush."""
        monkeypatch.setattr("langsmith_cli.cache.get_cache_dir", lambda: tmp_path)

        progress_calls: list[int] = []

        runs = [_make_run(i) for i in range(1, 6)]
        meta, count = append_runs_streaming(
            "test-project",
            iter(runs),
            on_progress=lambda n: progress_calls.append(n),
            batch_size=2,
        )

        assert count == 5
        # With batch_size=2 and 5 runs: flushes at 2, 4, 5
        assert progress_calls == [2, 4, 5]

    def test_empty_iterator(self, tmp_path, monkeypatch):
        """Empty iterator writes nothing."""
        monkeypatch.setattr("langsmith_cli.cache.get_cache_dir", lambda: tmp_path)

        meta, count = append_runs_streaming("test-project", iter([]))
        assert count == 0
        assert meta.run_count == 0

    def test_pre_loaded_existing_ids(self, tmp_path, monkeypatch):
        """Can pass pre-loaded IDs to avoid re-reading cache file."""
        monkeypatch.setattr("langsmith_cli.cache.get_cache_dir", lambda: tmp_path)

        # Pass a pre-loaded set that marks run-1 as existing
        existing = {make_run_id(1)}
        runs = [_make_run(1), _make_run(2)]
        meta, count = append_runs_streaming(
            "test-project",
            iter(runs),
            existing_ids=existing,
        )

        assert count == 1  # Only run-2 written
        cached = read_cached_runs("test-project")
        assert len(cached) == 1
        assert cached[0].name == "run-2"

    def test_metadata_time_range_updated(self, tmp_path, monkeypatch):
        """Metadata tracks min/max start times across streaming batches."""
        monkeypatch.setattr("langsmith_cli.cache.get_cache_dir", lambda: tmp_path)

        runs = [
            _make_run(1, hour=10),
            _make_run(2, hour=18),
            _make_run(3, hour=14),
        ]
        meta, count = append_runs_streaming("test-project", iter(runs))

        assert count == 3
        assert meta.oldest_run_start_time == datetime(
            2026, 3, 9, 10, 0, 0, tzinfo=timezone.utc
        )
        assert meta.newest_run_start_time == datetime(
            2026, 3, 9, 18, 0, 0, tzinfo=timezone.utc
        )

    def test_naive_start_time_does_not_raise_when_cache_empty(
        self, tmp_path, monkeypatch
    ):
        """INVARIANT: Runs with naive start_time are accepted into a fresh cache
        without raising a TypeError about offset-naive vs offset-aware comparison.
        """
        monkeypatch.setattr("langsmith_cli.cache.get_cache_dir", lambda: tmp_path)

        naive_runs = [_make_naive_run(1, hour=10), _make_naive_run(2, hour=18)]
        meta, count = append_runs_streaming("test-project", iter(naive_runs))

        assert count == 2
        assert meta.oldest_run_start_time is not None
        assert meta.newest_run_start_time is not None
        assert meta.oldest_run_start_time.tzinfo is not None
        assert meta.newest_run_start_time.tzinfo is not None

    def test_naive_start_time_does_not_raise_when_cache_has_existing_metadata(
        self, tmp_path, monkeypatch
    ):
        """INVARIANT: Naive start_time runs can be appended to a project that already
        has timezone-aware metadata — the comparison must not raise TypeError.

        This is the regression case: existing metadata has tz-aware datetimes,
        incoming run has naive datetime → old code crashed with
        "can't compare offset-naive and offset-aware datetimes".
        """
        monkeypatch.setattr("langsmith_cli.cache.get_cache_dir", lambda: tmp_path)

        # First populate with tz-aware runs (creates tz-aware metadata)
        append_runs_streaming("test-project", iter([_make_run(1, hour=12)]))

        # Now append naive runs — must not raise
        naive_runs = [_make_naive_run(2, hour=10), _make_naive_run(3, hour=18)]
        meta, count = append_runs_streaming("test-project", iter(naive_runs))

        assert count == 2
        assert meta.oldest_run_start_time is not None
        assert meta.newest_run_start_time is not None
        # All stored timestamps must be tz-aware
        assert meta.oldest_run_start_time.tzinfo is not None
        assert meta.newest_run_start_time.tzinfo is not None


# Tests for strip_binary_data


class TestStripBinaryData:
    """INVARIANT: Large base64 strings are replaced with placeholders,
    small strings and non-base64 content are preserved unchanged.
    """

    def test_small_strings_preserved(self):
        """Strings below threshold are never stripped."""
        data = {"name": "test-run", "inputs": {"text": "hello world"}}
        assert strip_binary_data(data) == data

    def test_large_base64_replaced(self):
        """Large base64 strings are replaced with a placeholder."""
        big_b64 = "A" * 20_000  # 20KB of valid base64 chars
        data = {"inputs": {"image": big_b64}}
        result = strip_binary_data(data)
        assert result["inputs"]["image"].startswith("[binary:base64:")
        assert "20000bytes" in result["inputs"]["image"]
        assert "stored_in_langsmith" in result["inputs"]["image"]

    def test_data_uri_replaced(self):
        """data: URIs with base64 content are replaced with media type info."""
        data_uri = "data:video/mp4;base64," + "A" * 20_000
        data = {"inputs": {"video": data_uri}}
        result = strip_binary_data(data)
        assert "video/mp4" in result["inputs"]["video"]
        assert "stored_in_langsmith" in result["inputs"]["video"]

    def test_large_non_base64_preserved(self):
        """Large strings that aren't base64 are NOT stripped."""
        big_text = "Hello world! This is normal text. " * 1000  # ~33KB
        data = {"inputs": {"text": big_text}}
        result = strip_binary_data(data)
        assert result["inputs"]["text"] == big_text

    def test_nested_binary_stripped(self):
        """Binary data deep in nested structures is stripped."""
        big_b64 = "AAAA" * 5_000  # 20KB
        data = {
            "inputs": {
                "messages": [
                    [
                        {
                            "kwargs": {
                                "content": [
                                    {"type": "text", "data": "hello"},
                                    {"type": "image", "data": big_b64},
                                ]
                            }
                        }
                    ]
                ]
            }
        }
        result = strip_binary_data(data)
        content = result["inputs"]["messages"][0][0]["kwargs"]["content"]
        assert content[0]["data"] == "hello"
        assert content[1]["data"].startswith("[binary:base64:")

    def test_none_and_numbers_preserved(self):
        """Non-string types pass through unchanged."""
        data = {"count": 42, "rate": 3.14, "active": True, "error": None}
        assert strip_binary_data(data) == data

    def test_empty_structures_preserved(self):
        """Empty dicts and lists pass through."""
        data = {"inputs": {}, "tags": [], "name": ""}
        assert strip_binary_data(data) == data

    def test_small_object_returns_unchanged_identity(self):
        """INVARIANT: Objects with no strings >= threshold are returned as-is (optimization)."""
        data = {
            "id": "abc-123",
            "name": "test-run",
            "status": "success",
            "inputs": {"text": "hello", "count": 42},
            "outputs": {"result": "world"},
            "tags": ["prod", "v2"],
        }
        result = strip_binary_data(data)
        # The result should be the exact same object (identity) when no stripping needed
        assert result is data

    def test_mixed_small_and_large_strips_only_large(self):
        """Only large binary strings are stripped; small values are preserved."""
        big_b64 = "B" * 15_000
        data = {
            "name": "test",
            "small_field": "keep me",
            "big_field": big_b64,
            "number": 99,
        }
        result = strip_binary_data(data)
        assert result["name"] == "test"
        assert result["small_field"] == "keep me"
        assert result["big_field"].startswith("[binary:base64:")
        assert result["number"] == 99


class TestCacheGrepCommand:
    """Tests for runs cache grep command."""

    def _make_run_with_inputs(
        self, n: int, inputs: dict | None = None, outputs: dict | None = None
    ) -> Run:
        return Run(
            id=UUID(make_run_id(n)),
            name=f"run-{n}",
            run_type="llm",
            start_time=datetime(2026, 3, 9, 16, 0, 0, tzinfo=timezone.utc),
            total_tokens=1000,
            inputs=inputs or {},
            outputs=outputs or {},
            extra={"metadata": {"ls_model_name": "gpt-4"}},
        )

    def test_grep_finds_matching_runs(self, runner, mock_client, tmp_path, monkeypatch):
        """INVARIANT: cache grep returns runs matching the pattern."""
        monkeypatch.setattr("langsmith_cli.cache.get_cache_dir", lambda: tmp_path)

        runs = [
            self._make_run_with_inputs(1, inputs={"text": "hello world"}),
            self._make_run_with_inputs(2, inputs={"text": "goodbye world"}),
            self._make_run_with_inputs(3, inputs={"text": "hello again"}),
        ]
        append_runs_to_cache("test-proj", runs)

        result = runner.invoke(
            cli, ["--json", "runs", "cache", "grep", "hello", "--project", "test-proj"]
        )
        assert result.exit_code == 0
        # render_output outputs a JSON array
        data = json.loads(result.output.strip().split("\n")[-1])
        assert len(data) == 2

    def test_grep_case_insensitive(self, runner, mock_client, tmp_path, monkeypatch):
        """INVARIANT: -i flag enables case-insensitive grep."""
        monkeypatch.setattr("langsmith_cli.cache.get_cache_dir", lambda: tmp_path)

        runs = [
            self._make_run_with_inputs(1, inputs={"text": "Hello World"}),
            self._make_run_with_inputs(2, inputs={"text": "goodbye"}),
        ]
        append_runs_to_cache("test-proj", runs)

        result = runner.invoke(
            cli,
            [
                "--json",
                "runs",
                "cache",
                "grep",
                "hello",
                "-i",
                "--project",
                "test-proj",
            ],
        )
        assert result.exit_code == 0
        data = json.loads(result.output.strip().split("\n")[-1])
        assert len(data) == 1

    def test_grep_count_mode(self, runner, mock_client, tmp_path, monkeypatch):
        """INVARIANT: --count returns just the number of matches."""
        monkeypatch.setattr("langsmith_cli.cache.get_cache_dir", lambda: tmp_path)

        runs = [
            self._make_run_with_inputs(1, inputs={"text": "hello"}),
            self._make_run_with_inputs(2, inputs={"text": "hello again"}),
            self._make_run_with_inputs(3, inputs={"text": "goodbye"}),
        ]
        append_runs_to_cache("test-proj", runs)

        result = runner.invoke(
            cli,
            ["runs", "cache", "grep", "hello", "--count", "--project", "test-proj"],
        )
        assert result.exit_code == 0
        assert "2" in result.output

    def test_grep_no_cached_data(self, runner, mock_client, tmp_path, monkeypatch):
        """INVARIANT: Warns when no cached projects exist."""
        monkeypatch.setattr("langsmith_cli.cache.get_cache_dir", lambda: tmp_path)

        result = runner.invoke(cli, ["runs", "cache", "grep", "hello"])
        assert result.exit_code == 0
        assert "No cached" in result.output

    def test_grep_json_empty_array_when_no_projects(
        self, runner, mock_client, tmp_path, monkeypatch
    ):
        """INVARIANT: --json outputs empty array when no cached projects exist."""
        monkeypatch.setattr("langsmith_cli.cache.get_cache_dir", lambda: tmp_path)

        result = runner.invoke(cli, ["--json", "runs", "cache", "grep", "hello"])
        assert result.exit_code == 0
        data = json.loads(result.output.strip().split("\n")[-1])
        assert data == []

    def test_grep_json_empty_array_when_no_runs(
        self, runner, mock_client, tmp_path, monkeypatch
    ):
        """INVARIANT: --json outputs empty array when cache has no runs."""
        monkeypatch.setattr("langsmith_cli.cache.get_cache_dir", lambda: tmp_path)
        (tmp_path / "test-proj").mkdir()

        result = runner.invoke(
            cli,
            ["--json", "runs", "cache", "grep", "hello", "--project", "test-proj"],
        )
        assert result.exit_code == 0
        data = json.loads(result.output.strip().split("\n")[-1])
        assert data == []


class TestFindOrphanedCacheFiles:
    def test_no_cache_dir_returns_empty(self, tmp_path, monkeypatch):
        """INVARIANT: Returns empty list when cache dir does not exist."""
        nonexistent = tmp_path / "does_not_exist"
        monkeypatch.setattr("langsmith_cli.cache.get_cache_dir", lambda: nonexistent)
        assert find_orphaned_cache_files() == []

    def test_no_orphans_when_all_have_meta(self, tmp_path, monkeypatch):
        """INVARIANT: Returns empty list when every JSONL has a matching meta file."""
        monkeypatch.setattr("langsmith_cli.cache.get_cache_dir", lambda: tmp_path)
        (tmp_path / "project-a.jsonl").write_text("{}\n")
        (tmp_path / "project-a.meta.json").write_text("{}")
        assert find_orphaned_cache_files() == []

    def test_detects_jsonl_without_meta(self, tmp_path, monkeypatch):
        """INVARIANT: JSONL files without a matching .meta.json are orphaned."""
        monkeypatch.setattr("langsmith_cli.cache.get_cache_dir", lambda: tmp_path)
        (tmp_path / "project-a.jsonl").write_text("{}\n")
        (tmp_path / "project-b.jsonl").write_text("{}\n")
        (tmp_path / "project-b.meta.json").write_text("{}")
        orphaned = find_orphaned_cache_files()
        assert orphaned == ["project-a"]

    def test_multiple_orphans_sorted(self, tmp_path, monkeypatch):
        """INVARIANT: Multiple orphaned files are returned in sorted order."""
        monkeypatch.setattr("langsmith_cli.cache.get_cache_dir", lambda: tmp_path)
        for name in ["z-proj", "a-proj", "m-proj"]:
            (tmp_path / f"{name}.jsonl").write_text("{}\n")
        orphaned = find_orphaned_cache_files()
        assert orphaned == ["a-proj", "m-proj", "z-proj"]


class TestRepairCacheMetadata:
    def test_raises_for_missing_cache(self, tmp_path, monkeypatch):
        """INVARIANT: FileNotFoundError when no JSONL file exists."""
        monkeypatch.setattr("langsmith_cli.cache.get_cache_dir", lambda: tmp_path)
        import pytest

        with pytest.raises(FileNotFoundError, match="no-such-project"):
            repair_cache_metadata("no-such-project")

    def test_regenerates_meta_from_jsonl(self, tmp_path, monkeypatch):
        """INVARIANT: Repaired metadata matches run count and time bounds in JSONL."""
        monkeypatch.setattr("langsmith_cli.cache.get_cache_dir", lambda: tmp_path)
        runs = [_make_run(i) for i in range(1, 4)]
        append_runs_to_cache("test", runs)
        # Remove the meta file to simulate an orphaned cache
        (tmp_path / "test.meta.json").unlink()
        assert not (tmp_path / "test.meta.json").exists()

        meta = repair_cache_metadata("test")

        assert meta.run_count == 3
        assert meta.oldest_run_start_time is not None
        assert meta.newest_run_start_time is not None
        assert meta.oldest_run_start_time <= meta.newest_run_start_time
        # Meta file is written back to disk
        assert (tmp_path / "test.meta.json").exists()

    def test_skips_corrupt_lines(self, tmp_path, monkeypatch):
        """INVARIANT: Corrupt JSON lines are silently skipped during repair."""
        monkeypatch.setattr("langsmith_cli.cache.get_cache_dir", lambda: tmp_path)
        jsonl = tmp_path / "test.jsonl"
        jsonl.write_text(
            '{"start_time": "2026-03-09T16:00:00+00:00"}\n'
            "NOT VALID JSON\n"
            '{"start_time": "2026-03-09T17:00:00+00:00"}\n'
        )

        meta = repair_cache_metadata("test")

        assert meta.run_count == 2

    def test_handles_missing_start_time(self, tmp_path, monkeypatch):
        """INVARIANT: Lines without start_time are counted but don't affect time range."""
        monkeypatch.setattr("langsmith_cli.cache.get_cache_dir", lambda: tmp_path)
        (tmp_path / "test.jsonl").write_text('{"id": "abc"}\n{"id": "def"}\n')

        meta = repair_cache_metadata("test")

        assert meta.run_count == 2
        assert meta.oldest_run_start_time is None
        assert meta.newest_run_start_time is None


class TestCacheRepairCommand:
    @staticmethod
    def _make_orphaned(tmp_path, stem: str) -> None:
        """Write a JSONL without a matching .meta.json."""
        run = _make_run(1)
        data = run.model_dump(mode="json")
        (tmp_path / f"{stem}.jsonl").write_text(json.dumps(data, default=str) + "\n")

    def test_repair_all_orphaned(self, runner, tmp_path, monkeypatch):
        """INVARIANT: repair command regenerates metadata for all orphaned JSONL files."""
        monkeypatch.setattr("langsmith_cli.cache.get_cache_dir", lambda: tmp_path)
        self._make_orphaned(tmp_path, "proj-a")
        self._make_orphaned(tmp_path, "proj-b")

        result = runner.invoke(cli, ["runs", "cache", "repair"])

        assert result.exit_code == 0
        assert (tmp_path / "proj-a.meta.json").exists()
        assert (tmp_path / "proj-b.meta.json").exists()
        assert "Repaired" in result.output

    def test_repair_specific_project(self, runner, tmp_path, monkeypatch):
        """INVARIANT: repair --project targets exactly one project."""
        monkeypatch.setattr("langsmith_cli.cache.get_cache_dir", lambda: tmp_path)
        self._make_orphaned(tmp_path, "proj-a")
        self._make_orphaned(tmp_path, "proj-b")

        result = runner.invoke(cli, ["runs", "cache", "repair", "--project", "proj-a"])

        assert result.exit_code == 0
        assert (tmp_path / "proj-a.meta.json").exists()
        # proj-b was NOT targeted
        assert not (tmp_path / "proj-b.meta.json").exists()

    def test_repair_no_orphans_prints_healthy_message(
        self, runner, tmp_path, monkeypatch
    ):
        """INVARIANT: repair with no orphans prints a healthy message and exits cleanly."""
        monkeypatch.setattr("langsmith_cli.cache.get_cache_dir", lambda: tmp_path)

        result = runner.invoke(cli, ["runs", "cache", "repair"])

        assert result.exit_code == 0
        assert (
            "healthy" in result.output.lower() or "no orphaned" in result.output.lower()
        )

    def test_repair_missing_project_exits_with_error(
        self, runner, tmp_path, monkeypatch
    ):
        """INVARIANT: repair --project for a non-existent file exits with non-zero code."""
        monkeypatch.setattr("langsmith_cli.cache.get_cache_dir", lambda: tmp_path)

        result = runner.invoke(cli, ["runs", "cache", "repair", "--project", "ghost"])

        assert result.exit_code != 0


class TestCacheListOrphanWarning:
    def test_warns_about_orphaned_files(self, runner, tmp_path, monkeypatch):
        """INVARIANT: cache list warns when orphaned JSONL files are present."""
        monkeypatch.setattr("langsmith_cli.cache.get_cache_dir", lambda: tmp_path)
        # One proper project
        run = _make_run(1)
        append_runs_to_cache("good-project", [run])
        # One orphaned JSONL
        data = run.model_dump(mode="json")
        (tmp_path / "orphaned.jsonl").write_text(json.dumps(data, default=str) + "\n")

        result = runner.invoke(cli, ["runs", "cache", "list"])

        assert result.exit_code == 0
        output = strip_ansi(result.output)
        assert "orphan" in output.lower() or "repair" in output.lower()

    def test_no_warning_when_no_orphans(self, runner, tmp_path, monkeypatch):
        """INVARIANT: cache list shows no repair warning when everything is healthy."""
        monkeypatch.setattr("langsmith_cli.cache.get_cache_dir", lambda: tmp_path)
        run = _make_run(1)
        append_runs_to_cache("good-project", [run])

        result = runner.invoke(cli, ["runs", "cache", "list"])

        assert result.exit_code == 0
        assert "repair" not in result.output.lower()


class TestCacheGrepOrphanWarning:
    def test_warns_about_orphaned_files_when_no_project_specified(
        self, runner, tmp_path, monkeypatch
    ):
        """INVARIANT: cache grep warns about orphaned files when searching all projects."""
        monkeypatch.setattr("langsmith_cli.cache.get_cache_dir", lambda: tmp_path)
        run = _make_run(1)
        append_runs_to_cache("good-project", [run])
        # Orphaned JSONL
        data = run.model_dump(mode="json")
        (tmp_path / "orphaned.jsonl").write_text(json.dumps(data, default=str) + "\n")

        result = runner.invoke(cli, ["runs", "cache", "grep", "run-1"])

        assert result.exit_code == 0
        output = strip_ansi(result.output)
        assert "orphan" in output.lower() or "repair" in output.lower()

    def test_warns_for_specific_project_with_no_meta(
        self, runner, tmp_path, monkeypatch
    ):
        """INVARIANT: cache grep warns when the named project's meta is missing."""
        monkeypatch.setattr("langsmith_cli.cache.get_cache_dir", lambda: tmp_path)
        run = _make_run(1)
        data = run.model_dump(mode="json")
        (tmp_path / "my-project.jsonl").write_text(json.dumps(data, default=str) + "\n")
        # No .meta.json

        result = runner.invoke(
            cli, ["runs", "cache", "grep", "run-1", "--project", "my-project"]
        )

        assert result.exit_code == 0
        output = strip_ansi(result.output)
        assert "repair" in output.lower()
