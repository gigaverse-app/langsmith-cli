"""
Permanent tests for datasets command.

These tests use mocked data and will continue to work indefinitely,
unlike E2E tests that depend on real trace data (which expires after 400 days).
"""

from langsmith_cli.main import cli
from unittest.mock import patch, MagicMock
import json


def test_datasets_list(runner):
    """INVARIANT: Datasets list should return all datasets with correct structure."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        # Create mock datasets with real structure
        d1 = MagicMock()
        d1.id = "ae99b6fa-a6db-4f1c-8868-bc6764f4c29e"
        d1.name = "ds-soundbites-baseset"
        d1.description = "Integration Dataset"
        d1.data_type = "kv"
        d1.example_count = 111
        d1.session_count = 1
        d1.created_at = "2024-07-03T09:27:16.098548+00:00"
        d1.modified_at = "2024-07-03T09:27:16.098548+00:00"

        d2 = MagicMock()
        d2.id = "f4057f0c-c31c-49a7-b1d6-b7ca4d50b7e4"
        d2.name = "ds-factcheck-scoring"
        d2.description = "Factcheck Scoring Dataset"
        d2.data_type = "kv"
        d2.example_count = 4
        d2.session_count = 43
        d2.created_at = "2024-06-26T16:42:50.934517+00:00"
        d2.modified_at = "2024-06-26T16:42:50.934517+00:00"

        mock_client.list_datasets.return_value = iter([d1, d2])

        result = runner.invoke(cli, ["datasets", "list"])
        assert result.exit_code == 0
        assert "ds-soundbites-baseset" in result.output
        assert "ds-factcheck-scoring" in result.output


def test_datasets_list_json(runner):
    """INVARIANT: JSON output should be valid JSON list with dataset fields."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        d1 = MagicMock()
        d1.id = "ae99b6fa-a6db-4f1c-8868-bc6764f4c29e"
        d1.name = "test-dataset"
        d1.data_type = "kv"
        d1.example_count = 10
        d1.model_dump.return_value = {
            "id": "ae99b6fa-a6db-4f1c-8868-bc6764f4c29e",
            "name": "test-dataset",
            "data_type": "kv",
            "example_count": 10,
        }

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

        datasets = []
        for i in range(5):
            d = MagicMock()
            d.id = f"id-{i}"
            d.name = f"dataset-{i}"
            d.data_type = "kv"
            d.example_count = i * 10
            datasets.append(d)

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

        d1 = MagicMock()
        d1.id = "1"
        d1.name = "factcheck-dataset"
        d1.data_type = "kv"
        d1.example_count = 5

        d2 = MagicMock()
        d2.id = "2"
        d2.name = "other-dataset"
        d2.data_type = "kv"
        d2.example_count = 3

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

        d1 = MagicMock()
        d1.id = "1"
        d1.name = "kv-dataset"
        d1.data_type = "kv"
        d1.example_count = 10

        d2 = MagicMock()
        d2.id = "2"
        d2.name = "chat-dataset"
        d2.data_type = "chat"
        d2.example_count = 5

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
