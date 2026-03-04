"""
Tests for runs export command.

Tests use mocked data with real Pydantic model instances.
"""

from langsmith_cli.main import cli
from unittest.mock import patch
import json
import os
from conftest import create_run, create_project, parse_json_output, strip_ansi


def test_export_creates_directory_and_files(runner, tmp_path):
    """INVARIANT: Export should create directory and write JSON files per run."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        # Mock project resolution
        project = create_project(name="my-project")
        mock_client.read_project.return_value = project

        # Create test runs
        run1 = create_run(
            name="run-1",
            id_str="11111111-1111-1111-1111-111111111111",
            inputs={"q": "hello"},
            outputs={"a": "world"},
        )
        run2 = create_run(
            name="run-2",
            id_str="22222222-2222-2222-2222-222222222222",
            inputs={"q": "foo"},
            outputs={"a": "bar"},
        )
        mock_client.list_runs.return_value = [run1, run2]

        out_dir = tmp_path / "traces"
        result = runner.invoke(
            cli,
            ["runs", "export", str(out_dir), "--project", "my-project"],
        )
        assert result.exit_code == 0

        # Verify files were created
        assert out_dir.exists()
        files = sorted(os.listdir(out_dir))
        assert len(files) == 2
        assert "11111111-1111-1111-1111-111111111111.json" in files
        assert "22222222-2222-2222-2222-222222222222.json" in files

        # Verify content
        with open(out_dir / files[0]) as f:
            data = json.load(f)
        assert data["name"] in ["run-1", "run-2"]


def test_export_json_output(runner, tmp_path):
    """INVARIANT: --json should return export summary."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        project = create_project(name="test-proj")
        mock_client.read_project.return_value = project

        run1 = create_run(name="run-1", id_str="11111111-1111-1111-1111-111111111111")
        mock_client.list_runs.return_value = [run1]

        out_dir = tmp_path / "traces"
        result = runner.invoke(
            cli,
            ["--json", "runs", "export", str(out_dir), "--project", "test-proj"],
        )
        assert result.exit_code == 0
        data = parse_json_output(result.output)
        assert data["status"] == "success"
        assert data["exported"] == 1
        assert len(data["files"]) == 1


def test_export_with_fields_pruning(runner, tmp_path):
    """INVARIANT: --fields should limit fields in exported files."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        project = create_project(name="test-proj")
        mock_client.read_project.return_value = project

        run1 = create_run(
            name="run-1",
            id_str="11111111-1111-1111-1111-111111111111",
            inputs={"q": "hello"},
            outputs={"a": "world"},
        )
        mock_client.list_runs.return_value = [run1]

        out_dir = tmp_path / "traces"
        result = runner.invoke(
            cli,
            [
                "runs",
                "export",
                str(out_dir),
                "--project",
                "test-proj",
                "--fields",
                "name,inputs,status",
            ],
        )
        assert result.exit_code == 0

        files = os.listdir(out_dir)
        assert len(files) == 1
        with open(out_dir / files[0]) as f:
            data = json.load(f)
        assert "name" in data
        assert "inputs" in data
        assert "status" in data
        # Full run data fields should not be present
        assert "extra" not in data


def test_export_custom_filename_pattern(runner, tmp_path):
    """INVARIANT: --filename-pattern should control output filenames."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        project = create_project(name="test-proj")
        mock_client.read_project.return_value = project

        run1 = create_run(
            name="my-chain",
            id_str="11111111-1111-1111-1111-111111111111",
        )
        mock_client.list_runs.return_value = [run1]

        out_dir = tmp_path / "traces"
        result = runner.invoke(
            cli,
            [
                "runs",
                "export",
                str(out_dir),
                "--project",
                "test-proj",
                "--filename-pattern",
                "{name}_{index}.json",
            ],
        )
        assert result.exit_code == 0
        files = os.listdir(out_dir)
        assert "my-chain_0.json" in files


def test_export_with_roots_filter(runner, tmp_path):
    """INVARIANT: --roots should filter to root traces only."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        project = create_project(name="test-proj")
        mock_client.read_project.return_value = project
        mock_client.list_runs.return_value = []

        out_dir = tmp_path / "traces"
        result = runner.invoke(
            cli,
            ["runs", "export", str(out_dir), "--project", "test-proj", "--roots"],
        )
        assert result.exit_code == 0
        # Verify is_root was passed to list_runs
        call_kwargs = mock_client.list_runs.call_args[1]
        assert call_kwargs["is_root"] is True


def test_export_with_status_filter(runner, tmp_path):
    """INVARIANT: --status should filter runs by status."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        project = create_project(name="test-proj")
        mock_client.read_project.return_value = project
        mock_client.list_runs.return_value = []

        out_dir = tmp_path / "traces"
        result = runner.invoke(
            cli,
            [
                "runs",
                "export",
                str(out_dir),
                "--project",
                "test-proj",
                "--status",
                "error",
            ],
        )
        assert result.exit_code == 0
        call_kwargs = mock_client.list_runs.call_args[1]
        assert call_kwargs["error"] is True


