"""Compatibility tests for the single Parquet local trace cache."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from conftest import create_run, parse_json_output, strip_ansi
from langsmith_cli.cache import (
    append_runs_streaming,
    append_runs_to_cache,
    clear_cache,
    get_existing_run_ids,
    list_cached_projects,
    load_runs_from_cache,
    read_cached_runs,
    sample_cached_rows,
)
from langsmith_cli.main import cli


PROJECT_ID = "f47ac10b-58cc-4372-a567-0e02b2c3d479"
PROJECT_NAME = "dev/cache-compat"
FIRST_RUN_ID = "20000000-0000-0000-0000-000000000001"
SECOND_RUN_ID = "20000000-0000-0000-0000-000000000002"


def _run(run_id: str, name: str, *, outputs: dict | None = None):
    return create_run(
        id_str=run_id,
        name=name,
        outputs=outputs,
        session_id=PROJECT_ID,
    )


@pytest.fixture
def cache_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr("langsmith_cli.cache.get_cache_dir", lambda: tmp_path)
    monkeypatch.setenv("LANGSMITH_CLI_RUN_CACHE_DIR", str(tmp_path))
    return tmp_path


def test_append_and_read_use_parquet_only(cache_root: Path) -> None:
    metadata = append_runs_to_cache(
        PROJECT_NAME,
        [_run(FIRST_RUN_ID, "first"), _run(SECOND_RUN_ID, "second")],
    )

    assert metadata.run_count == 2
    assert {str(run.id) for run in read_cached_runs(PROJECT_NAME)} == {
        FIRST_RUN_ID,
        SECOND_RUN_ID,
    }
    assert list(cache_root.rglob("*.parquet"))
    assert list(cache_root.rglob("*.jsonl")) == []


def test_streaming_wrapper_uses_repository_deduplication(cache_root: Path) -> None:
    progress: list[int] = []
    runs = [_run(FIRST_RUN_ID, "first"), _run(FIRST_RUN_ID, "first")]

    _metadata, first_added = append_runs_streaming(
        PROJECT_NAME,
        iter(runs),
        batch_size=1,
        on_progress=progress.append,
    )
    _metadata, repeated_added = append_runs_streaming(PROJECT_NAME, iter([runs[0]]))

    assert first_added == 1
    assert repeated_added == 0
    assert progress == [1, 1]
    assert get_existing_run_ids(PROJECT_NAME) == {FIRST_RUN_ID}


def test_list_load_sample_and_clear_share_one_inventory(cache_root: Path) -> None:
    append_runs_to_cache(PROJECT_NAME, [_run(FIRST_RUN_ID, "first")])

    projects = list_cached_projects()
    loaded = load_runs_from_cache([PROJECT_NAME, "missing"])
    samples = sample_cached_rows(PROJECT_NAME, 1)

    assert [(project.project_name, project.run_count) for project in projects] == [
        (PROJECT_NAME, 1)
    ]
    assert [str(run.id) for run in loaded.items] == [FIRST_RUN_ID]
    assert loaded.failed_sources == [
        ("missing", "Not cached. Run 'runs pull --to local' first.")
    ]
    assert samples[0]["id"] == FIRST_RUN_ID
    assert clear_cache(PROJECT_NAME) == 1
    assert read_cached_runs(PROJECT_NAME) == []


def test_read_cached_runs_applies_time_bounds(cache_root: Path) -> None:
    append_runs_to_cache(PROJECT_NAME, [_run(FIRST_RUN_ID, "first")])
    start = datetime(2024, 7, 3, 9, 27, 16, tzinfo=timezone.utc)

    assert len(read_cached_runs(PROJECT_NAME, since=start - timedelta(seconds=1))) == 1
    assert read_cached_runs(PROJECT_NAME, since=start + timedelta(seconds=1)) == []


def test_cache_download_is_compatibility_alias_for_local_inventory(
    runner, mock_client, cache_root: Path
) -> None:
    mock_client.list_runs.return_value = [_run(FIRST_RUN_ID, "downloaded")]

    result = runner.invoke(
        cli,
        ["--json", "runs", "cache", "download", "--project", PROJECT_NAME],
    )

    assert result.exit_code == 0, result.output
    payload = parse_json_output(result.output)
    assert payload[0]["project"] == PROJECT_NAME
    assert payload[0]["result"]["added_run_count"] == 1
    assert [str(run.id) for run in read_cached_runs(PROJECT_NAME)] == [FIRST_RUN_ID]
    assert list(cache_root.rglob("*.jsonl")) == []


@pytest.mark.parametrize("legacy_option", ["--full", "--workers=4"])
def test_cache_download_rejects_legacy_options_it_cannot_honor(
    runner, mock_client, legacy_option: str
) -> None:
    """Compatibility flags must never be accepted and silently ignored."""
    result = runner.invoke(
        cli,
        [
            "runs",
            "cache",
            "download",
            "--project",
            PROJECT_NAME,
            legacy_option,
        ],
    )

    assert result.exit_code != 0
    assert "not supported by the additive Parquet cache" in result.output
    mock_client.assert_not_called()


def test_cache_download_applies_supported_selection_filters(
    runner, mock_client, cache_root: Path
) -> None:
    quoted_name = 'quoted"name'
    run = create_run(
        id_str=FIRST_RUN_ID,
        name=quoted_name,
        run_type="chain",
        metadata={"team": "production"},
        session_id=PROJECT_ID,
    )
    mock_client.list_runs.return_value = [run]

    exact = runner.invoke(
        cli,
        [
            "--json",
            "runs",
            "cache",
            "download",
            "--project",
            PROJECT_NAME,
            "--filter",
            'has(tags, "important")',
            "--run-type",
            "chain",
            "--name-pattern",
            quoted_name,
        ],
    )
    wildcard = runner.invoke(
        cli,
        [
            "runs",
            "cache",
            "download",
            "--project",
            PROJECT_NAME,
            "--name-pattern",
            "*quoted*",
            "--metadata",
            "team=prod*",
        ],
    )

    assert exact.exit_code == 0, exact.output
    selection_filter = mock_client.list_runs.call_args_list[0].kwargs["filter"]
    assert 'has(tags, "important")' in selection_filter
    assert 'eq(run_type, "chain")' in selection_filter
    assert 'eq(name, "quoted\\"name")' in selection_filter
    assert wildcard.exit_code == 0, wildcard.output
    assert f"{PROJECT_NAME}: added" in strip_ansi(wildcard.output)
    assert list(cache_root.rglob("*.jsonl")) == []


def test_cache_download_fails_for_project_id_and_mixed_project_expansion(
    runner, mock_client
) -> None:
    project_id_result = runner.invoke(
        cli,
        [
            "runs",
            "cache",
            "download",
            "--project-id",
            PROJECT_ID,
        ],
    )
    assert project_id_result.exit_code != 0
    assert "requires a project name" in project_id_result.output

    other_project_id = "f47ac10b-58cc-4372-a567-0e02b2c3d480"
    mock_client.list_runs.side_effect = [
        [_run(FIRST_RUN_ID, "selected")],
        [
            create_run(
                id_str=SECOND_RUN_ID,
                name="wrong project",
                session_id=other_project_id,
            )
        ],
    ]
    mixed_result = runner.invoke(
        cli,
        ["runs", "cache", "download", "--project", PROJECT_NAME],
    )

    assert mixed_result.exit_code != 0
    assert "do not identify exactly one project" in mixed_result.output


def test_cache_grep_delegates_to_runs_list_local(runner, cache_root: Path) -> None:
    append_runs_to_cache(
        PROJECT_NAME,
        [_run(FIRST_RUN_ID, "matching", outputs={"message": "needle"})],
    )

    result = runner.invoke(
        cli,
        [
            "--json",
            "runs",
            "cache",
            "grep",
            "needle",
            "--project",
            PROJECT_NAME,
        ],
    )

    assert result.exit_code == 0, result.output
    assert parse_json_output(result.output)[0]["id"] == FIRST_RUN_ID


def test_cache_list_schema_repair_and_clear_commands(runner, cache_root: Path) -> None:
    append_runs_to_cache(
        PROJECT_NAME,
        [_run(FIRST_RUN_ID, "schema", outputs={"answer": "yes"})],
    )

    listed = runner.invoke(cli, ["--json", "runs", "cache", "list"])
    schema = runner.invoke(
        cli,
        ["--json", "runs", "cache", "schema", "--project", PROJECT_NAME],
    )
    repaired = runner.invoke(cli, ["--json", "runs", "cache", "repair"])
    cleared = runner.invoke(
        cli, ["--json", "runs", "cache", "clear", "--project", PROJECT_NAME, "--yes"]
    )

    assert parse_json_output(listed.output)[0]["project_name"] == PROJECT_NAME
    assert "outputs" in parse_json_output(schema.output)
    assert parse_json_output(repaired.output) == {
        "status": "healthy",
        "fragment_count": 1,
        "run_count": 1,
    }
    assert parse_json_output(cleared.output)["removed_run_count"] == 1


def test_cache_human_and_alternate_output_paths(
    runner, cache_root: Path, tmp_path: Path
) -> None:
    empty = runner.invoke(cli, ["runs", "cache", "list"])
    assert empty.exit_code == 0
    assert "No local traces" in empty.output

    append_runs_to_cache(
        PROJECT_NAME,
        [_run(FIRST_RUN_ID, "schema", outputs={"answer": "yes"})],
    )
    table = runner.invoke(cli, ["runs", "cache", "list"])
    count = runner.invoke(cli, ["runs", "cache", "list", "--count"])
    yaml_result = runner.invoke(cli, ["runs", "cache", "list", "--format", "yaml"])
    output_path = tmp_path / "cache-projects.json"
    written = runner.invoke(
        cli,
        [
            "runs",
            "cache",
            "list",
            "--format",
            "json",
            "--output",
            str(output_path),
        ],
    )
    repaired = runner.invoke(cli, ["runs", "cache", "repair"])
    schema = runner.invoke(
        cli,
        [
            "runs",
            "cache",
            "schema",
            "--project",
            PROJECT_NAME,
            "--include",
            "outputs",
        ],
    )
    missing_schema = runner.invoke(
        cli,
        ["runs", "cache", "schema", "--project", "not-cached"],
    )
    cleared = runner.invoke(
        cli, ["runs", "cache", "clear", "--project", PROJECT_NAME, "--yes"]
    )

    assert table.exit_code == 0
    assert PROJECT_NAME in table.output
    assert count.output.strip() == "1"
    assert yaml_result.exit_code == 0
    assert "project_name:" in yaml_result.output
    assert written.exit_code == 0
    assert PROJECT_NAME in output_path.read_text(encoding="utf-8")
    assert "Healthy: 1 logical run" in strip_ansi(repaired.output)
    assert "outputs:" in schema.output
    assert missing_schema.exit_code != 0
    assert "No cache found" in missing_schema.output
    assert "Evicted 1 run" in strip_ansi(cleared.output)


def test_cache_dir_reports_shared_root(runner, cache_root: Path) -> None:
    result = runner.invoke(cli, ["runs", "cache", "dir"])

    assert result.exit_code == 0
    assert result.output.strip() == str(cache_root)


def test_usage_and_pricing_loader_reads_shared_parquet_inventory(
    cache_root: Path,
) -> None:
    """Legacy analysis commands consume the same inventory as --source local."""
    from langsmith_cli.cache import load_runs_from_cache
    from langsmith_cli.local_traces.repository import LocalTraceRepository
    from langsmith_cli.trace_query import RunQuery

    append_runs_to_cache(PROJECT_NAME, [_run(FIRST_RUN_ID, "analysis")])

    loaded = load_runs_from_cache([PROJECT_NAME])
    local = LocalTraceRepository(cache_root).query(
        RunQuery(project=PROJECT_NAME, limit=None)
    )

    assert [str(run.id) for run in loaded.items] == [FIRST_RUN_ID]
    assert [str(run.id) for run in local] == [FIRST_RUN_ID]
    assert loaded.successful_sources == [PROJECT_NAME]
    assert list(cache_root.rglob("*.jsonl")) == []
