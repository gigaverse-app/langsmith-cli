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


def test_datasets_list_with_dataset_ids(runner):
    """INVARIANT: --dataset-ids should filter by comma-separated IDs."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        d1 = create_dataset(name="dataset-1", example_count=10)

        mock_client.list_datasets.return_value = iter([d1])

        result = runner.invoke(
            cli,
            [
                "datasets",
                "list",
                "--dataset-ids",
                "ae99b6fa-a6db-4f1c-8868-bc6764f4c29e",
            ],
        )
        assert result.exit_code == 0
        mock_client.list_datasets.assert_called_once()
        call_kwargs = mock_client.list_datasets.call_args[1]
        assert call_kwargs["dataset_ids"] == ["ae99b6fa-a6db-4f1c-8868-bc6764f4c29e"]


def test_datasets_list_with_exclude(runner):
    """INVARIANT: --exclude should filter out datasets by name substring."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        d1 = create_dataset(name="production-data", example_count=10)
        d2 = create_dataset(name="test-data", example_count=5)

        mock_client.list_datasets.return_value = iter([d1, d2])

        # Exclude uses substring matching, not glob patterns
        result = runner.invoke(cli, ["--json", "datasets", "list", "--exclude", "test"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data) == 1
        assert data[0]["name"] == "production-data"


def test_datasets_list_with_count(runner):
    """INVARIANT: --count should output only the count of datasets."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        datasets = [create_dataset(name=f"dataset-{i}") for i in range(3)]
        mock_client.list_datasets.return_value = iter(datasets)

        result = runner.invoke(cli, ["--json", "datasets", "list", "--count"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data == 3


def test_datasets_list_with_output_file(runner, tmp_path):
    """INVARIANT: --output should write datasets to a JSONL file."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        d1 = create_dataset(name="dataset-1", example_count=10)
        d2 = create_dataset(name="dataset-2", example_count=20)

        mock_client.list_datasets.return_value = iter([d1, d2])

        output_file = tmp_path / "datasets.jsonl"
        result = runner.invoke(cli, ["datasets", "list", "--output", str(output_file)])
        assert result.exit_code == 0

        # Verify file was written
        assert output_file.exists()
        lines = output_file.read_text().strip().split("\n")
        assert len(lines) == 2
        data1 = json.loads(lines[0])
        assert data1["name"] == "dataset-1"


def test_datasets_get_table_output(runner):
    """INVARIANT: datasets get without --json should show table-like output."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        d1 = create_dataset(
            name="my-dataset", description="My description", example_count=42
        )
        mock_client.read_dataset.return_value = d1

        result = runner.invoke(
            cli, ["datasets", "get", "ae99b6fa-a6db-4f1c-8868-bc6764f4c29e"]
        )
        assert result.exit_code == 0
        assert "my-dataset" in result.output
        assert "My description" in result.output


def test_datasets_get_json(runner):
    """INVARIANT: datasets get with --json should return valid JSON."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        d1 = create_dataset(name="my-dataset", example_count=42)
        mock_client.read_dataset.return_value = d1

        result = runner.invoke(
            cli, ["--json", "datasets", "get", "ae99b6fa-a6db-4f1c-8868-bc6764f4c29e"]
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["name"] == "my-dataset"


def test_datasets_get_with_fields(runner):
    """INVARIANT: --fields should limit returned fields."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        d1 = create_dataset(name="my-dataset", description="Desc", example_count=42)
        mock_client.read_dataset.return_value = d1

        result = runner.invoke(
            cli,
            [
                "--json",
                "datasets",
                "get",
                "ae99b6fa-a6db-4f1c-8868-bc6764f4c29e",
                "--fields",
                "name,id",
            ],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "name" in data
        assert "id" in data
        # Other fields should be filtered out
        assert "description" not in data


def test_datasets_create(runner):
    """INVARIANT: datasets create should create a new dataset."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        created_dataset = create_dataset(name="new-dataset", description="New dataset")
        mock_client.create_dataset.return_value = created_dataset

        result = runner.invoke(
            cli,
            ["datasets", "create", "new-dataset", "--description", "New dataset"],
        )
        assert result.exit_code == 0
        mock_client.create_dataset.assert_called_once()
        assert "new-dataset" in result.output


def test_datasets_create_json(runner):
    """INVARIANT: datasets create with --json should return valid JSON."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        created_dataset = create_dataset(name="new-dataset")
        mock_client.create_dataset.return_value = created_dataset

        result = runner.invoke(cli, ["--json", "datasets", "create", "new-dataset"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["name"] == "new-dataset"


def test_datasets_create_with_type(runner):
    """INVARIANT: --type should set the dataset type."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        created_dataset = create_dataset(name="chat-dataset", data_type="chat")
        mock_client.create_dataset.return_value = created_dataset

        result = runner.invoke(
            cli, ["datasets", "create", "chat-dataset", "--type", "chat"]
        )
        assert result.exit_code == 0
        mock_client.create_dataset.assert_called_once()
        call_kwargs = mock_client.create_dataset.call_args[1]
        # data_type should be a DataType enum
        assert call_kwargs["data_type"].value == "chat"


def test_datasets_push(runner, tmp_path):
    """INVARIANT: datasets push should upload examples from a JSONL file."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        # Create a JSONL file with examples
        jsonl_file = tmp_path / "examples.jsonl"
        examples = [
            {"inputs": {"text": "hello"}, "outputs": {"result": "world"}},
            {"inputs": {"text": "foo"}, "outputs": {"result": "bar"}},
        ]
        jsonl_file.write_text("\n".join(json.dumps(e) for e in examples))

        # Mock read_dataset to succeed (dataset exists)
        mock_client.read_dataset.return_value = create_dataset(name="target-dataset")

        result = runner.invoke(
            cli, ["datasets", "push", str(jsonl_file), "--dataset", "target-dataset"]
        )
        assert result.exit_code == 0
        mock_client.create_examples.assert_called_once()
        call_kwargs = mock_client.create_examples.call_args[1]
        assert len(call_kwargs["inputs"]) == 2
        assert call_kwargs["dataset_name"] == "target-dataset"


def test_datasets_push_creates_dataset_if_not_exists(runner, tmp_path):
    """INVARIANT: datasets push should create dataset if it doesn't exist."""
    from langsmith.utils import LangSmithNotFoundError

    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        # Create a JSONL file with examples
        jsonl_file = tmp_path / "examples.jsonl"
        examples = [{"inputs": {"text": "hello"}, "outputs": {"result": "world"}}]
        jsonl_file.write_text("\n".join(json.dumps(e) for e in examples))

        # Mock read_dataset to raise NotFoundError
        mock_client.read_dataset.side_effect = LangSmithNotFoundError("Not found")
        mock_client.create_dataset.return_value = create_dataset(name="new-dataset")

        result = runner.invoke(
            cli, ["datasets", "push", str(jsonl_file), "--dataset", "new-dataset"]
        )
        assert result.exit_code == 0
        # Should create dataset when not found
        mock_client.create_dataset.assert_called_once()
        mock_client.create_examples.assert_called_once()


def test_datasets_push_uses_filename_as_default_dataset(runner, tmp_path):
    """INVARIANT: datasets push should use filename as dataset name if not specified."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        # Create a JSONL file with examples
        jsonl_file = tmp_path / "my-examples.jsonl"
        examples = [{"inputs": {"text": "hello"}, "outputs": {"result": "world"}}]
        jsonl_file.write_text("\n".join(json.dumps(e) for e in examples))

        mock_client.read_dataset.return_value = create_dataset(name="my-examples")

        result = runner.invoke(cli, ["datasets", "push", str(jsonl_file)])
        assert result.exit_code == 0
        mock_client.create_examples.assert_called_once()
        call_kwargs = mock_client.create_examples.call_args[1]
        # Should use filename without extension as dataset name
        assert call_kwargs["dataset_name"] == "my-examples"
