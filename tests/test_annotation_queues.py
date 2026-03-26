"""
Tests for annotation-queues command group.

INVARIANT: annotation-queues list/get/create/update/delete commands must be
reachable and produce valid output backed by real Pydantic AnnotationQueue models.
"""

from unittest.mock import patch

from langsmith.utils import LangSmithNotFoundError

from langsmith_cli.main import cli
from conftest import create_annotation_queue, strip_ansi, parse_json_output


def test_annotation_queues_list_exits_zero(runner):
    """INVARIANT: annotation-queues list exits 0 with empty queue list."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        mock_client.list_annotation_queues.return_value = iter([])
        result = runner.invoke(cli, ["annotation-queues", "list"])
        assert result.exit_code == 0


def test_annotation_queues_list_shows_items(runner):
    """INVARIANT: annotation-queues list displays queue names in table."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        q1 = create_annotation_queue(
            id_str="33333333-3333-3333-3333-333333333333",
            name="review-queue",
        )
        q2 = create_annotation_queue(
            id_str="44444444-4444-4444-4444-444444444444",
            name="quality-queue",
        )
        mock_client.list_annotation_queues.return_value = iter([q1, q2])
        result = runner.invoke(cli, ["annotation-queues", "list"])
        assert result.exit_code == 0
        output = strip_ansi(result.output)
        assert "review-queue" in output
        assert "quality-queue" in output


def test_annotation_queues_list_json(runner):
    """INVARIANT: --json flag produces a valid JSON list with queue fields."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        q = create_annotation_queue(
            id_str="33333333-3333-3333-3333-333333333333",
            name="test-queue",
        )
        mock_client.list_annotation_queues.return_value = iter([q])
        result = runner.invoke(cli, ["--json", "annotation-queues", "list"])
        assert result.exit_code == 0
        data = parse_json_output(result.output)
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["name"] == "test-queue"


def test_annotation_queues_get_exits_zero(runner):
    """INVARIANT: annotation-queues get returns a single queue by ID."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        q = create_annotation_queue(id_str="33333333-3333-3333-3333-333333333333")
        mock_client.read_annotation_queue.return_value = q
        result = runner.invoke(
            cli, ["annotation-queues", "get", "33333333-3333-3333-3333-333333333333"]
        )
        assert result.exit_code == 0


def test_annotation_queues_get_json(runner):
    """INVARIANT: annotation-queues get --json returns queue fields as JSON."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        q = create_annotation_queue(
            id_str="33333333-3333-3333-3333-333333333333",
            name="my-queue",
        )
        mock_client.read_annotation_queue.return_value = q
        result = runner.invoke(
            cli,
            [
                "--json",
                "annotation-queues",
                "get",
                "33333333-3333-3333-3333-333333333333",
            ],
        )
        assert result.exit_code == 0
        data = parse_json_output(result.output)
        assert data["name"] == "my-queue"


def test_annotation_queues_create_exits_zero(runner):
    """INVARIANT: annotation-queues create calls create_annotation_queue and exits 0."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        q = create_annotation_queue(name="new-queue")
        mock_client.create_annotation_queue.return_value = q
        result = runner.invoke(cli, ["annotation-queues", "create", "new-queue"])
        assert result.exit_code == 0
        mock_client.create_annotation_queue.assert_called_once()


def test_annotation_queues_create_with_description(runner):
    """INVARIANT: --description is passed to create_annotation_queue SDK call."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        q = create_annotation_queue(name="new-queue")
        mock_client.create_annotation_queue.return_value = q
        result = runner.invoke(
            cli,
            [
                "annotation-queues",
                "create",
                "new-queue",
                "--description",
                "My review queue",
            ],
        )
        assert result.exit_code == 0
        call_kwargs = mock_client.create_annotation_queue.call_args[1]
        assert call_kwargs.get("description") == "My review queue"


def test_annotation_queues_update_exits_zero(runner):
    """INVARIANT: annotation-queues update calls update_annotation_queue and exits 0."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        q = create_annotation_queue(
            id_str="33333333-3333-3333-3333-333333333333",
            name="updated-queue",
        )
        mock_client.read_annotation_queue.return_value = q
        mock_client.update_annotation_queue.return_value = q
        result = runner.invoke(
            cli,
            [
                "annotation-queues",
                "update",
                "33333333-3333-3333-3333-333333333333",
                "--name",
                "updated-queue",
            ],
        )
        assert result.exit_code == 0
        mock_client.update_annotation_queue.assert_called_once()


def test_annotation_queues_delete_requires_confirm(runner):
    """INVARIANT: annotation-queues delete without --confirm prompts user."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        q = create_annotation_queue(id_str="33333333-3333-3333-3333-333333333333")
        mock_client.read_annotation_queue.return_value = q
        runner.invoke(
            cli,
            ["annotation-queues", "delete", "33333333-3333-3333-3333-333333333333"],
            input="n\n",
        )
        mock_client.delete_annotation_queue.assert_not_called()


def test_annotation_queues_delete_with_confirm(runner):
    """INVARIANT: annotation-queues delete --confirm calls delete_annotation_queue."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        q = create_annotation_queue(id_str="33333333-3333-3333-3333-333333333333")
        mock_client.read_annotation_queue.return_value = q
        mock_client.delete_annotation_queue.return_value = None
        result = runner.invoke(
            cli,
            [
                "annotation-queues",
                "delete",
                "33333333-3333-3333-3333-333333333333",
                "--confirm",
            ],
        )
        assert result.exit_code == 0
        mock_client.delete_annotation_queue.assert_called_once()


def test_annotation_queues_get_not_found(runner):
    """INVARIANT: annotation-queues get exits non-zero when queue ID not found."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        mock_client.read_annotation_queue.side_effect = LangSmithNotFoundError(
            "not found"
        )
        result = runner.invoke(
            cli,
            ["annotation-queues", "get", "33333333-3333-3333-3333-333333333333"],
        )
        assert result.exit_code != 0
        assert "not found" in result.output.lower()


def test_annotation_queues_update_not_found(runner):
    """INVARIANT: annotation-queues update exits non-zero when queue ID not found."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        mock_client.read_annotation_queue.side_effect = LangSmithNotFoundError(
            "not found"
        )
        result = runner.invoke(
            cli,
            [
                "annotation-queues",
                "update",
                "33333333-3333-3333-3333-333333333333",
                "--name",
                "new-name",
            ],
        )
        assert result.exit_code != 0
        assert "not found" in result.output.lower()
