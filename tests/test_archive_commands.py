"""CLI contracts for organization-operated archive jobs."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterator

import pytest
from langsmith.schemas import Run, TracerSessionResult

from conftest import create_project, create_run, parse_json_output
from langsmith_cli.main import cli


class FakeArchiveClient:
    def __init__(self, projects: list[TracerSessionResult], runs: list[Run]) -> None:
        self.projects = projects
        self.runs = runs

    def list_projects(self, *, limit: None) -> Iterator[TracerSessionResult]:
        return iter(self.projects)

    def read_project(self, *, project_name: str) -> TracerSessionResult:
        return next(
            project for project in self.projects if project.name == project_name
        )

    def list_runs(self, **kwargs: Any) -> Iterator[Run]:
        return iter(self.runs)


def _write_config(tmp_path: Path) -> Path:
    config_path = tmp_path / "archive.yaml"
    config_path.write_text(
        f"""routes:
  - name: dev
    project_pattern: dev/**
    archive_uri: {tmp_path / "dev-archive"}
  - name: staging
    project_pattern: stg/**
    archive_uri: {tmp_path / "stg-archive"}
""",
        encoding="utf-8",
    )
    return config_path


def test_sync_command_routes_projects_and_reports_unmatched(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    runner,
) -> None:
    from langsmith_cli.commands import archive as archive_commands

    client = FakeArchiveClient(
        [
            create_project(name="dev/agent"),
            create_project(
                name="qa/unrouted",
                project_id="22345678-1234-5678-1234-567812345678",
            ),
        ],
        [create_run()],
    )
    monkeypatch.setattr(archive_commands, "get_or_create_client", lambda ctx: client)
    result = runner.invoke(
        cli,
        [
            "--json",
            "archive",
            "sync",
            "--config",
            str(_write_config(tmp_path)),
            "--route",
            "dev",
            "--today",
            "2026-08-21",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = parse_json_output(result.output)
    assert len(payload["processed"]) == 2
    assert {item["phase"] for item in payload["processed"]} == {
        "primary",
        "reconciliation",
    }
    assert payload["unmatched_projects"] == ["qa/unrouted"]
    assert all(item["route"] == "dev" for item in payload["processed"])


def test_bulk_backfill_submits_all_projects_before_importing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    runner,
) -> None:
    from langsmith_cli.archive.backfill import BackfillImportResult
    from langsmith_cli.commands import archive as archive_commands

    projects = [
        create_project(name="dev/agent"),
        create_project(
            name="dev/other",
            project_id="22345678-1234-5678-1234-567812345678",
        ),
        create_project(
            name="stg/not-selected",
            project_id="32345678-1234-5678-1234-567812345678",
        ),
        create_project(
            name="qa/unrouted",
            project_id="52345678-1234-5678-1234-567812345678",
        ),
    ]
    client = FakeArchiveClient(projects, [])
    monkeypatch.setattr(archive_commands, "get_or_create_client", lambda ctx: client)
    events: list[str] = []

    class FakeManagedExporter:
        def begin_window(self, **kwargs: object) -> str:
            project_id = str(kwargs["project_id"])
            events.append(f"begin:{project_id}")
            return project_id

        def complete_export(self, job: str) -> str:
            events.append(f"complete:{job}")
            return job

    exporter = FakeManagedExporter()
    monkeypatch.setattr(
        archive_commands.LangSmithBulkExporter,
        "from_langsmith_client",
        staticmethod(lambda client, **kwargs: exporter),
    )

    def import_snapshot(
        store: object,
        *,
        project_id: str,
        project_name: str,
        snapshot: str,
    ) -> BackfillImportResult:
        assert snapshot == project_id
        events.append(f"import:{project_id}")
        return BackfillImportResult(
            export_id=f"export-{project_name}",
            imported_days=2,
            skipped_days=0,
            canonical_run_count=7,
        )

    monkeypatch.setattr(archive_commands, "import_backfill_snapshot", import_snapshot)
    result = runner.invoke(
        cli,
        [
            "--json",
            "archive",
            "backfill",
            "--config",
            str(_write_config(tmp_path)),
            "--route",
            "dev",
            "--start-date",
            "2026-08-18",
            "--end-date",
            "2026-08-20",
            "--bulk-export-destination-id",
            "42345678-1234-5678-1234-567812345678",
        ],
    )

    assert result.exit_code == 0, result.output
    assert events == [
        f"begin:{projects[0].id}",
        f"begin:{projects[1].id}",
        f"complete:{projects[0].id}",
        f"import:{projects[0].id}",
        f"complete:{projects[1].id}",
        f"import:{projects[1].id}",
    ]
    payload = parse_json_output(result.output)
    assert [item["project_name"] for item in payload["projects"]] == [
        "dev/agent",
        "dev/other",
    ]
    assert payload["unmatched_projects"] == ["qa/unrouted"]


@pytest.mark.parametrize(
    ("project_name", "message"),
    (
        ("stg/not-selected", "belongs to route staging"),
        ("qa/unrouted", "No archive route matches project"),
    ),
)
def test_bulk_backfill_rejects_explicit_project_outside_route(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    runner,
    project_name: str,
    message: str,
) -> None:
    from langsmith_cli.commands import archive as archive_commands

    client = FakeArchiveClient([create_project(name=project_name)], [])
    monkeypatch.setattr(archive_commands, "get_or_create_client", lambda ctx: client)
    monkeypatch.setattr(
        archive_commands.LangSmithBulkExporter,
        "from_langsmith_client",
        staticmethod(lambda client, **kwargs: object()),
    )
    result = runner.invoke(
        cli,
        [
            "--json",
            "archive",
            "backfill",
            "--config",
            str(_write_config(tmp_path)),
            "--route",
            "dev",
            "--project",
            project_name,
            "--start-date",
            "2026-08-18",
            "--end-date",
            "2026-08-20",
            "--bulk-export-destination-id",
            "42345678-1234-5678-1234-567812345678",
        ],
    )

    assert result.exit_code != 0
    assert message in parse_json_output(result.output)["message"]


def test_sync_command_rejects_project_from_another_selected_route(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    runner,
) -> None:
    from langsmith_cli.commands import archive as archive_commands

    client = FakeArchiveClient([create_project(name="dev/agent")], [create_run()])
    monkeypatch.setattr(archive_commands, "get_or_create_client", lambda ctx: client)
    result = runner.invoke(
        cli,
        [
            "--json",
            "archive",
            "sync",
            "--config",
            str(_write_config(tmp_path)),
            "--route",
            "staging",
            "--project",
            "dev/agent",
            "--date",
            "2026-08-19",
            "--phase",
            "primary",
        ],
    )

    assert result.exit_code != 0
    payload = parse_json_output(result.output)
    assert "belongs to route dev" in payload["message"]


def test_status_command_lists_published_manifests(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    runner,
) -> None:
    from langsmith_cli.commands import archive as archive_commands

    client = FakeArchiveClient([create_project(name="dev/agent")], [create_run()])
    monkeypatch.setattr(archive_commands, "get_or_create_client", lambda ctx: client)
    config_path = _write_config(tmp_path)
    sync_result = runner.invoke(
        cli,
        [
            "--json",
            "archive",
            "sync",
            "--config",
            str(config_path),
            "--route",
            "dev",
            "--project",
            "dev/agent",
            "--date",
            "2026-08-19",
            "--phase",
            "primary",
        ],
    )
    assert sync_result.exit_code == 0, sync_result.output

    status_result = runner.invoke(
        cli,
        [
            "--json",
            "archive",
            "status",
            "--config",
            str(config_path),
            "--route",
            "dev",
        ],
    )
    assert status_result.exit_code == 0, status_result.output
    manifests = parse_json_output(status_result.output)
    assert len(manifests) == 1
    assert manifests[0]["project_name"] == "dev/agent"
    assert manifests[0]["canonical_run_count"] == 1


def test_sync_command_surfaces_safe_retry_on_concurrent_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    runner,
) -> None:
    from langsmith_cli.archive.storage import ConcurrentArchiveWriteError
    from langsmith_cli.commands import archive as archive_commands

    client = FakeArchiveClient([create_project(name="dev/agent")], [create_run()])
    monkeypatch.setattr(archive_commands, "get_or_create_client", lambda ctx: client)

    def conflict(*args: object, **kwargs: object) -> None:
        raise ConcurrentArchiveWriteError("stale")

    monkeypatch.setattr(archive_commands, "sync_project_day", conflict)
    result = runner.invoke(
        cli,
        [
            "--json",
            "archive",
            "sync",
            "--config",
            str(_write_config(tmp_path)),
            "--route",
            "dev",
            "--project",
            "dev/agent",
            "--date",
            "2026-08-19",
            "--phase",
            "primary",
        ],
    )
    assert result.exit_code != 0
    payload = parse_json_output(result.output)
    assert "rerun the sync safely" in payload["message"]


def test_archive_list_honors_fetch_for_local_filters(
    monkeypatch: pytest.MonkeyPatch,
    runner,
) -> None:
    from langsmith_cli.archive import query as archive_query

    observed_limits: list[int | None] = []

    def query_runs(query: archive_query.ArchiveRunQuery) -> list[Run]:
        observed_limits.append(query.limit)
        return []

    monkeypatch.setattr(archive_query, "query_archive_runs", query_runs)
    result = runner.invoke(
        cli,
        [
            "--json",
            "runs",
            "list",
            "--archive",
            "--name-pattern",
            "worker-*",
            "--fetch",
            "7",
        ],
    )
    assert result.exit_code == 0, result.output
    assert observed_limits == [7]


def test_archive_get_reports_missing_run(
    monkeypatch: pytest.MonkeyPatch,
    runner,
) -> None:
    from langsmith_cli.archive import query as archive_query

    def missing_run(
        run_id: str, *, follow_children: bool, **kwargs: object
    ) -> tuple[Run, list[Run]]:
        raise LookupError(f"Archived run not found: {run_id}")

    monkeypatch.setattr(archive_query, "read_archived_run", missing_run)
    result = runner.invoke(
        cli,
        ["--json", "runs", "get", "12345678-1234-5678-1234-567812345678", "--archive"],
    )

    assert result.exit_code != 0
    assert "Archived run not found" in parse_json_output(result.output)["message"]


def test_archive_get_forwards_partition_pruning_hints(
    monkeypatch: pytest.MonkeyPatch,
    runner,
) -> None:
    from langsmith_cli.archive import query as archive_query

    observed: dict[str, object] = {}

    def read_run(
        run_id: str,
        *,
        follow_children: bool,
        project: str | None,
        project_id: str | None,
        since: object,
        before: object,
    ) -> tuple[Run, list[Run]]:
        observed.update(
            run_id=run_id,
            follow_children=follow_children,
            project=project,
            project_id=project_id,
            since=since,
            before=before,
        )
        return create_run(id_str=run_id), []

    monkeypatch.setattr(archive_query, "read_archived_run", read_run)
    run_id = "12345678-1234-5678-1234-567812345678"
    result = runner.invoke(
        cli,
        [
            "--json",
            "runs",
            "get",
            run_id,
            "--archive",
            "--project",
            "dev/agent",
            "--project-id",
            "22345678-1234-5678-1234-567812345678",
            "--since",
            "2026-08-18",
            "--before",
            "2026-08-20",
        ],
    )

    assert result.exit_code == 0, result.output
    assert observed["project"] == "dev/agent"
    assert observed["project_id"] == "22345678-1234-5678-1234-567812345678"
    assert str(observed["since"]) == "2026-08-18 00:00:00"
    assert str(observed["before"]) == "2026-08-20 00:00:00"


def test_archive_get_latest_maps_filters_and_fields(
    monkeypatch: pytest.MonkeyPatch,
    runner,
) -> None:
    from langsmith_cli.archive import query as archive_query

    observed: list[archive_query.ArchiveRunQuery] = []

    def query_runs(query: archive_query.ArchiveRunQuery) -> list[Run]:
        observed.append(query)
        return [create_run(name="latest", error="boom", extra={"model": "gpt-5"})]

    monkeypatch.setattr(archive_query, "query_archive_runs", query_runs)
    result = runner.invoke(
        cli,
        [
            "--json",
            "runs",
            "get-latest",
            "--archive",
            "--project",
            "dev/agent",
            "--failed",
            "--roots",
            "--tag",
            "nightly",
            "--model",
            "gpt-5",
            "--fields",
            "id,name,error",
        ],
    )

    assert result.exit_code == 0, result.output
    assert parse_json_output(result.output)["name"] == "latest"
    query = observed[0]
    assert query.project == "dev/agent"
    assert query.error is True
    assert query.is_root is True
    assert query.tags == ("nightly",)
    assert query.text == "gpt-5"
    assert query.text_fields == ("extra",)


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        (["--filter", 'eq(name, "x")'], "does not yet support"),
        ([], "No archived runs found"),
    ],
)
def test_archive_get_latest_fails_clearly(
    monkeypatch: pytest.MonkeyPatch,
    runner,
    arguments: list[str],
    message: str,
) -> None:
    from langsmith_cli.archive import query as archive_query

    monkeypatch.setattr(archive_query, "query_archive_runs", lambda query: [])
    result = runner.invoke(
        cli,
        ["--json", "runs", "get-latest", "--archive", *arguments],
    )

    assert result.exit_code != 0
    assert message in parse_json_output(result.output)["message"]


@pytest.mark.parametrize(
    "arguments",
    [
        ["--filter", 'eq(name, "x")'],
        ["--query", "needle", "--grep", "needle"],
    ],
)
def test_archive_list_rejects_unsupported_or_ambiguous_filters(
    runner,
    arguments: list[str],
) -> None:
    result = runner.invoke(
        cli,
        ["--json", "runs", "list", "--archive", *arguments],
    )

    assert result.exit_code != 0


def test_archive_list_applies_local_filters_sort_limit_and_count(
    monkeypatch: pytest.MonkeyPatch,
    runner,
) -> None:
    from langsmith_cli.archive import query as archive_query

    runs = [
        create_run(name="worker-b"),
        create_run(name="ignore", id_str="22345678-1234-5678-1234-567812345678"),
        create_run(name="worker-a", id_str="32345678-1234-5678-1234-567812345678"),
    ]
    monkeypatch.setattr(archive_query, "query_archive_runs", lambda query: runs)
    result = runner.invoke(
        cli,
        [
            "--json",
            "runs",
            "list",
            "--archive",
            "--name-pattern",
            "worker-*",
            "--exclude",
            "*b",
            "--sort-by",
            "name",
            "--limit",
            "1",
            "--count",
        ],
    )

    assert result.exit_code == 0, result.output
    assert result.output.strip() == "1"


def test_archive_list_writes_selected_fields(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    runner,
) -> None:
    from langsmith_cli.archive import query as archive_query

    monkeypatch.setattr(
        archive_query, "query_archive_runs", lambda query: [create_run(name="saved")]
    )
    output_path = tmp_path / "runs.jsonl"
    result = runner.invoke(
        cli,
        [
            "runs",
            "list",
            "--archive",
            "--output",
            str(output_path),
            "--fields",
            "id,name",
        ],
    )

    assert result.exit_code == 0, result.output
    assert '"name": "saved"' in output_path.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "arguments",
    [
        ["--route", "dev", "--all-routes"],
        [],
        ["--date", "2026-08-19"],
    ],
)
def test_sync_command_rejects_ambiguous_or_incomplete_selection(
    tmp_path: Path,
    runner,
    arguments: list[str],
) -> None:
    result = runner.invoke(
        cli,
        [
            "--json",
            "archive",
            "sync",
            "--config",
            str(_write_config(tmp_path)),
            *arguments,
        ],
    )
    assert result.exit_code != 0
