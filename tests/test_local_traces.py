"""Local trace inventory and uniform-source user journeys."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from threading import Barrier
from typing import Any, Iterator
from unittest.mock import patch

import pytest
from langsmith.schemas import Run

from conftest import create_run, parse_json_output, strip_ansi
from langsmith_cli.archive.storage import LocalArchiveStore
from langsmith_cli.archive.models import ArchivePhase
from langsmith_cli.archive.sync import sync_project_day
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


def test_local_trace_models_reject_invalid_identity_time_and_catalog_state() -> None:
    """Every persisted boundary fails fast instead of normalizing invalid state."""
    from pydantic import ValidationError

    from langsmith_cli.local_traces.models import (
        TraceCatalog,
        TraceFragment,
        TraceSelection,
    )

    naive = datetime(2026, 8, 22, 12, 0)
    with pytest.raises(ValidationError, match="timezone-aware"):
        TracePullRequest.model_validate(
            _pull_request().model_dump() | {"requested_at": naive}
        )
    with pytest.raises(ValidationError, match="originate outside local"):
        TracePullRequest.model_validate(
            _pull_request().model_dump() | {"source": TraceSource.LOCAL}
        )
    with pytest.raises(ValidationError, match="stable project ID and name"):
        TracePullRequest.model_validate(
            _pull_request().model_dump() | {"project_id": ""}
        )
    with pytest.raises(ValidationError, match="since must be earlier"):
        TracePullRequest.model_validate(
            _pull_request().model_dump() | {"since": OBSERVED_AT, "before": OBSERVED_AT}
        )

    valid_selection = {
        "source": TraceSource.CLOUD,
        "project_name": PROJECT_NAME,
        "requested_at": OBSERVED_AT,
    }
    with pytest.raises(ValidationError, match="timezone-aware"):
        TraceSelection.model_validate(valid_selection | {"requested_at": naive})
    with pytest.raises(ValidationError, match="non-negative"):
        TraceSelection.model_validate(valid_selection | {"limit": -1})
    with pytest.raises(ValidationError, match="destination"):
        TraceSelection.model_validate(valid_selection | {"source": TraceSource.LOCAL})
    with pytest.raises(ValidationError, match="exact project name"):
        TraceSelection.model_validate(valid_selection | {"project_name": ""})
    with pytest.raises(ValidationError, match="since must be earlier"):
        TraceSelection.model_validate(
            valid_selection | {"since": OBSERVED_AT, "before": OBSERVED_AT}
        )

    valid_fragment = {
        "key": "traces/fragments/fragment.parquet",
        "sha256": "a" * 64,
        "content_digest": "b" * 64,
        "row_count": 1,
        "project_id": PROJECT_ID,
        "project_name": PROJECT_NAME,
        "origin": TraceSource.CLOUD,
        "observed_at": OBSERVED_AT,
    }
    with pytest.raises(ValidationError, match="lowercase SHA-256"):
        TraceFragment.model_validate(valid_fragment | {"sha256": "INVALID"})
    with pytest.raises(ValidationError, match="at least one row"):
        TraceFragment.model_validate(valid_fragment | {"row_count": 0})
    with pytest.raises(ValidationError, match="timezone-aware"):
        TraceFragment.model_validate(valid_fragment | {"observed_at": naive})
    fragment = TraceFragment.model_validate(valid_fragment)
    with pytest.raises(ValidationError, match="Unsupported local trace catalog"):
        TraceCatalog(schema_version=2)
    with pytest.raises(ValidationError, match="duplicate fragments"):
        TraceCatalog(fragments=(fragment, fragment))


def test_empty_addition_records_pull_without_creating_a_fragment(
    tmp_path: Path,
) -> None:
    repository = LocalTraceRepository(tmp_path)

    result = repository.add_runs(_pull_request(), [])

    assert result.added_run_count == 0
    assert result.fragment_count == 0
    assert len(repository.read_catalog().pulls) == 1


def test_repository_fail_fast_and_eviction_edges(tmp_path: Path) -> None:
    repository = LocalTraceRepository(tmp_path)
    with pytest.raises(LookupError, match="Local run not found"):
        repository.get(FIRST_RUN_ID, follow_children=False)
    with pytest.raises(ValueError, match="session_id does not match"):
        repository.add_runs(
            _pull_request(),
            [
                create_run(
                    id_str=FIRST_RUN_ID,
                    session_id="f47ac10b-58cc-4372-a567-0e02b2c3d480",
                )
            ],
        )

    repository.add_runs(_pull_request(), [_run(FIRST_RUN_ID, "stored")])
    assert repository.query(RunQuery(project_name_pattern="prod/*", limit=None)) == []
    assert repository.query(RunQuery(project_name_regex="^prod/", limit=None)) == []
    evicted = repository.evict()
    repeated = repository.evict()
    assert evicted.remaining_run_count == 0
    assert repeated.removed_run_count == 0

    missing_repository = LocalTraceRepository(tmp_path / "missing")
    missing_repository.add_runs(_pull_request(), [_run(FIRST_RUN_ID, "missing")])
    fragment = missing_repository.read_catalog().fragments[0]
    (tmp_path / "missing" / fragment.key).unlink()
    with pytest.raises(ValueError, match="fragment is missing"):
        missing_repository.query(RunQuery(limit=None))


class _ArchiveRunsClient:
    def __init__(self, runs: list[Run]) -> None:
        self._runs = runs

    def list_runs(self, **kwargs: Any) -> Iterator[Run]:
        return iter(self._runs)


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


def test_concurrent_catalog_additions_merge(tmp_path: Path) -> None:
    """INVARIANT: stale writers merge independent fragments instead of replacing."""
    barrier = Barrier(2)

    def add(run_id: str) -> None:
        barrier.wait()
        LocalTraceRepository(tmp_path).add_runs(
            _pull_request(), [_run(run_id, f"run-{run_id[-1]}")]
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(add, FIRST_RUN_ID),
            executor.submit(add, SECOND_RUN_ID),
        ]
        for future in futures:
            future.result()

    assert {
        str(run.id)
        for run in LocalTraceRepository(tmp_path).query(RunQuery(limit=None))
    } == {
        FIRST_RUN_ID,
        SECOND_RUN_ID,
    }


def test_local_duckdb_disables_external_access_and_extension_autoload(
    tmp_path: Path,
) -> None:
    """INVARIANT: an offline local query cannot reach unapproved external state."""
    from langsmith_cli.archive.duckdb import archive_duckdb_connection

    approved = tmp_path / "approved.parquet"
    approved.touch()
    with archive_duckdb_connection(allowed_paths=[approved]) as connection:
        settings = dict(
            connection.execute(
                "SELECT name, value FROM duckdb_settings() WHERE name IN ("
                "'enable_external_access', 'autoinstall_known_extensions', "
                "'autoload_known_extensions')"
            ).fetchall()
        )

    assert settings == {
        "autoinstall_known_extensions": "false",
        "autoload_known_extensions": "false",
        "enable_external_access": "false",
    }


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


def test_empty_cloud_pull_is_a_successful_noop(
    runner, mock_client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Selecting no remote runs is normal and must not invent project identity."""
    monkeypatch.setenv("LANGSMITH_CLI_RUN_CACHE_DIR", str(tmp_path))
    mock_client.list_runs.return_value = []

    result = runner.invoke(
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
        ],
    )

    assert result.exit_code == 0, result.output
    assert parse_json_output(result.output) == {
        "added_run_count": 0,
        "selected_run_count": 0,
        "total_run_count": 0,
        "fragment_count": 0,
        "content_digest": None,
    }
    assert list(tmp_path.rglob("*")) == []


