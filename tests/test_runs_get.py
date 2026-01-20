"""Tests for runs get and get-latest commands."""

import json
import re
from unittest.mock import patch, MagicMock

import pytest

from conftest import create_run, create_project
from langsmith_cli.main import cli


class TestRunsGet:
    """Tests for runs get command."""

    def test_get_json_output(self, runner, mock_client):
        """Get command returns run data in JSON format."""
        mock_client.read_run.return_value = create_run(
            name="Detailed Run",
            id_str="12345678-0000-0000-0000-000000000456",
            inputs={"q": "hello"},
            outputs={"a": "world"},
        )

        result = runner.invoke(
            cli, ["--json", "runs", "get", "12345678-0000-0000-0000-000000000456"]
        )

        assert result.exit_code == 0
        assert "12345678-0000-0000-0000-000000000456" in result.output
        assert "hello" in result.output

    def test_get_with_fields_pruning(self, runner, mock_client):
        """--fields prunes output to selected fields only."""
        mock_client.read_run.return_value = create_run(
            name="Full Run",
            id_str="12345678-0000-0000-0000-000000000789",
            inputs={"input": "foo"},
            outputs={"output": "bar"},
            extra={"heavy_field": "huge_data"},
        )

        result = runner.invoke(
            cli,
            [
                "--json",
                "runs",
                "get",
                "12345678-0000-0000-0000-000000000789",
                "--fields",
                "inputs",
            ],
        )

        assert result.exit_code == 0
        assert "foo" in result.output
        assert "huge_data" not in result.output

    def test_get_rich_output(self, runner, mock_client):
        """Get command displays rich formatted output without --json."""
        mock_client.read_run.return_value = create_run(
            name="Rich Output Test",
            id_str="12345678-0000-0000-0000-000000000123",
            inputs={"query": "test"},
            outputs={"result": "success"},
        )

        result = runner.invoke(
            cli, ["runs", "get", "12345678-0000-0000-0000-000000000123"]
        )

        assert result.exit_code == 0
        clean_output = re.sub(r"\x1b\[[0-9;]*m", "", result.output)
        assert "12345678-0000-0000-0000-000000000123" in clean_output
        assert "Rich Output Test" in clean_output

    def test_get_with_complex_data_types(self, runner, mock_client):
        """Get command handles dict and list data types."""
        mock_client.read_run.return_value = create_run(
            name="Complex Data",
            metadata={"key": "value", "nested": {"deep": "data"}},
            tags=["tag1", "tag2"],
            extra={"simple_field": "simple_value"},
        )

        result = runner.invoke(
            cli, ["runs", "get", "12345678-1234-5678-1234-567812345678"]
        )

        assert result.exit_code == 0
        assert "tag1" in result.output or "tags" in result.output
        assert "simple_value" in result.output


class TestRunsGetLatest:
    """Tests for runs get-latest command."""

    def test_get_latest_basic(self, runner, mock_client):
        """Get-latest returns most recent run."""
        mock_client.list_runs.return_value = iter([create_run(name="Latest Run")])

        result = runner.invoke(cli, ["runs", "get-latest", "--project", "test"])

        assert result.exit_code == 0
        assert "Latest Run" in result.output

    def test_get_latest_json_output(self, runner, mock_client):
        """Get-latest with --json returns JSON."""
        mock_client.list_runs.return_value = iter([create_run(name="Latest Run")])

        result = runner.invoke(
            cli, ["--json", "runs", "get-latest", "--project", "test"]
        )

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["name"] == "Latest Run"

    def test_get_latest_with_fields(self, runner, mock_client):
        """Get-latest with --fields returns only selected fields."""
        mock_client.list_runs.return_value = iter(
            [
                create_run(
                    name="Latest Run",
                    inputs={"text": "test input"},
                    outputs={"response": "test output"},
                )
            ]
        )

        result = runner.invoke(
            cli,
            [
                "--json",
                "runs",
                "get-latest",
                "--project",
                "test",
                "--fields",
                "inputs,outputs",
            ],
        )

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "inputs" in data
        assert "outputs" in data
        assert "id" not in data
        assert "name" not in data

    @pytest.mark.parametrize(
        "flag,expected_error",
        [
            ("--failed", True),
            ("--succeeded", False),
        ],
    )
    def test_get_latest_status_flags(self, runner, mock_client, flag, expected_error):
        """--failed and --succeeded flags filter by error status."""
        mock_client.list_runs.return_value = iter([create_run(name="Run")])

        runner.invoke(cli, ["--json", "runs", "get-latest", "--project", "test", flag])

        call_kwargs = mock_client.list_runs.call_args[1]
        assert call_kwargs["error"] is expected_error

    def test_get_latest_with_roots_flag(self, runner, mock_client):
        """--roots flag filters to root runs."""
        mock_client.list_runs.return_value = iter([create_run(name="Root Run")])

        runner.invoke(
            cli, ["--json", "runs", "get-latest", "--project", "test", "--roots"]
        )

        call_kwargs = mock_client.list_runs.call_args[1]
        assert call_kwargs["is_root"] is True

    def test_get_latest_no_runs_found(self, runner, mock_client):
        """Get-latest returns error when no runs match."""
        mock_client.list_runs.return_value = iter([])

        result = runner.invoke(
            cli, ["runs", "get-latest", "--project", "test", "--failed"]
        )

        assert result.exit_code == 1
        assert "No runs found" in result.output

    def test_get_latest_with_multiple_projects(self, runner, mock_client):
        """Get-latest searches multiple projects with pattern."""
        mock_client.list_projects.return_value = [
            create_project(name="prd/project1"),
            create_project(name="prd/project2"),
        ]

        def list_runs_side_effect(**kwargs):
            if kwargs["project_name"] == "prd/project1":
                return iter([])
            return iter([create_run(name="Run from project2")])

        mock_client.list_runs.side_effect = list_runs_side_effect

        result = runner.invoke(
            cli, ["--json", "runs", "get-latest", "--project-name-pattern", "prd/*"]
        )

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["name"] == "Run from project2"

    def test_get_latest_with_tag_filter(self, runner, mock_client):
        """--tag filter builds correct FQL."""
        mock_client.list_runs.return_value = iter(
            [create_run(name="Tagged Run", tags=["prod", "critical"])]
        )

        result = runner.invoke(
            cli,
            [
                "--json",
                "runs",
                "get-latest",
                "--project",
                "test",
                "--tag",
                "prod",
                "--tag",
                "critical",
            ],
        )

        assert result.exit_code == 0
        call_kwargs = mock_client.list_runs.call_args[1]
        assert 'has(tags, "prod")' in call_kwargs["filter"]
        assert 'has(tags, "critical")' in call_kwargs["filter"]


