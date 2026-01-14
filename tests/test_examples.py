"""
Permanent tests for examples command.

These tests use mocked data and will continue to work indefinitely.
"""

from langsmith_cli.main import cli
from unittest.mock import patch, MagicMock
import json


def test_examples_list(runner):
    """INVARIANT: Examples list should return examples with correct structure."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        # Create mock examples
        ex1 = MagicMock()
        ex1.id = "3442bd7c-27a2-437b-a38c-f278e455d87b"
        ex1.dataset_id = "ae99b6fa-a6db-4f1c-8868-bc6764f4c29e"
        ex1.inputs = {"text": "Example input 1"}
        ex1.outputs = {"result": "Example output 1"}
        ex1.created_at = "2024-08-15T19:47:22.513097+00:00"

        ex2 = MagicMock()
        ex2.id = "05da0305-224c-4b3c-9662-671146ee94a5"
        ex2.dataset_id = "ae99b6fa-a6db-4f1c-8868-bc6764f4c29e"
        ex2.inputs = {"text": "Example input 2"}
        ex2.outputs = {"result": "Example output 2"}
        ex2.created_at = "2024-08-15T19:47:00.263129+00:00"

        mock_client.list_examples.return_value = iter([ex1, ex2])

        result = runner.invoke(cli, ["examples", "list", "--dataset", "test-dataset"])
        assert result.exit_code == 0


def test_examples_list_json(runner):
    """INVARIANT: JSON output should be valid with example fields."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        ex1 = MagicMock()
        ex1.id = "3442bd7c-27a2-437b-a38c-f278e455d87b"
        ex1.dataset_id = "ae99b6fa-a6db-4f1c-8868-bc6764f4c29e"
        ex1.inputs = {"text": "Example input"}
        ex1.outputs = {"result": "Example output"}
        ex1.created_at = "2024-08-15T19:47:22.513097+00:00"
        ex1.model_dump.return_value = {
            "id": "3442bd7c-27a2-437b-a38c-f278e455d87b",
            "dataset_id": "ae99b6fa-a6db-4f1c-8868-bc6764f4c29e",
            "inputs": {"text": "Example input"},
            "outputs": {"result": "Example output"},
        }

        mock_client.list_examples.return_value = iter([ex1])

        result = runner.invoke(
            cli, ["--json", "examples", "list", "--dataset", "test-dataset"]
        )
        assert result.exit_code == 0

        data = json.loads(result.output)
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["id"] == "3442bd7c-27a2-437b-a38c-f278e455d87b"


def test_examples_list_with_limit(runner):
    """INVARIANT: --limit parameter should be passed to API."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        # Create 5 mock examples
        examples = []
        for i in range(5):
            ex = MagicMock()
            ex.id = f"id-{i}"
            ex.dataset_id = "dataset-id"
            ex.inputs = {"text": f"input {i}"}
            ex.outputs = {"result": f"output {i}"}
            examples.append(ex)

        mock_client.list_examples.return_value = iter(examples[:3])

        result = runner.invoke(
            cli, ["examples", "list", "--dataset", "test-dataset", "--limit", "3"]
        )
        assert result.exit_code == 0
        mock_client.list_examples.assert_called_once()
        call_kwargs = mock_client.list_examples.call_args[1]
        assert call_kwargs["limit"] == 3


def test_examples_list_with_offset(runner):
    """INVARIANT: --offset should skip first N examples."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        ex1 = MagicMock()
        ex1.id = "id-1"
        ex1.dataset_id = "dataset-id"
        ex1.inputs = {"text": "input 1"}
        ex1.outputs = {"result": "output 1"}

        ex2 = MagicMock()
        ex2.id = "id-2"
        ex2.dataset_id = "dataset-id"
        ex2.inputs = {"text": "input 2"}
        ex2.outputs = {"result": "output 2"}

        mock_client.list_examples.return_value = iter([ex2])

        result = runner.invoke(
            cli,
            [
                "examples",
                "list",
                "--dataset",
                "test-dataset",
                "--limit",
                "1",
                "--offset",
                "1",
            ],
        )
        assert result.exit_code == 0
        mock_client.list_examples.assert_called_once()
        call_kwargs = mock_client.list_examples.call_args[1]
        assert call_kwargs["offset"] == 1


def test_examples_list_with_splits_filter(runner):
    """INVARIANT: --splits parameter should filter by dataset split."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        ex1 = MagicMock()
        ex1.id = "id-1"
        ex1.dataset_id = "dataset-id"
        ex1.inputs = {"text": "training data"}
        ex1.outputs = {"result": "result 1"}
        ex1.metadata = {"dataset_split": ["train"]}

        mock_client.list_examples.return_value = iter([ex1])

        result = runner.invoke(
            cli,
            ["examples", "list", "--dataset", "test-dataset", "--splits", "train"],
        )
        assert result.exit_code == 0
        mock_client.list_examples.assert_called_once()
        call_kwargs = mock_client.list_examples.call_args[1]
        # --splits is parsed as a list by parse_comma_separated_list()
        assert call_kwargs.get("splits") == ["train"]


def test_examples_list_by_dataset_name(runner):
    """INVARIANT: Examples should be retrievable by dataset name."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        ex1 = MagicMock()
        ex1.id = "id-1"
        ex1.dataset_id = "ae99b6fa-a6db-4f1c-8868-bc6764f4c29e"
        ex1.inputs = {"text": "Example"}
        ex1.outputs = {"result": "Result"}

        mock_client.list_examples.return_value = iter([ex1])

        result = runner.invoke(
            cli, ["examples", "list", "--dataset", "ds-soundbites-baseset"]
        )
        assert result.exit_code == 0


def test_examples_list_empty_results(runner):
    """INVARIANT: Empty results should be handled gracefully."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        mock_client.list_examples.return_value = iter([])

        result = runner.invoke(cli, ["examples", "list", "--dataset", "empty-dataset"])
        assert result.exit_code == 0


def test_examples_list_without_dataset_returns_empty(runner):
    """INVARIANT: Examples list without --dataset should handle gracefully."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        # When no dataset specified, list_examples returns empty
        mock_client.list_examples.return_value = iter([])

        result = runner.invoke(cli, ["examples", "list"])
        # Should succeed with empty results message
        assert result.exit_code == 0
