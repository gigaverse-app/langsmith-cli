"""Tests for --output flag across list commands."""

from unittest.mock import patch
from uuid import UUID
from datetime import datetime, timezone

from langsmith.schemas import Dataset, Example, Run, TracerSessionResult
from langsmith_cli.main import cli
from conftest import strip_ansi


def test_runs_list_with_output(runner, tmp_path):
    """Test runs list with --output writes to file."""
    output_file = tmp_path / "runs_output.jsonl"

    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        mock_run = Run(
            id=UUID("12345678-1234-1234-1234-123456789abc"),
            name="test-run",
            run_type="chain",
            status="success",
            start_time=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end_time=datetime(2024, 1, 1, tzinfo=timezone.utc),
        )
        mock_client.list_runs.return_value = [mock_run]
        mock_client.list_projects.return_value = [
            TracerSessionResult(
                id=UUID("87654321-4321-4321-4321-cba987654321"),
                name="default",
                tenant_id=UUID("00000000-0000-0000-0000-000000000000"),
                reference_dataset_id=None,
            )
        ]

        result = runner.invoke(cli, ["runs", "list", "--output", str(output_file)])

        assert result.exit_code == 0
        assert output_file.exists()
        content = output_file.read_text()
        assert "test-run" in content
        assert "Wrote 1 items" in strip_ansi(result.output)


def test_projects_list_with_output(runner, tmp_path):
    """Test projects list with --output writes to file."""
    output_file = tmp_path / "projects_output.jsonl"

    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        mock_project = TracerSessionResult(
            id=UUID("87654321-4321-4321-4321-cba987654321"),
            name="test-project",
            tenant_id=UUID("00000000-0000-0000-0000-000000000000"),
            reference_dataset_id=None,
        )
        mock_client.list_projects.return_value = [mock_project]

        result = runner.invoke(cli, ["projects", "list", "--output", str(output_file)])

        assert result.exit_code == 0
        assert output_file.exists()
        content = output_file.read_text()
        assert "test-project" in content
        assert "Wrote 1 items" in strip_ansi(result.output)


def test_datasets_list_with_output(runner, tmp_path):
    """Test datasets list with --output writes to file."""
    output_file = tmp_path / "datasets_output.jsonl"

    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        mock_dataset = Dataset(
            id=UUID("ae99b6fa-a6db-4f1c-8868-bc6764f4c29e"),
            name="test-dataset",
            description="Test dataset",
            data_type="kv",
            created_at=datetime(2024, 7, 3, tzinfo=timezone.utc),
            modified_at=datetime(2024, 7, 3, tzinfo=timezone.utc),
            example_count=10,
        )
        mock_client.list_datasets.return_value = iter([mock_dataset])

        result = runner.invoke(cli, ["datasets", "list", "--output", str(output_file)])

        assert result.exit_code == 0
        assert output_file.exists()
        content = output_file.read_text()
        assert "test-dataset" in content
        assert "Wrote 1 items" in strip_ansi(result.output)


def test_examples_list_with_output(runner, tmp_path):
    """Test examples list with --output writes to file."""
    output_file = tmp_path / "examples_output.jsonl"

    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        mock_example = Example(
            id=UUID("11111111-1111-1111-1111-111111111111"),
            dataset_id=UUID("ae99b6fa-a6db-4f1c-8868-bc6764f4c29e"),
            inputs={"question": "What is 2+2?"},
            outputs={"answer": "4"},
            created_at=datetime(2024, 7, 3, tzinfo=timezone.utc),
        )
        mock_client.list_examples.return_value = iter([mock_example])

        result = runner.invoke(
            cli,
            [
                "examples",
                "list",
                "--dataset",
                "test-dataset",
                "--output",
                str(output_file),
            ],
        )

        assert result.exit_code == 0
        assert output_file.exists()
        content = output_file.read_text()
        assert "What is 2+2?" in content
        assert "Wrote 1 items" in strip_ansi(result.output)


def test_output_with_fields_filter(runner, tmp_path):
    """Test --output with --fields applies field filtering."""
    output_file = tmp_path / "runs_filtered.jsonl"

    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        mock_run = Run(
            id=UUID("12345678-1234-1234-1234-123456789abc"),
            name="test-run",
            run_type="chain",
            status="success",
            start_time=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end_time=datetime(2024, 1, 1, tzinfo=timezone.utc),
        )
        mock_client.list_runs.return_value = [mock_run]
        mock_client.list_projects.return_value = [
            TracerSessionResult(
                id=UUID("87654321-4321-4321-4321-cba987654321"),
                name="default",
                tenant_id=UUID("00000000-0000-0000-0000-000000000000"),
                reference_dataset_id=None,
            )
        ]

        result = runner.invoke(
            cli,
            [
                "runs",
                "list",
                "--fields",
                "id,name",
                "--output",
                str(output_file),
            ],
        )

        assert result.exit_code == 0
        assert output_file.exists()
        content = output_file.read_text()
        # Should contain only id and name fields
        assert "test-run" in content
        assert "id" in content
        # Should NOT contain other fields like run_type
        assert "run_type" not in content