class TestRunsStats:
    """Tests for runs stats command."""

    def test_stats_basic(self, runner, mock_client):
        """Stats command displays statistics."""
        mock_client.get_run_stats.return_value = {"error_rate": 0.1, "latency_p50": 0.2}

        result = runner.invoke(cli, ["runs", "stats"])

        assert result.exit_code == 0
        assert "Error Rate" in result.output
        assert "0.1" in result.output

    def test_stats_table_output(self, runner, mock_client):
        """Stats with table output shows metrics."""
        mock_project = MagicMock()
        mock_project.id = "project-123"
        mock_client.read_project.return_value = mock_project
        mock_client.get_run_stats.return_value = {
            "run_count": 100,
            "error_count": 5,
            "avg_latency": 1.5,
        }

        result = runner.invoke(cli, ["runs", "stats", "--project", "test-project"])

        assert result.exit_code == 0
        assert "100" in result.output
        assert "5" in result.output

    def test_stats_json_output(self, runner, mock_client):
        """Stats with --json returns JSON."""
        mock_project = MagicMock()
        mock_project.id = "project-456"
        mock_client.read_project.return_value = mock_project
        mock_client.get_run_stats.return_value = {"run_count": 50, "error_count": 2}

        result = runner.invoke(
            cli, ["--json", "runs", "stats", "--project", "my-project"]
        )

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["run_count"] == 50
        assert data["error_count"] == 2

    def test_stats_fallback_to_project_id(self, runner, mock_client):
        """Stats falls back to using project name as ID on error."""
        mock_client.read_project.side_effect = Exception("Not found")
        mock_client.get_run_stats.return_value = {"run_count": 10}

        result = runner.invoke(
            cli, ["--json", "runs", "stats", "--project", "fallback-id"]
        )

        assert result.exit_code == 0
        mock_client.get_run_stats.assert_called_once()


class TestRunsOpen:
    """Tests for runs open command."""

    def test_open_command(self, runner):
        """Open command launches browser with correct URL."""
        with patch("webbrowser.open") as mock_browser:
            result = runner.invoke(cli, ["runs", "open", "test-run-id"])

            assert result.exit_code == 0
            assert "Opening run test-run-id" in result.output
            assert "https://smith.langchain.com/r/test-run-id" in result.output
            mock_browser.assert_called_once_with(
                "https://smith.langchain.com/r/test-run-id"
            )


class TestRunsWatch:
    """Tests for runs watch command."""

    def test_watch_keyboard_interrupt(self, runner, mock_client):
        """Watch handles keyboard interrupt gracefully."""
        from uuid import UUID

        mock_client.list_projects.return_value = []
        test_run = create_run(name="Watched Run", total_tokens=100)
        # Override session_id to be a UUID
        test_run_dict = test_run.model_dump()
        test_run_dict["session_id"] = UUID("00000000-0000-0000-0000-000000000092")

        from langsmith.schemas import Run

        test_run_with_session = Run(**test_run_dict)

        mock_client.list_runs.side_effect = [
            [test_run_with_session],
            KeyboardInterrupt(),
        ]

        with patch("time.sleep") as mock_sleep:
            mock_sleep.side_effect = KeyboardInterrupt()
            result = runner.invoke(cli, ["runs", "watch", "--project", "test"])

        assert result.exit_code == 0
