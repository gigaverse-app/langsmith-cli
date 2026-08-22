"""Local trace inventory and uniform-source user journeys."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from conftest import create_run, parse_json_output
from langsmith_cli.archive.storage import LocalArchiveStore
from langsmith_cli.local_traces.models import TracePullRequest, TraceSource
from langsmith_cli.local_traces.repository import LocalTraceRepository
from langsmith_cli.main import cli
from langsmith_cli.trace_query import RunQuery


PROJECT_ID = "f47ac10b-58cc-4372-a567-0e02b2c3d479"
PROJECT_NAME = "dev/local-traces"
FIRST_RUN_ID = "10000000-0000-0000-0000-000000000001"
SECOND_RUN_ID = "10000000-0000-0000-0000-000000000002"
OBSERVED_AT = datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc)


def _pull_request(source: TraceSource = TraceSource.CLOUD) -> TracePullRequest:
    return TracePullRequest(
        source=source,
        project_id=PROJECT_ID,
        project_name=PROJECT_NAME,
        requested_at=OBSERVED_AT,
    )


def _run(run_id: str, name: str, *, outputs: dict[str, str] | None = None):
    return create_run(
        id_str=run_id,
        name=name,
        outputs=outputs,
        session_id=PROJECT_ID,
    )


def test_local_addition_preserves_unrelated_traces(tmp_path: Path) -> None:
    """INVARIANT: an explicit pull adds to the working set; it never replaces it."""
    repository = LocalTraceRepository(tmp_path)

    first = repository.add_runs(_pull_request(), [_run(FIRST_RUN_ID, "first")])
    second = repository.add_runs(_pull_request(), [_run(SECOND_RUN_ID, "second")])

    assert first.added_run_count == 1
    assert second.added_run_count == 1
    assert second.total_run_count == 2
    assert [str(run.id) for run in repository.query(RunQuery(limit=None))] == [
        FIRST_RUN_ID,
        SECOND_RUN_ID,
    ]


def test_repeated_pull_is_idempotent(tmp_path: Path) -> None:
    """INVARIANT: identical observations create one active fragment and one run."""
    repository = LocalTraceRepository(tmp_path)
    request = _pull_request()
    run = _run(FIRST_RUN_ID, "same")

    first = repository.add_runs(request, [run])
    repeated = repository.add_runs(request, [run])

    assert first.added_run_count == 1
    assert repeated.added_run_count == 0
    assert repeated.total_run_count == 1
    assert len(repository.read_catalog().fragments) == 1


def test_newer_observation_wins_without_duplicate_run_ids(tmp_path: Path) -> None:
    """INVARIANT: overlapping immutable fragments expose one newest logical run."""
    repository = LocalTraceRepository(tmp_path)
    repository.add_runs(
        _pull_request(), [_run(FIRST_RUN_ID, "run", outputs={"version": "old"})]
    )
    newer_request = _pull_request().model_copy(
        update={"requested_at": OBSERVED_AT + timedelta(minutes=1)}
    )

    repository.add_runs(
        newer_request,
        [_run(FIRST_RUN_ID, "run", outputs={"version": "new"})],
    )

    runs = repository.query(RunQuery(limit=None))
    assert len(runs) == 1
    assert runs[0].outputs == {"version": "new"}


def test_interrupted_fragment_write_does_not_change_catalog(tmp_path: Path) -> None:
    """INVARIANT: only the catalog CAS makes a staged immutable fragment visible."""
    repository = LocalTraceRepository(tmp_path)
    repository.add_runs(_pull_request(), [_run(FIRST_RUN_ID, "existing")])
    catalog_before = repository.read_catalog()

    with (
        patch.object(
            LocalArchiveStore,
            "put_text_if_version",
            side_effect=OSError("simulated catalog write failure"),
        ),
        pytest.raises(OSError, match="simulated catalog write failure"),
    ):
        repository.add_runs(_pull_request(), [_run(SECOND_RUN_ID, "not-visible")])

    assert repository.read_catalog() == catalog_before
    assert [str(run.id) for run in repository.query(RunQuery(limit=None))] == [
        FIRST_RUN_ID
    ]


def test_local_reads_do_not_create_or_mutate_cache_files(tmp_path: Path) -> None:
    """INVARIANT: local queries are never read-through cache operations."""
    repository = LocalTraceRepository(tmp_path)
    assert repository.query(RunQuery(limit=None)) == []
    assert list(tmp_path.iterdir()) == []

    repository.add_runs(_pull_request(), [_run(FIRST_RUN_ID, "stored")])
    before = {
        path.relative_to(tmp_path): (path.stat().st_mtime_ns, path.stat().st_size)
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    repository.query(RunQuery(limit=None))
    after = {
        path.relative_to(tmp_path): (path.stat().st_mtime_ns, path.stat().st_size)
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    assert after == before


def test_catalog_rejects_paths_outside_the_cache_root(tmp_path: Path) -> None:
    """INVARIANT: catalog metadata cannot make DuckDB scan arbitrary local files."""
    repository = LocalTraceRepository(tmp_path)
    repository.add_runs(_pull_request(), [_run(FIRST_RUN_ID, "stored")])
    catalog_path = tmp_path / "traces" / "catalog.json"
    payload = json.loads(catalog_path.read_text(encoding="utf-8"))
    payload["fragments"][0]["key"] = "../outside.parquet"
    catalog_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="outside the local trace cache"):
        repository.query(RunQuery(limit=None))


def test_runs_pull_cloud_then_list_local_offline(
    runner, mock_client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A user explicitly pulls once, then standard local queries require no client."""
    monkeypatch.setenv("LANGSMITH_CLI_RUN_CACHE_DIR", str(tmp_path))
    mock_client.list_runs.return_value = [_run(FIRST_RUN_ID, "offline")]

    pulled = runner.invoke(
        cli,
        [
            "--json",
            "runs",
            "pull",
            "--source",
            "cloud",
            "--to",
            "local",
            "--project",
            PROJECT_NAME,
            "--last",
            "7d",
        ],
    )
    assert pulled.exit_code == 0, pulled.output
    assert parse_json_output(pulled.output)["added_run_count"] == 1

    mock_client.reset_mock()
    with patch("langsmith.Client", side_effect=AssertionError("network access")):
        listed = runner.invoke(
            cli,
            [
                "--json",
                "runs",
                "list",
                "--source",
                "local",
                "--project",
                PROJECT_NAME,
            ],
        )

    assert listed.exit_code == 0, listed.output
    assert parse_json_output(listed.output)[0]["id"] == FIRST_RUN_ID
    assert mock_client.mock_calls == []
    assert list(tmp_path.rglob("*.parquet"))
    assert list(tmp_path.rglob("*.jsonl")) == []


def test_source_and_archive_alias_conflict_fails_fast(runner, mock_client) -> None:
    result = runner.invoke(cli, ["runs", "list", "--source", "local", "--archive"])

    assert result.exit_code != 0
    assert "Use either --source or --archive" in result.output
    mock_client.assert_not_called()