def test_export_with_tag_filter(runner, tmp_path):
    """INVARIANT: --tag should add FQL tag filter."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        project = create_project(name="test-proj")
        mock_client.read_project.return_value = project
        mock_client.list_runs.return_value = []

        out_dir = tmp_path / "traces"
        result = runner.invoke(
            cli,
            [
                "runs",
                "export",
                str(out_dir),
                "--project",
                "test-proj",
                "--tag",
                "production",
            ],
        )
        assert result.exit_code == 0
        call_kwargs = mock_client.list_runs.call_args[1]
        assert 'has(tags, "production")' in (call_kwargs.get("filter") or "")


def test_export_empty_results(runner, tmp_path):
    """INVARIANT: No matching runs should be handled gracefully."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        project = create_project(name="test-proj")
        mock_client.read_project.return_value = project
        mock_client.list_runs.return_value = []

        out_dir = tmp_path / "traces"
        result = runner.invoke(
            cli,
            ["--json", "runs", "export", str(out_dir), "--project", "test-proj"],
        )
        assert result.exit_code == 0
        data = parse_json_output(result.output)
        assert data["exported"] == 0


def test_export_table_output(runner, tmp_path):
    """INVARIANT: Export without --json should show success message."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        project = create_project(name="test-proj")
        mock_client.read_project.return_value = project

        run1 = create_run(name="run-1", id_str="11111111-1111-1111-1111-111111111111")
        mock_client.list_runs.return_value = [run1]

        out_dir = tmp_path / "traces"
        result = runner.invoke(
            cli,
            ["runs", "export", str(out_dir), "--project", "test-proj"],
        )
        assert result.exit_code == 0
        output = strip_ansi(result.output)
        assert "Exported" in output
        assert "1" in output


def test_export_limit_applied(runner, tmp_path):
    """INVARIANT: --limit should cap the number of exported files."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        project = create_project(name="test-proj")
        mock_client.read_project.return_value = project

        # Create 10 runs but limit to 3
        runs = [
            create_run(name=f"run-{i}", id_str=f"0000000{i}-0000-0000-0000-000000000000")
            for i in range(10)
        ]
        mock_client.list_runs.return_value = runs

        out_dir = tmp_path / "traces"
        result = runner.invoke(
            cli,
            [
                "runs",
                "export",
                str(out_dir),
                "--project",
                "test-proj",
                "--limit",
                "3",
            ],
        )
        assert result.exit_code == 0
        # Verify limit was passed to API
        call_kwargs = mock_client.list_runs.call_args[1]
        assert call_kwargs["limit"] == 3


def test_export_sanitizes_filename(runner, tmp_path):
    """INVARIANT: Run names with slashes should be sanitized in filenames."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        project = create_project(name="test-proj")
        mock_client.read_project.return_value = project

        run1 = create_run(
            name="my/chain/name",
            id_str="11111111-1111-1111-1111-111111111111",
        )
        mock_client.list_runs.return_value = [run1]

        out_dir = tmp_path / "traces"
        result = runner.invoke(
            cli,
            [
                "runs",
                "export",
                str(out_dir),
                "--project",
                "test-proj",
                "--filename-pattern",
                "{name}.json",
            ],
        )
        assert result.exit_code == 0
        files = os.listdir(out_dir)
        # Slashes should be replaced with underscores
        assert "my_chain_name.json" in files


def test_export_invalid_filename_pattern(runner, tmp_path):
    """INVARIANT: Invalid filename pattern variables should produce a friendly error,
    not a raw KeyError."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        project = create_project(name="test-proj")
        mock_client.read_project.return_value = project

        run1 = create_run(name="run-1", id_str="11111111-1111-1111-1111-111111111111")
        mock_client.list_runs.return_value = [run1]

        out_dir = tmp_path / "traces"
        result = runner.invoke(
            cli,
            [
                "--json",
                "runs",
                "export",
                str(out_dir),
                "--project",
                "test-proj",
                "--filename-pattern",
                "{name}_{id}.json",  # {id} is invalid, should be {run_id}
            ],
        )
        assert result.exit_code != 0
        data = parse_json_output(result.output)
        assert "error" in data
        # Should mention valid pattern variables, not just "KeyError: 'id'"
        assert "run_id" in data["message"]
        assert "trace_id" in data["message"] or "name" in data["message"]


def test_export_with_time_filter(runner, tmp_path):
    """INVARIANT: --last and --since should filter exported runs."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        project = create_project(name="test-proj")
        mock_client.read_project.return_value = project
        mock_client.list_runs.return_value = []

        out_dir = tmp_path / "traces"
        result = runner.invoke(
            cli,
            [
                "runs",
                "export",
                str(out_dir),
                "--project",
                "test-proj",
                "--last",
                "24h",
            ],
        )
        assert result.exit_code == 0
        # Verify time filter was applied (it becomes part of the FQL filter)
        call_kwargs = mock_client.list_runs.call_args[1]
        assert call_kwargs.get("filter") is not None
        assert "start_time" in call_kwargs["filter"]
