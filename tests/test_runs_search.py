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
        assert kwargs["query"] == "test"
        assert kwargs["filter"] is None

    def test_search_with_roots_flag(self, runner, mock_client):
        """Search command supports --roots flag."""
        mock_client.list_runs.return_value = []

        runner.invoke(cli, ["runs", "search", "error", "--roots"])

        _, kwargs = mock_client.list_runs.call_args
        assert kwargs["is_root"] is True

    @pytest.mark.parametrize(
        "extra_args,expected_grep,expected_grep_in",
        [
            (["--input-contains", "email"], "email", "inputs"),
            (["--output-contains", "timeout"], "timeout", "outputs"),
        ],
    )
    def test_search_with_contains_flags(
        self, runner, mock_client, extra_args, expected_grep, expected_grep_in
    ):
        """--input-contains and --output-contains add scoped grep terms."""
        if expected_grep_in == "inputs":
            mock_client.list_runs.return_value = [
                create_run(name="matching-run", inputs={"text": expected_grep}),
                create_run(name="other-run", inputs={"text": "other"}),
            ]
        else:
            mock_client.list_runs.return_value = [
                create_run(name="matching-run", outputs={"text": expected_grep}),
                create_run(name="other-run", outputs={"text": "other"}),
            ]

        result = runner.invoke(cli, ["runs", "search", "user_123"] + extra_args)

        assert result.exit_code == 0
        assert "matching-run" in result.output
        assert "other-run" not in result.output
        _, kwargs = mock_client.list_runs.call_args
        assert kwargs["query"] == "user_123"
        assert kwargs["filter"] is None
        assert "select" not in kwargs

    def test_search_scoped_inputs_uses_grep(self, runner, mock_client):
        """--in inputs performs scoped content search instead of misleading broad FQL."""
        mock_client.list_runs.return_value = [
            create_run(name="matching-run", inputs={"text": "user_123"}),
            create_run(name="other-run", outputs={"text": "user_123"}),
        ]

        result = runner.invoke(cli, ["runs", "search", "user_123", "--in", "inputs"])

        assert result.exit_code == 0
        assert "matching-run" in result.output
        assert "other-run" not in result.output
        _, kwargs = mock_client.list_runs.call_args
        assert kwargs["query"] is None
        assert kwargs["filter"] is None
        assert kwargs["limit"] == 100

    def test_search_fields_push_down_select(self, runner, mock_client):
        """--fields in JSON mode is pushed to SDK select."""
        mock_client.list_runs.return_value = []

        result = runner.invoke(
            cli,
            ["--json", "runs", "search", "timeout", "--fields", "id,name,status"],
        )

        assert result.exit_code == 0
        _, kwargs = mock_client.list_runs.call_args
        assert kwargs["query"] == "timeout"
        assert kwargs["select"] == ["id", "name", "status"]

    def test_search_retries_with_fql_when_query_rejected(self, runner, mock_client):
        """Plain search falls back to FQL search() if LangSmith rejects freeform query."""
        from langsmith.utils import LangSmithError

        mock_client.list_runs.side_effect = [
            LangSmithError("Failed to generate filter from freeform query"),
            [create_run(name="search-result")],
        ]

        result = runner.invoke(cli, ["runs", "search", "test"])

        assert result.exit_code == 0
        assert "search-result" in result.output
        assert mock_client.list_runs.call_count == 2
        first_call = mock_client.list_runs.call_args_list[0][1]
        second_call = mock_client.list_runs.call_args_list[1][1]
        assert first_call["query"] == "test"
        assert second_call["query"] is None
        assert second_call["filter"] == 'search("test")'

    def test_order_by_not_passed_to_api(self, runner, mock_client):
        """INVARIANT: order_by must NOT be passed to client.list_runs() — API rejects it with 400."""
        mock_client.list_runs.return_value = []

        runner.invoke(cli, ["runs", "search", "test"])

        call_kwargs = mock_client.list_runs.call_args[1]
        assert "order_by" not in call_kwargs, (
            "order_by should not be passed to list_runs — LangSmith API rejects it with 400 Bad Request"
        )
