"""Tests for --roots flag behavior and error handling.

These tests are designed to catch the ergonomics issues discovered:
1. Silent failures when API errors occur
2. Exit code 0 even when all projects fail
3. Warnings going to stderr being missed when stdout is redirected

The original issue: A user ran:
    langsmith-cli --json runs list --project "dev/moments" --last "7d" --limit 500 --roots > file.jsonl

And got an empty `[]` result. Later, the same command worked and returned 9 root runs.
The problem was a transient API failure that was silently swallowed - the user
saw exit code 0 and an empty array, with no indication that something went wrong.
"""

from langsmith_cli.main import cli
from unittest.mock import patch
from uuid import UUID
import json

from conftest import create_run


def test_runs_list_api_failure_returns_nonzero_exit_code_in_json_mode(runner):
    """INVARIANT: When API completely fails, exit code should be non-zero.

    This catches the bug where `--json runs list --roots` silently returns []
    when the API fails, with exit code 0 (success).

    The user expects: if something goes wrong, tell me about it!
    """
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        # Simulate API failure (timeout, network error, etc.)
        mock_client.list_runs.side_effect = Exception("Connection timeout")

        result = runner.invoke(cli, ["--json", "runs", "list", "--project", "test"])

        # Exit code must be non-zero when API fails
        assert result.exit_code != 0

        # Error message should be visible (in output since CliRunner captures both streams)
        assert (
            "Failed to fetch" in result.output or "Connection timeout" in result.output
        )


def test_runs_list_api_failure_shows_error_in_output_or_stderr(runner):
    """INVARIANT: When API fails, error information must be visible.

    Users who run `langsmith-cli --json runs list > output.json` should
    know when something goes wrong. Either:
    1. Error message in stderr (which they should see)
    2. Error info included in JSON output
    3. Non-zero exit code (which scripts can check)

    The CLI now:
    - Outputs empty JSON array (for parseable output)
    - Logs errors to stderr
    - Returns non-zero exit code
    """
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        mock_client.list_runs.side_effect = Exception("API Error: Rate limited")

        result = runner.invoke(cli, ["--json", "runs", "list", "--project", "test"])

        # Exit code must be non-zero
        assert result.exit_code != 0

        # Output should contain error information
        assert "Rate limited" in result.output or "Failed to fetch" in result.output

        # Empty JSON array should be output for parseable output
        # (The error message comes after, so we check it's in there)
        assert "[]" in result.output


def test_runs_list_roots_flag_passes_is_root_to_api(runner):
    """INVARIANT: --roots flag must pass is_root=True to the LangSmith API.

    This verifies the basic contract that --roots translates to is_root=True
    in the API call.
    """
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        mock_client.list_runs.return_value = []

        runner.invoke(cli, ["runs", "list", "--project", "test", "--roots"])

        mock_client.list_runs.assert_called_once()
        call_kwargs = mock_client.list_runs.call_args[1]
        assert call_kwargs["is_root"] is True


def test_runs_list_roots_with_limit_passes_correct_limit(runner):
    """INVARIANT: --roots with --limit should pass the limit to API.

    This tests that combining --roots with --limit doesn't cause issues.
    Both small and large limits should work correctly.
    """
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        mock_client.list_runs.return_value = []

        # Test with limit 500 (the failing case from the user report)
        runner.invoke(
            cli, ["runs", "list", "--project", "test", "--roots", "--limit", "500"]
        )

        call_kwargs = mock_client.list_runs.call_args[1]
        assert call_kwargs["is_root"] is True
        assert call_kwargs["limit"] == 500


def test_runs_list_roots_with_fields_returns_filtered_output(runner):
    """INVARIANT: --roots with --fields should return runs with only specified fields.

    The --fields parameter filters output but should not affect which runs
    are returned from the API.
    """
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        # Create root run (parent_run_id=None)
        root_run = create_run(
            name="Root Run",
            id_str="11111111-1111-1111-1111-111111111111",
            inputs={"question": "What is 2+2?"},
            outputs={"answer": "4"},
        )
        mock_client.list_runs.return_value = [root_run]

        result = runner.invoke(
            cli,
            [
                "--json",
                "runs",
                "list",
                "--project",
                "test",
                "--roots",
                "--fields",
                "id,name,status",
            ],
        )

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data) == 1

        # Verify only requested fields are present
        run_data = data[0]
        assert "id" in run_data
        assert "name" in run_data
        assert "status" in run_data
        # These fields should NOT be in the output due to --fields filtering
        assert "inputs" not in run_data
        assert "outputs" not in run_data