def test_pull_human_output_and_archive_filter_failure(
    runner, mock_client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LANGSMITH_CLI_RUN_CACHE_DIR", str(tmp_path))
    mock_client.list_runs.return_value = [_run(FIRST_RUN_ID, "human")]

    human = runner.invoke(
        cli,
        [
            "runs",
            "pull",
            "--source",
            "cloud",
            "--to",
            "local",
            "--project",
            PROJECT_NAME,
        ],
    )
    unsupported = runner.invoke(
        cli,
        [
            "runs",
            "pull",
            "--source",
            "archive",
            "--to",
            "local",
            "--project",
            PROJECT_NAME,
            "--filter",
            'eq(name, "x")',
        ],
    )

    assert human.exit_code == 0, human.output
    assert "Added 1 run(s) from cloud" in strip_ansi(human.output)
    assert unsupported.exit_code != 0
    assert "Archive pulls do not support raw FQL filters" in unsupported.output


def test_source_and_archive_alias_conflict_fails_fast(runner, mock_client) -> None:
    result = runner.invoke(cli, ["runs", "list", "--source", "local", "--archive"])

    assert result.exit_code != 0
    assert "Use either --source or --archive" in result.output
    mock_client.assert_not_called()


def test_local_backend_rejects_unsupported_fql(runner, mock_client) -> None:
    result = runner.invoke(
        cli,
        ["runs", "list", "--source", "local", "--filter", 'eq(name, "x")'],
    )

    assert result.exit_code != 0
    assert "Local backend does not yet support: --filter" in result.output
    mock_client.assert_not_called()


def test_runs_search_delegates_to_the_local_source(
    runner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LANGSMITH_CLI_RUN_CACHE_DIR", str(tmp_path))
    LocalTraceRepository(tmp_path).add_runs(
        _pull_request(),
        [_run(FIRST_RUN_ID, "searchable", outputs={"message": "local needle"})],
    )

    result = runner.invoke(
        cli,
        [
            "--json",
            "runs",
            "search",
            "needle",
            "--source",
            "local",
            "--project",
            PROJECT_NAME,
        ],
    )

    assert result.exit_code == 0, result.output
    assert parse_json_output(result.output)[0]["id"] == FIRST_RUN_ID


def test_runs_get_and_get_latest_use_the_local_source(
    runner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LANGSMITH_CLI_RUN_CACHE_DIR", str(tmp_path))
    LocalTraceRepository(tmp_path).add_runs(
        _pull_request(),
        [_run(FIRST_RUN_ID, "local latest", outputs={"answer": "offline"})],
    )

    fetched = runner.invoke(
        cli,
        ["--json", "runs", "get", FIRST_RUN_ID, "--source", "local"],
    )
    latest = runner.invoke(
        cli,
        [
            "--json",
            "runs",
            "get-latest",
            "--source",
            "local",
            "--project",
            PROJECT_NAME,
        ],
    )

    assert fetched.exit_code == 0, fetched.output
    assert latest.exit_code == 0, latest.output
    assert parse_json_output(fetched.output)["outputs"] == {"answer": "offline"}
    assert parse_json_output(latest.output)["id"] == FIRST_RUN_ID


def test_local_get_scopes_duplicate_run_ids_by_project(
    runner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """INVARIANT: local identity is (project, run ID), including child lookup."""
    second_project_id = "f47ac10b-58cc-4372-a567-0e02b2c3d480"
    second_project_name = "dev/other-local-traces"
    monkeypatch.setenv("LANGSMITH_CLI_RUN_CACHE_DIR", str(tmp_path))
    repository = LocalTraceRepository(tmp_path)
    repository.add_runs(
        _pull_request(),
        [_run(FIRST_RUN_ID, "first-project", outputs={"project": "first"})],
    )
    second_addition = repository.add_runs(
        TracePullRequest(
            source=TraceSource.CLOUD,
            project_id=second_project_id,
            project_name=second_project_name,
            requested_at=OBSERVED_AT,
        ),
        [
            create_run(
                id_str=FIRST_RUN_ID,
                name="second-project",
                outputs={"project": "second"},
                session_id=second_project_id,
            )
        ],
    )
    assert second_addition.added_run_count == 1

    fetched = runner.invoke(
        cli,
        [
            "--json",
            "runs",
            "get",
            FIRST_RUN_ID,
            "--source",
            "local",
            "--project",
            second_project_name,
        ],
    )

    assert fetched.exit_code == 0, fetched.output
    assert parse_json_output(fetched.output)["outputs"] == {"project": "second"}


def test_runs_pull_archive_then_get_local(
    runner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_root = tmp_path / "archive"
    local_root = tmp_path / "local"
    monkeypatch.setenv("LANGSMITH_ARCHIVE_URI", str(archive_root))
    monkeypatch.setenv("LANGSMITH_CLI_RUN_CACHE_DIR", str(local_root))
    archived_run = _run(FIRST_RUN_ID, "archived", outputs={"source": "archive"})
    sync_project_day(
        _ArchiveRunsClient([archived_run]),
        LocalArchiveStore(archive_root, str(archive_root)),
        project_id=PROJECT_ID,
        project_name=PROJECT_NAME,
        trace_date=archived_run.start_time.date(),
        phase=ArchivePhase.PRIMARY,
    )

    pulled = runner.invoke(
        cli,
        [
            "--json",
            "runs",
            "pull",
            "--source",
            "archive",
            "--to",
            "local",
            "--project",
            PROJECT_NAME,
            "--since",
            "2024-07-03T00:00:00Z",
            "--before",
            "2024-07-04T00:00:00Z",
        ],
    )
    fetched = runner.invoke(
        cli,
        ["--json", "runs", "get", FIRST_RUN_ID, "--source", "local"],
    )

    assert pulled.exit_code == 0, pulled.output
    assert fetched.exit_code == 0, fetched.output
    assert parse_json_output(fetched.output)["outputs"] == {"source": "archive"}


def test_archive_pull_expands_complete_trace_outside_selection_window(
    runner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """INVARIANT: selection bounds choose traces, never truncate their members."""
    archive_root = tmp_path / "archive"
    local_root = tmp_path / "local"
    monkeypatch.setenv("LANGSMITH_ARCHIVE_URI", str(archive_root))
    monkeypatch.setenv("LANGSMITH_CLI_RUN_CACHE_DIR", str(local_root))
    root = _run(FIRST_RUN_ID, "root")
    child = create_run(
        id_str=SECOND_RUN_ID,
        name="early-child",
        parent_run_id=FIRST_RUN_ID,
        trace_id=FIRST_RUN_ID,
        session_id=PROJECT_ID,
    ).model_copy(update={"start_time": datetime(2024, 7, 3, 8, 0, tzinfo=timezone.utc)})
    sync_project_day(
        _ArchiveRunsClient([root, child]),
        LocalArchiveStore(archive_root, str(archive_root)),
        project_id=PROJECT_ID,
        project_name=PROJECT_NAME,
        trace_date=root.start_time.date(),
        phase=ArchivePhase.PRIMARY,
    )

    pulled = runner.invoke(
        cli,
        [
            "--json",
            "runs",
            "pull",
            "--source",
            "archive",
            "--to",
            "local",
            "--project",
            PROJECT_NAME,
            "--since",
            "2024-07-03T09:00:00Z",
            "--before",
            "2024-07-04T00:00:00Z",
        ],
    )
    fetched = runner.invoke(
        cli,
        [
            "--json",
            "runs",
            "get",
            FIRST_RUN_ID,
            "--source",
            "local",
            "--follow-children",
        ],
    )

    assert pulled.exit_code == 0, pulled.output
    assert parse_json_output(pulled.output)["selected_run_count"] == 2
    assert fetched.exit_code == 0, fetched.output
    assert parse_json_output(fetched.output)["_children"][0]["id"] == SECOND_RUN_ID


def test_archive_trace_expansion_uses_one_batched_query() -> None:
    """Archive expansion must not rescan manifests once per selected trace."""
    from langsmith_cli.local_traces.models import TraceSelection
    from langsmith_cli.local_traces.transfer import complete_archive_traces

    selected = [_run(FIRST_RUN_ID, "first"), _run(SECOND_RUN_ID, "second")]
    selection = TraceSelection(
        source=TraceSource.ARCHIVE,
        project_name=PROJECT_NAME,
        requested_at=OBSERVED_AT,
    )
    with patch(
        "langsmith_cli.archive.query.query_archive_runs", return_value=selected
    ) as query_archive_runs:
        expanded = complete_archive_traces(selection, selected)

    assert expanded == selected
    query_archive_runs.assert_called_once()
    query = query_archive_runs.call_args.args[0]
    assert query.trace_ids == (FIRST_RUN_ID, SECOND_RUN_ID)


def test_cache_repair_rejects_tampered_fragment(
    runner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """INVARIANT: repair verifies bytes against the catalog's SHA-256 contract."""
    monkeypatch.setenv("LANGSMITH_CLI_RUN_CACHE_DIR", str(tmp_path))
    repository = LocalTraceRepository(tmp_path)
    repository.add_runs(_pull_request(), [_run(FIRST_RUN_ID, "stored")])
    fragment = repository.read_catalog().fragments[0]
    (tmp_path / fragment.key).write_bytes(b"not parquet")

    repaired = runner.invoke(cli, ["--json", "runs", "cache", "repair"])

    assert repaired.exit_code != 0
    assert "checksum mismatch" in repaired.output


def test_archive_and_local_share_supported_query_semantics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Golden query: shared predicates return identical IDs and ordering."""
    from langsmith_cli.archive.query import query_archive_runs

    archive_root = tmp_path / "archive"
    monkeypatch.setenv("LANGSMITH_ARCHIVE_URI", str(archive_root))
    matching = create_run(
        id_str=FIRST_RUN_ID,
        name="matching",
        run_type="llm",
        tags=["production"],
        outputs={"message": "Needle"},
        session_id=PROJECT_ID,
    )
    ignored = create_run(
        id_str=SECOND_RUN_ID,
        name="ignored",
        run_type="tool",
        tags=["production"],
        outputs={"message": "Needle"},
        session_id=PROJECT_ID,
    )
    sync_project_day(
        _ArchiveRunsClient([matching, ignored]),
        LocalArchiveStore(archive_root, str(archive_root)),
        project_id=PROJECT_ID,
        project_name=PROJECT_NAME,
        trace_date=matching.start_time.date(),
        phase=ArchivePhase.PRIMARY,
    )
    repository = LocalTraceRepository(tmp_path / "local")
    repository.add_runs(_pull_request(), [matching, ignored])
    query = RunQuery(
        project=PROJECT_NAME,
        run_type="llm",
        tags=("production",),
        text="needle",
        text_ignore_case=True,
        limit=None,
    )

    archived_ids = [str(run.id) for run in query_archive_runs(query)]
    local_ids = [str(run.id) for run in repository.query(query)]

    assert archived_ids == local_ids == [FIRST_RUN_ID]
