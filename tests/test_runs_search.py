"""Tests for runs search command."""

import pytest

from conftest import create_run
from langsmith_cli.main import cli


class TestRunsSearch:
    """Tests for runs search command."""

    def test_search_basic(self, runner, mock_client):
        """Search command finds runs matching query."""
        mock_client.list_runs.return_value = [create_run(name="search-result")]

        result = runner.invoke(cli, ["runs", "search", "test"])

        assert result.exit_code == 0
        assert "search-result" in result.output
        _, kwargs = mock_client.list_runs.call_args
        assert 'search("test")' in kwargs["filter"]

    def test_search_with_roots_flag(self, runner, mock_client):
        """Search command supports --roots flag."""
        mock_client.list_runs.return_value = []

        runner.invoke(cli, ["runs", "search", "error", "--roots"])

        _, kwargs = mock_client.list_runs.call_args
        assert kwargs["is_root"] is True

    @pytest.mark.parametrize(
        "extra_args,expected_search",
        [
            (["--input-contains", "email"], 'search("email")'),
            (["--output-contains", "timeout"], 'search("timeout")'),
        ],
    )
    def test_search_with_contains_flags(
        self, runner, mock_client, extra_args, expected_search
    ):
        """--input-contains and --output-contains add search terms."""
        mock_client.list_runs.return_value = []

        result = runner.invoke(cli, ["runs", "search", "user_123"] + extra_args)

        assert result.exit_code == 0
        _, kwargs = mock_client.list_runs.call_args
        assert 'search("user_123")' in kwargs["filter"]
        assert expected_search in kwargs["filter"]

    def test_order_by_not_passed_to_api(self, runner, mock_client):
        """INVARIANT: order_by must NOT be passed to client.list_runs() — API rejects it with 400."""
        mock_client.list_runs.return_value = []

        runner.invoke(cli, ["runs", "search", "test"])

        call_kwargs = mock_client.list_runs.call_args[1]
        assert "order_by" not in call_kwargs, (
            "order_by should not be passed to list_runs — LangSmith API rejects it with 400 Bad Request"
        )