def test_runs_list_roots_with_time_filter_passes_both_to_api(runner):
    """INVARIANT: --roots with --last should pass both filters to API.

    The --roots and time filters should work together.
    """
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        mock_client.list_runs.return_value = []

        runner.invoke(
            cli,
            ["runs", "list", "--project", "test", "--roots", "--last", "7d"],
        )

        call_kwargs = mock_client.list_runs.call_args[1]
        assert call_kwargs["is_root"] is True
        # Verify time filter is in the filter string
        assert call_kwargs["filter"] is not None
        assert "start_time" in call_kwargs["filter"]


def test_runs_list_json_empty_result_is_valid_json_array(runner):
    """INVARIANT: Empty results should return valid JSON array [].

    Even when no runs match filters, output must be valid JSON.
    """
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        mock_client.list_runs.return_value = []

        result = runner.invoke(
            cli, ["--json", "runs", "list", "--project", "test", "--roots"]
        )

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data == []


def test_runs_list_multiple_roots_and_children_only_roots_returned_when_flag_set(
    runner,
):
    """INVARIANT: When --roots is used, only root runs should be in output.

    This tests the actual filtering behavior when we have a mix of root
    and child runs.
    """
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        # The API should only return root runs when is_root=True is passed
        # So we mock the API returning only root runs
        root_run = create_run(
            name="Root Chain",
            id_str="11111111-1111-1111-1111-111111111111",
            parent_run_id=None,  # This is a root run
        )
        mock_client.list_runs.return_value = [root_run]

        result = runner.invoke(
            cli, ["--json", "runs", "list", "--project", "test", "--roots"]
        )

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data) == 1
        assert data[0]["name"] == "Root Chain"

        # Verify is_root=True was passed
        call_kwargs = mock_client.list_runs.call_args[1]
        assert call_kwargs["is_root"] is True


def test_runs_list_roots_combined_with_multiple_options(runner):
    """INVARIANT: --roots should work correctly with multiple other options.

    Test that --roots doesn't conflict with:
    - --limit
    - --last
    - --fields
    - --json
    All combined together.
    """
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        root_run = create_run(
            name="task-segment-identification-chain",
            id_str="11111111-1111-1111-1111-111111111111",
        )
        mock_client.list_runs.return_value = [root_run]

        result = runner.invoke(
            cli,
            [
                "--json",
                "runs",
                "list",
                "--project",
                "dev/moments",
                "--last",
                "7d",
                "--limit",
                "100",
                "--roots",
                "--fields",
                "id,name,status",
            ],
        )

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data) == 1

        # Verify correct API call
        call_kwargs = mock_client.list_runs.call_args[1]
        assert call_kwargs["is_root"] is True
        assert call_kwargs["limit"] == 100
        assert "start_time" in call_kwargs["filter"]


def test_create_run_helper_supports_parent_run_id():
    """Test that the create_run helper correctly sets parent_run_id.

    This is a unit test for our test helper.
    """
    # Root run (no parent)
    root = create_run(name="Root", id_str="11111111-1111-1111-1111-111111111111")
    assert root.parent_run_id is None
    assert root.trace_id == UUID("11111111-1111-1111-1111-111111111111")

    # Child run (has parent)
    child = create_run(
        name="Child",
        id_str="22222222-2222-2222-2222-222222222222",
        parent_run_id="11111111-1111-1111-1111-111111111111",
        trace_id="11111111-1111-1111-1111-111111111111",
    )
    assert child.parent_run_id == UUID("11111111-1111-1111-1111-111111111111")
    assert child.trace_id == UUID("11111111-1111-1111-1111-111111111111")


def test_runs_list_roots_with_high_limit_same_behavior_as_low_limit(runner):
    """INVARIANT: --roots behavior should be identical regardless of limit value.

    The user reported that --limit 500 returned [] while --limit 100 returned
    9 runs. This should never happen - the limit only affects how many results
    are returned, not which ones.
    """
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        root_runs = [
            create_run(
                name=f"Root-{i}", id_str=f"1111111{i}-1111-1111-1111-111111111111"
            )
            for i in range(9)
        ]

        # Test with limit 100
        mock_client.list_runs.return_value = root_runs
        result_100 = runner.invoke(
            cli,
            [
                "--json",
                "runs",
                "list",
                "--project",
                "test",
                "--roots",
                "--limit",
                "100",
            ],
        )

        # Test with limit 500
        mock_client.list_runs.return_value = root_runs
        result_500 = runner.invoke(
            cli,
            [
                "--json",
                "runs",
                "list",
                "--project",
                "test",
                "--roots",
                "--limit",
                "500",
            ],
        )

        # Both should return the same runs
        data_100 = json.loads(result_100.output)
        data_500 = json.loads(result_500.output)

        assert len(data_100) == len(data_500) == 9
        assert result_100.exit_code == result_500.exit_code == 0
