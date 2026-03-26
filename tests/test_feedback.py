"""
Tests for feedback command group.

INVARIANT: feedback list/get/create/delete commands must be reachable
and produce valid output (table or JSON) backed by real Pydantic Feedback models.
"""

from unittest.mock import patch

from langsmith_cli.main import cli
from conftest import create_feedback, strip_ansi, parse_json_output


def test_feedback_list_exits_zero(runner):
    """INVARIANT: feedback list exits 0 with empty feedback list."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        mock_client.list_feedback.return_value = iter([])
        result = runner.invoke(cli, ["feedback", "list"])
        assert result.exit_code == 0


def test_feedback_list_shows_items(runner):
    """INVARIANT: feedback list displays feedback keys and scores in table."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        fb1 = create_feedback(
            id_str="11111111-1111-1111-1111-111111111111",
            key="correctness",
            score=0.9,
        )
        fb2 = create_feedback(
            id_str="22222222-2222-2222-2222-222222222222",
            key="helpfulness",
            score=0.7,
        )
        mock_client.list_feedback.return_value = iter([fb1, fb2])
        result = runner.invoke(cli, ["feedback", "list"])
        assert result.exit_code == 0
        output = strip_ansi(result.output)
        assert "correctness" in output
        assert "helpfulness" in output


def test_feedback_list_json(runner):
    """INVARIANT: --json flag produces a valid JSON list with feedback fields."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        fb = create_feedback(
            id_str="11111111-1111-1111-1111-111111111111",
            key="correctness",
            score=0.9,
        )
        mock_client.list_feedback.return_value = iter([fb])
        result = runner.invoke(cli, ["--json", "feedback", "list"])
        assert result.exit_code == 0
        data = parse_json_output(result.output)
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["key"] == "correctness"
        assert data[0]["score"] == 0.9


def test_feedback_list_with_run_id(runner):
    """INVARIANT: --run-id passes run_ids to the SDK list_feedback call."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        mock_client.list_feedback.return_value = iter([])
        run_id = "22222222-2222-2222-2222-222222222222"
        result = runner.invoke(cli, ["feedback", "list", "--run-id", run_id])
        assert result.exit_code == 0
        mock_client.list_feedback.assert_called_once()
        call_kwargs = mock_client.list_feedback.call_args[1]
        assert run_id in str(call_kwargs.get("run_ids", ""))


def test_feedback_list_with_key_filter(runner):
    """INVARIANT: --key passes feedback_key filter to SDK."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        mock_client.list_feedback.return_value = iter([])
        result = runner.invoke(cli, ["feedback", "list", "--key", "correctness"])
        assert result.exit_code == 0
        mock_client.list_feedback.assert_called_once()
        call_kwargs = mock_client.list_feedback.call_args[1]
        assert "correctness" in str(call_kwargs.get("feedback_key", ""))


def test_feedback_get_exits_zero(runner):
    """INVARIANT: feedback get returns a single feedback item."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        fb = create_feedback(id_str="11111111-1111-1111-1111-111111111111")
        mock_client.read_feedback.return_value = fb
        result = runner.invoke(
            cli, ["feedback", "get", "11111111-1111-1111-1111-111111111111"]
        )
        assert result.exit_code == 0


def test_feedback_get_json(runner):
    """INVARIANT: feedback get --json returns feedback fields as JSON."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        fb = create_feedback(
            id_str="11111111-1111-1111-1111-111111111111",
            key="correctness",
            score=0.9,
        )
        mock_client.read_feedback.return_value = fb
        result = runner.invoke(
            cli,
            ["--json", "feedback", "get", "11111111-1111-1111-1111-111111111111"],
        )
        assert result.exit_code == 0
        data = parse_json_output(result.output)
        assert data["key"] == "correctness"
        assert data["score"] == 0.9


def test_feedback_create_exits_zero(runner):
    """INVARIANT: feedback create calls create_feedback and exits 0."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        fb = create_feedback(id_str="11111111-1111-1111-1111-111111111111")
        mock_client.create_feedback.return_value = fb
        result = runner.invoke(
            cli,
            [
                "feedback",
                "create",
                "22222222-2222-2222-2222-222222222222",
                "--key",
                "correctness",
                "--score",
                "0.9",
            ],
        )
        assert result.exit_code == 0
        mock_client.create_feedback.assert_called_once()


def test_feedback_create_passes_comment(runner):
    """INVARIANT: feedback create --comment passes comment to SDK."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        fb = create_feedback()
        mock_client.create_feedback.return_value = fb
        result = runner.invoke(
            cli,
            [
                "feedback",
                "create",
                "22222222-2222-2222-2222-222222222222",
                "--key",
                "correctness",
                "--comment",
                "Excellent response",
            ],
        )
        assert result.exit_code == 0
        call_kwargs = mock_client.create_feedback.call_args[1]
        assert call_kwargs.get("comment") == "Excellent response"


def test_feedback_delete_requires_confirm(runner):
    """INVARIANT: feedback delete without --confirm prompts for confirmation."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        mock_client.delete_feedback.return_value = None
        runner.invoke(
            cli,
            ["feedback", "delete", "11111111-1111-1111-1111-111111111111"],
            input="n\n",
        )
        mock_client.delete_feedback.assert_not_called()


def test_feedback_delete_with_confirm(runner):
    """INVARIANT: feedback delete --confirm calls delete_feedback."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        mock_client.delete_feedback.return_value = None
        result = runner.invoke(
            cli,
            [
                "feedback",
                "delete",
                "11111111-1111-1111-1111-111111111111",
                "--confirm",
            ],
        )
        assert result.exit_code == 0
        mock_client.delete_feedback.assert_called_once()
