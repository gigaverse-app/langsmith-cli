"""
Permanent tests for datasets command.

These tests use mocked data and will continue to work indefinitely,
unlike E2E tests that depend on real trace data (which expires after 400 days).

All test data is created using real LangSmith Pydantic model instances from
langsmith.schemas, ensuring compatibility with the actual SDK.
"""

from langsmith_cli.main import cli
from unittest.mock import patch
import json
from conftest import create_dataset


def test_datasets_list(runner):
    """INVARIANT: Datasets list should return all datasets with correct structure."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        # Create real Dataset Pydantic instances
        d1 = create_dataset(
            name="ds-soundbites-baseset",
            description="Integration Dataset",
            example_count=111,
        )
        d2 = create_dataset(
            name="ds-factcheck-scoring",
            description="Factcheck Scoring Dataset",
            example_count=4,
            session_count=43,
        )

        mock_client.list_datasets.return_value = iter([d1, d2])

        result = runner.invoke(cli, ["datasets", "list"])
        assert result.exit_code == 0
        assert "ds-soundbites-baseset" in result.output
        assert "ds-factcheck-scoring" in result.output


def test_datasets_list_json(runner):
    """INVARIANT: JSON output should be valid JSON list with dataset fields."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        # Create real Dataset instance
        d1 = create_dataset(name="test-dataset", example_count=10)

        mock_client.list_datasets.return_value = iter([d1])

        result = runner.invoke(cli, ["--json", "datasets", "list"])
        assert result.exit_code == 0

        data = json.loads(result.output)
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["name"] == "test-dataset"
        assert data[0]["example_count"] == 10


def test_datasets_list_with_limit(runner):
    """INVARIANT: --limit parameter should respect the limit."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        # Create real Dataset instances
        datasets = [
            create_dataset(name=f"dataset-{i}", example_count=i * 10) for i in range(5)
        ]

        mock_client.list_datasets.return_value = iter(datasets[:2])

        result = runner.invoke(cli, ["datasets", "list", "--limit", "2"])
        assert result.exit_code == 0
        mock_client.list_datasets.assert_called_once()
        call_kwargs = mock_client.list_datasets.call_args[1]
        assert call_kwargs["limit"] == 2


def test_datasets_list_with_name_filter(runner):
    """INVARIANT: --name-contains should filter datasets by name substring."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        d1 = create_dataset(name="factcheck-dataset", example_count=5)
        d2 = create_dataset(name="other-dataset", example_count=3)

        # Simulate filtering by name
        def list_datasets_side_effect(**kwargs):
            name_contains = kwargs.get("dataset_name_contains")
            if name_contains == "factcheck":
                return iter([d1])
            return iter([d1, d2])

        mock_client.list_datasets.side_effect = list_datasets_side_effect

        result = runner.invoke(
            cli, ["datasets", "list", "--name-contains", "factcheck"]
        )
        assert result.exit_code == 0
        assert "factcheck-dataset" in result.output


def test_datasets_list_with_data_type_filter(runner):
    """INVARIANT: --data-type should filter by dataset type."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        d1 = create_dataset(name="kv-dataset", data_type="kv", example_count=10)

        mock_client.list_datasets.return_value = iter([d1])

        result = runner.invoke(cli, ["datasets", "list", "--data-type", "kv"])
        assert result.exit_code == 0
        mock_client.list_datasets.assert_called_once()
        call_kwargs = mock_client.list_datasets.call_args[1]
        assert call_kwargs["data_type"] == "kv"


def test_datasets_list_empty_results(runner):
    """INVARIANT: Empty results should show appropriate message."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        mock_client.list_datasets.return_value = iter([])

        result = runner.invoke(cli, ["datasets", "list"])
        assert result.exit_code == 0
        # Should handle empty results gracefully
        assert "No datasets found" in result.output or "Datasets" in result.output
