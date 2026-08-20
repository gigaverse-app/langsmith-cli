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
