from langsmith_cli.main import cli
from unittest.mock import patch, MagicMock
from langsmith.schemas import Run
from datetime import datetime, timezone, timedelta
from uuid import UUID
import pytest
import json
from conftest import create_run


def test_runs_list(runner):
    """Test the runs list command."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        # Use real Run model instead of MagicMock
        test_run = Run(
            id=UUID("12345678-1234-5678-1234-567812345678"),
            name="My Run",
            run_type="chain",
            start_time=datetime.now(timezone.utc),
            status="success",
            latency=0.5,
            error=None,
        )
        mock_client.list_runs.return_value = [test_run]

        result = runner.invoke(cli, ["runs", "list"])
        assert result.exit_code == 0
        assert "12345678-1234-5678-1234-567812345678" in result.output
        assert "My Run" in result.output
        assert "success" in result.output


def test_runs_list_json_output_is_valid_json(runner):
    """Test that runs list --json always outputs valid JSON that can be parsed."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        test_run = Run(
            id=UUID("12345678-1234-5678-1234-567812345678"),
            name="My Run",
            run_type="chain",
            start_time=datetime.now(timezone.utc),
            status="success",
            latency=0.5,
            error=None,
        )
        mock_client.list_runs.return_value = [test_run]

        result = runner.invoke(cli, ["--json", "runs", "list", "--limit", "10"])
        assert result.exit_code == 0, f"Command failed: {result.output}"

        # Output should be valid JSON that can be parsed
        data = json.loads(result.output)
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["id"] == "12345678-1234-5678-1234-567812345678"
        assert data[0]["name"] == "My Run"


def test_runs_list_json_output_empty_list(runner):
    """Test that runs list --json outputs empty JSON array when no results."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        mock_client.list_runs.return_value = []

        result = runner.invoke(cli, ["--json", "runs", "list", "--limit", "10"])
        assert result.exit_code == 0, f"Command failed: {result.output}"

        # Output should be valid JSON - empty array
        data = json.loads(result.output)
        assert isinstance(data, list)
        assert len(data) == 0


def test_runs_list_json_output_with_api_error(runner):
    """Test that runs list --json outputs valid JSON even when API call fails."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        # Simulate API error
        mock_client.list_runs.side_effect = Exception("API Error")

        result = runner.invoke(
            cli,
            ["--json", "runs", "list", "--project", "test-project", "--limit", "10"],
        )
        assert result.exit_code == 0, f"Command failed: {result.output}"

        # Output should still be valid JSON - empty array (exception is caught)
        data = json.loads(result.output)
        assert isinstance(data, list)
        assert len(data) == 0


def test_runs_list_json_output_with_iterator_error(runner):
    """Test that runs list --json outputs valid JSON even when iterator fails during iteration."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        # Create an iterator that fails when converting to list
        def failing_iterator():
            raise Exception("Iterator failed during iteration")
            yield  # Never reached

        mock_client.list_runs.return_value = failing_iterator()

        result = runner.invoke(
            cli,
            ["--json", "runs", "list", "--project", "test-project", "--limit", "10"],
        )
        assert result.exit_code == 0, f"Command failed: {result.output}"

        # Output should still be valid JSON - empty array (exception is caught)
        data = json.loads(result.output)
        assert isinstance(data, list)
        assert len(data) == 0


def test_runs_list_json_output_only_json_no_extra_text(runner):
    """Test that runs list --json outputs ONLY JSON, no extra text before or after."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        test_run = Run(
            id=UUID("12345678-1234-5678-1234-567812345678"),
            name="Test Run",
            run_type="chain",
            start_time=datetime.now(timezone.utc),
            status="success",
            latency=1.5,
        )
        mock_client.list_runs.return_value = [test_run]

        result = runner.invoke(
            cli, ["--json", "runs", "list", "--project", "test", "--limit", "10"]
        )
        assert result.exit_code == 0, f"Command failed: {result.output}"

        # Output should start with '[' (JSON array)
        assert result.output.strip().startswith("["), (
            f"Output should start with '[', got: {result.output[:50]}"
        )

        # Output should end with ']' (JSON array)
        assert result.output.strip().endswith("]"), (
            f"Output should end with ']', got: ...{result.output[-50:]}"
        )

        # Should be valid JSON
        data = json.loads(result.output)
        assert isinstance(data, list)
        assert len(data) == 1


def test_runs_list_with_roots_flag(runner):
    """Test that --roots flag filters to root traces."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        mock_client.list_runs.return_value = []

        runner.invoke(cli, ["runs", "list", "--roots"])

        # Verify is_root=True was passed
        mock_client.list_runs.assert_called_once()
        args, kwargs = mock_client.list_runs.call_args
        assert kwargs["is_root"] is True


def test_runs_list_table_includes_tokens_and_model(runner):
    """Test that table output includes tokens and model columns."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        test_run = Run(
            id=UUID("12345678-1234-5678-1234-567812345678"),
            name="Test LLM Call",
            run_type="llm",
            start_time=datetime.now(timezone.utc),
            status="success",
            latency=1.5,
            total_tokens=1234,
            extra={
                "invocation_params": {"model_name": "gpt-4"},
            },
        )
        mock_client.list_runs.return_value = [test_run]

        result = runner.invoke(cli, ["runs", "list", "--limit", "10"])
        assert result.exit_code == 0

        # Check that tokens are displayed (formatted with comma)
        assert "1,234" in result.output

        # Check that model name is displayed
        assert "gpt-4" in result.output


def test_runs_list_table_handles_missing_tokens_and_model(runner):
    """Test that table gracefully handles missing tokens and model fields."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        test_run = Run(
            id=UUID("12345678-1234-5678-1234-567812345678"),
            name="Test Run",
            run_type="chain",
            start_time=datetime.now(timezone.utc),
            status="success",
            latency=1.5,
            # No total_tokens, no extra fields
        )
        mock_client.list_runs.return_value = [test_run]

        result = runner.invoke(cli, ["runs", "list", "--limit", "10"])
        assert result.exit_code == 0

        # Should not crash and should show "-" for missing data
        # Name might be wrapped across lines in table, so check for "Test"
        assert "Test" in result.output
        # Check for the ID to confirm the run is displayed
        assert "12345678-1234-5678-1234-567812345678" in result.output


def test_runs_search_with_roots_flag(runner):
    """Test that search command supports --roots flag."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        mock_client.list_runs.return_value = []

        runner.invoke(cli, ["runs", "search", "error", "--roots"])

        # Verify is_root=True was passed to list_runs
        mock_client.list_runs.assert_called_once()
        args, kwargs = mock_client.list_runs.call_args
        assert kwargs["is_root"] is True


def test_runs_list_filters(runner):
    """Test runs list with filters."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        mock_client.list_runs.return_value = []

        runner.invoke(
            cli,
            ["runs", "list", "--project", "prod", "--limit", "5", "--status", "error"],
        )

        mock_client.list_runs.assert_called_with(
            project_name="prod",
            limit=5,
            error=True,
            filter=None,
            trace_id=None,
            run_type=None,
            is_root=None,
            trace_filter=None,
            tree_filter=None,
            order_by="-start_time",
            reference_example_id=None,
        )


def test_runs_get(runner):
    """Test the runs get command."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        # Use real Run model instead of MagicMock
        test_run = Run(
            id=UUID("12345678-0000-0000-0000-000000000456"),
            name="Detailed Run",
            run_type="chain",
            start_time=datetime.now(timezone.utc),
            inputs={"q": "hello"},
            outputs={"a": "world"},
        )
        mock_client.read_run.return_value = test_run

        # Use --json to checking the raw output mostly, but default is table/text
        result = runner.invoke(
            cli, ["--json", "runs", "get", "12345678-0000-0000-0000-000000000456"]
        )
        assert result.exit_code == 0, f"Exit code {result.exit_code}: {result.output}"
        assert "12345678-0000-0000-0000-000000000456" in result.output
        assert "hello" in result.output


def test_runs_get_fields(runner):
    """Test runs get with pruning fields."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        # Use real Run model with various fields
        test_run = Run(
            id=UUID("12345678-0000-0000-0000-000000000789"),
            name="Full Run",
            run_type="chain",
            start_time=datetime.now(timezone.utc),
            inputs={"input": "foo"},  # inputs must be dict
            outputs={"output": "bar"},  # outputs must be dict
            extra={"heavy_field": "huge_data"},  # Extra data field
        )
        mock_client.read_run.return_value = test_run

        result = runner.invoke(
            cli,
            [
                "--json",
                "runs",
                "get",
                "12345678-0000-0000-0000-000000000789",
                "--fields",
                "inputs",
            ],
        )
        assert result.exit_code == 0, f"Exit code {result.exit_code}: {result.output}"

        # Should contain inputs
        assert "foo" in result.output
        # Should NOT contain extra heavy_field
        assert "huge_data" not in result.output


def test_runs_search(runner):
    """Test the runs search command."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        # Use real Run model
        test_run = Run(
            id=UUID("12345678-0000-0000-0000-000000000001"),
            name="search-result",
            run_type="chain",
            start_time=datetime.now(timezone.utc),
            status="success",
            latency=0.5,
        )
        mock_client.list_runs.return_value = [test_run]

        # Use the search command with new positional argument
        result = runner.invoke(cli, ["runs", "search", "test"])
        assert result.exit_code == 0
        assert "search-result" in result.output
        # Verify list_runs was called with the search filter
        mock_client.list_runs.assert_called_once()
        args, kwargs = mock_client.list_runs.call_args
        assert 'search("test")' in kwargs["filter"]


def test_runs_stats(runner):
    """Test the runs stats command."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        mock_client.get_run_stats.return_value = {"error_rate": 0.1, "latency_p50": 0.2}

        result = runner.invoke(cli, ["runs", "stats"])
        assert result.exit_code == 0
        assert "Error Rate" in result.output
        assert "0.1" in result.output


def test_runs_list_with_tags(runner):
    """Test runs list with tag filtering."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        mock_client.list_runs.return_value = []

        runner.invoke(
            cli,
            ["runs", "list", "--tag", "production", "--tag", "experimental"],
        )

        # Verify FQL filter was constructed correctly
        mock_client.list_runs.assert_called_once()
        args, kwargs = mock_client.list_runs.call_args
        assert 'has(tags, "production")' in kwargs["filter"]
        assert 'has(tags, "experimental")' in kwargs["filter"]
        assert kwargs["filter"].startswith("and(")


def test_runs_list_with_name_pattern(runner):
    """Test runs list with name pattern filtering."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        # Create real Run instances with different names
        run1 = Run(
            id=UUID("00000000-0000-0000-0000-000000000001"),
            name="auth-service",
            run_type="chain",
            start_time=datetime.now(timezone.utc),
            status="success",
            latency=0.5,
        )

        run2 = Run(
            id=UUID("00000000-0000-0000-0000-000000000002"),
            name="database-query",
            run_type="chain",
            start_time=datetime.now(timezone.utc),
            status="success",
            latency=0.6,
        )

        run3 = Run(
            id=UUID("00000000-0000-0000-0000-000000000003"),
            name="test-auth-check",
            run_type="chain",
            start_time=datetime.now(timezone.utc),
            status="success",
            latency=0.7,
        )

        mock_client.list_runs.return_value = iter([run1, run2, run3])

        result = runner.invoke(cli, ["runs", "list", "--name-pattern", "*auth*"])

        # Pattern matching is done client-side, so no FQL filter
        # Should match run1 and run3 (contains "auth")
        assert result.exit_code == 0
        assert "auth-service" in result.output
        assert "test-auth-check" in result.output
        assert "database-query" not in result.output


def test_runs_list_with_name_pattern_uses_reasonable_limit(runner):
    """Test that name pattern filtering uses a capped limit, not unlimited."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        mock_client.list_runs.return_value = iter([])

        # Test with small limit - should use minimum of 100
        runner.invoke(cli, ["runs", "list", "--name-pattern", "*test*", "--limit", "5"])
        args, kwargs = mock_client.list_runs.call_args
        # Should fetch max(5*10, 100) = 100 for pattern matching, not unlimited (None)
        assert kwargs["limit"] == 100, "Should use minimum of 100 for pattern matching"

        # Test with larger limit - should use 10x
        runner.invoke(
            cli, ["runs", "list", "--name-pattern", "*test*", "--limit", "50"]
        )
        args, kwargs = mock_client.list_runs.call_args
        # Should fetch max(50*10, 100) = 500 for pattern matching
        assert kwargs["limit"] == 500, "Should use 10x limit for pattern matching"

        # Test without explicit limit (default is 20)
        runner.invoke(cli, ["runs", "list", "--name-pattern", "*test*"])
        args, kwargs = mock_client.list_runs.call_args
        # Should fetch max(20*10, 100) = 200 for pattern matching
        assert kwargs["limit"] == 200, "Should cap at max(10x default, 100)"


def test_runs_list_with_name_regex_uses_reasonable_limit(runner):
    """Test that name regex filtering uses a capped limit, not unlimited."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        mock_client.list_runs.return_value = iter([])

        # Test with small limit - should use minimum of 100
        runner.invoke(cli, ["runs", "list", "--name-regex", "^test.*", "--limit", "5"])
        args, kwargs = mock_client.list_runs.call_args
        # Should fetch max(5*10, 100) = 100 for regex matching, not unlimited (None)
        assert kwargs["limit"] == 100, "Should use minimum of 100 for regex matching"


def test_runs_list_with_smart_filters(runner):
    """Test runs list with smart filters."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        mock_client.list_runs.return_value = []

        # Test --slow flag
        runner.invoke(cli, ["runs", "list", "--slow"])
        args, kwargs = mock_client.list_runs.call_args
        assert 'gt(latency, "5s")' in kwargs["filter"]

        # Test --recent flag
        runner.invoke(cli, ["runs", "list", "--recent"])
        args, kwargs = mock_client.list_runs.call_args
        assert 'gt(start_time, "' in kwargs["filter"]

        # Test --today flag
        runner.invoke(cli, ["runs", "list", "--today"])
        args, kwargs = mock_client.list_runs.call_args
        assert 'gt(start_time, "' in kwargs["filter"]


def test_runs_list_combined_filters(runner):
    """Test runs list with multiple filters combined."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        # Create real Run instances for client-side name pattern filtering
        run1 = Run(
            id=UUID("00000000-0000-0000-0000-000000000011"),
            name="api-endpoint",
            run_type="chain",
            start_time=datetime.now(timezone.utc),
            status="success",
            latency=1.0,
        )

        run2 = Run(
            id=UUID("00000000-0000-0000-0000-000000000012"),
            name="worker-task",
            run_type="chain",
            start_time=datetime.now(timezone.utc),
            status="success",
            latency=1.5,
        )

        mock_client.list_runs.return_value = iter([run1, run2])

        result = runner.invoke(
            cli,
            ["runs", "list", "--tag", "prod", "--slow", "--name-pattern", "*api*"],
        )

        # Verify API-level filters (tag and slow) are combined
        # name-pattern is client-side, so not in FQL
        args, kwargs = mock_client.list_runs.call_args
        assert 'has(tags, "prod")' in kwargs["filter"]
        assert 'gt(latency, "5s")' in kwargs["filter"]
        assert kwargs["filter"].startswith("and(")

        # Client-side filtering should match only run1
        assert result.exit_code == 0
        assert "api-endpoint" in result.output
        assert "worker-task" not in result.output


def test_runs_list_with_min_latency(runner):
    """Test runs list with --min-latency filter."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        mock_client.list_runs.return_value = []

        runner.invoke(cli, ["runs", "list", "--min-latency", "2s"])

        args, kwargs = mock_client.list_runs.call_args
        assert 'gt(latency, "2s")' in kwargs["filter"]


def test_runs_list_with_max_latency(runner):
    """Test runs list with --max-latency filter."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        mock_client.list_runs.return_value = []

        runner.invoke(cli, ["runs", "list", "--max-latency", "10s"])

        args, kwargs = mock_client.list_runs.call_args
        assert 'lt(latency, "10s")' in kwargs["filter"]


def test_runs_list_with_latency_range(runner):
    """Test runs list with both --min-latency and --max-latency."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        mock_client.list_runs.return_value = []

        runner.invoke(
            cli, ["runs", "list", "--min-latency", "1s", "--max-latency", "5s"]
        )

        args, kwargs = mock_client.list_runs.call_args
        assert 'gt(latency, "1s")' in kwargs["filter"]
        assert 'lt(latency, "5s")' in kwargs["filter"]
        assert kwargs["filter"].startswith("and(")


def test_runs_list_with_last_filter(runner):
    """Test runs list with --last filter."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        mock_client.list_runs.return_value = []

        runner.invoke(cli, ["runs", "list", "--last", "24h"])

        args, kwargs = mock_client.list_runs.call_args
        assert 'gt(start_time, "' in kwargs["filter"]
        # Verify it's a valid ISO timestamp
        assert "T" in kwargs["filter"]


def test_runs_list_with_since_relative(runner):
    """Test runs list with --since using relative time."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        mock_client.list_runs.return_value = []

        runner.invoke(cli, ["runs", "list", "--since", "7d"])

        args, kwargs = mock_client.list_runs.call_args
        assert 'gt(start_time, "' in kwargs["filter"]


def test_runs_list_with_since_iso(runner):
    """Test runs list with --since using ISO timestamp."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        mock_client.list_runs.return_value = []

        runner.invoke(cli, ["runs", "list", "--since", "2024-01-14T10:00:00Z"])

        args, kwargs = mock_client.list_runs.call_args
        assert 'gt(start_time, "2024-01-14T10:00:00' in kwargs["filter"]


def test_runs_list_with_name_regex(runner):
    """Test runs list with --name-regex filter."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        # Create real Run instances with different names
        run1 = Run(
            id=UUID("00000000-0000-0000-0000-000000000021"),
            name="test-auth-v1",
            run_type="chain",
            start_time=datetime.now(timezone.utc),
            status="success",
            latency=0.5,
        )

        run2 = Run(
            id=UUID("00000000-0000-0000-0000-000000000022"),
            name="test-auth-v2",
            run_type="chain",
            start_time=datetime.now(timezone.utc),
            status="success",
            latency=0.6,
        )

        run3 = Run(
            id=UUID("00000000-0000-0000-0000-000000000023"),
            name="prod-checkout",
            run_type="chain",
            start_time=datetime.now(timezone.utc),
            status="success",
            latency=0.7,
        )

        mock_client.list_runs.return_value = iter([run1, run2, run3])

        # Filter with regex for "test-auth-v[0-9]+"
        result = runner.invoke(
            cli, ["runs", "list", "--name-regex", "test-auth-v[0-9]+"]
        )

        assert result.exit_code == 0
        # Should match run1 and run2, but not run3
        assert "test-auth-v1" in result.output
        assert "test-auth-v2" in result.output
        assert "prod-checkout" not in result.output


def test_runs_list_with_name_regex_anchors(runner):
    """Test runs list with --name-regex using anchors."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        run1 = Run(
            id=UUID("00000000-0000-0000-0000-000000000031"),
            name="auth-service",
            run_type="chain",
            start_time=datetime.now(timezone.utc),
            status="success",
            latency=0.5,
        )

        run2 = Run(
            id=UUID("00000000-0000-0000-0000-000000000032"),
            name="test-auth",
            run_type="chain",
            start_time=datetime.now(timezone.utc),
            status="success",
            latency=0.6,
        )

        mock_client.list_runs.return_value = iter([run1, run2])

        # Filter with regex starting with "^auth"
        result = runner.invoke(cli, ["runs", "list", "--name-regex", "^auth"])

        assert result.exit_code == 0
        # Should only match run1
        assert "auth-service" in result.output
        assert "test-auth" not in result.output


def test_runs_list_with_model_filter(runner):
    """Test runs list with --model filter."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        mock_client.list_runs.return_value = []

        runner.invoke(cli, ["runs", "list", "--model", "gpt-4"])

        # Verify FQL filter was constructed with model search
        mock_client.list_runs.assert_called_once()
        args, kwargs = mock_client.list_runs.call_args
        assert 'search("gpt-4")' in kwargs["filter"]


def test_runs_list_with_failed_flag(runner):
    """Test runs list with --failed flag."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        mock_client.list_runs.return_value = []

        runner.invoke(cli, ["runs", "list", "--failed"])

        # Verify error=True was passed
        mock_client.list_runs.assert_called_once()
        args, kwargs = mock_client.list_runs.call_args
        assert kwargs["error"] is True


def test_runs_list_with_succeeded_flag(runner):
    """Test runs list with --succeeded flag."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        mock_client.list_runs.return_value = []

        runner.invoke(cli, ["runs", "list", "--succeeded"])

        # Verify error=False was passed
        mock_client.list_runs.assert_called_once()
        args, kwargs = mock_client.list_runs.call_args
        assert kwargs["error"] is False


def test_runs_list_with_sort_by(runner):
    """Test runs list with --sort-by flag."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        run1 = Run(
            id=UUID("00000000-0000-0000-0000-000000000041"),
            name="zebra",
            run_type="chain",
            start_time=datetime.now(timezone.utc),
            status="success",
            latency=2.0,
        )

        run2 = Run(
            id=UUID("00000000-0000-0000-0000-000000000042"),
            name="alpha",
            run_type="chain",
            start_time=datetime.now(timezone.utc),
            status="error",
            latency=1.0,
        )

        run3 = Run(
            id=UUID("00000000-0000-0000-0000-000000000043"),
            name="beta",
            run_type="chain",
            start_time=datetime.now(timezone.utc),
            status="success",
            latency=3.0,
        )

        mock_client.list_runs.return_value = iter([run1, run2, run3])

        # Sort by name ascending
        result = runner.invoke(cli, ["runs", "list", "--sort-by", "name"])

        assert result.exit_code == 0
        # Check order in output (alpha should appear before zebra)
        alpha_pos = result.output.find("alpha")
        zebra_pos = result.output.find("zebra")
        assert alpha_pos < zebra_pos


def test_runs_list_with_sort_by_latency_desc(runner):
    """Test runs list with --sort-by descending."""
    from datetime import timedelta

    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        # Create runs with computed latency (end_time - start_time)
        start1 = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        run1 = Run(
            id=UUID("00000000-0000-0000-0000-000000000051"),
            name="fast",
            run_type="chain",
            start_time=start1,
            end_time=start1 + timedelta(seconds=1.0),  # latency = 1.0s
            status="success",
        )

        start2 = datetime(2024, 1, 1, 12, 0, 1, tzinfo=timezone.utc)
        run2 = Run(
            id=UUID("00000000-0000-0000-0000-000000000052"),
            name="slow",
            run_type="chain",
            start_time=start2,
            end_time=start2 + timedelta(seconds=5.0),  # latency = 5.0s
            status="success",
        )

        # Return runs in original order (fast first, slow second)
        mock_client.list_runs.return_value = iter([run1, run2])

        # Sort by latency descending - slow (5.0) should come before fast (1.0)
        result = runner.invoke(cli, ["runs", "list", "--sort-by", "-latency"])

        assert result.exit_code == 0
        # After sorting by -latency, slow (5.0s) should appear before fast (1.0s)
        slow_pos = result.output.find("slow")
        fast_pos = result.output.find("fast")
        assert slow_pos < fast_pos, (
            f"Expected 'slow' before 'fast', but got slow at {slow_pos}, fast at {fast_pos}"
        )


def test_runs_list_with_csv_format(runner):
    """Test runs list with CSV export."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        run1 = Run(
            id=UUID("00000000-0000-0000-0000-000000000061"),
            name="test-run",
            run_type="chain",
            start_time=datetime.now(timezone.utc),
            status="success",
        )

        mock_client.list_runs.return_value = iter([run1])

        result = runner.invoke(cli, ["runs", "list", "--format", "csv"])

        assert result.exit_code == 0
        # CSV should have header and data rows
        assert "id,name,status" in result.output or "id" in result.output
        assert "test-run,success" in result.output or "test-run" in result.output


def test_runs_list_with_yaml_format(runner):
    """Test runs list with YAML export."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        run1 = Run(
            id=UUID("00000000-0000-0000-0000-000000000071"),
            name="test-run",
            run_type="chain",
            start_time=datetime.now(timezone.utc),
            status="success",
        )

        mock_client.list_runs.return_value = iter([run1])

        result = runner.invoke(cli, ["runs", "list", "--format", "yaml"])

        assert result.exit_code == 0
        # YAML should contain the data
        assert "id:" in result.output or "id" in result.output
        assert "name: test-run" in result.output or "test-run" in result.output


def test_runs_search_with_input_contains(runner):
    """Test runs search with --input-contains flag."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        mock_client.list_runs.return_value = []

        result = runner.invoke(
            cli, ["runs", "search", "user_123", "--input-contains", "email"]
        )

        assert result.exit_code == 0
        # Verify both search terms were included
        mock_client.list_runs.assert_called_once()
        args, kwargs = mock_client.list_runs.call_args
        assert 'search("user_123")' in kwargs["filter"]
        assert 'search("email")' in kwargs["filter"]


def test_runs_search_with_output_contains(runner):
    """Test runs search with --output-contains flag."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        mock_client.list_runs.return_value = []

        result = runner.invoke(
            cli, ["runs", "search", "error", "--output-contains", "timeout"]
        )

        assert result.exit_code == 0
        # Verify both search terms were included
        mock_client.list_runs.assert_called_once()
        args, kwargs = mock_client.list_runs.call_args
        assert 'search("error")' in kwargs["filter"]
        assert 'search("timeout")' in kwargs["filter"]


def test_runs_list_with_invalid_duration_format(runner):
    """Test that invalid duration format raises error."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        mock_client.list_runs.return_value = []

        # Invalid duration - missing unit
        result = runner.invoke(cli, ["runs", "list", "--min-latency", "5"])
        assert result.exit_code != 0
        assert "Invalid duration format" in result.output

        # Invalid duration - invalid unit
        result = runner.invoke(cli, ["runs", "list", "--max-latency", "5x"])
        assert result.exit_code != 0
        assert "Invalid duration format" in result.output


def test_runs_list_with_invalid_time_format(runner):
    """Test that invalid time format raises error."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        mock_client.list_runs.return_value = []

        # Invalid time format
        result = runner.invoke(cli, ["runs", "list", "--last", "5x"])
        assert result.exit_code != 0
        assert "Invalid time format" in result.output


def test_runs_list_with_invalid_since_format(runner):
    """Test that invalid since format raises error."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        mock_client.list_runs.return_value = []

        # Invalid since format (neither ISO nor relative)
        result = runner.invoke(cli, ["runs", "list", "--since", "invalid"])
        assert result.exit_code != 0
        assert "Invalid --since format" in result.output


def test_runs_list_with_empty_results(runner):
    """Test runs list with no results."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        mock_client.list_runs.return_value = []

        result = runner.invoke(cli, ["runs", "list"])

        assert result.exit_code == 0
        assert "No runs found" in result.output


def test_runs_list_with_invalid_regex(runner):
    """Test that invalid regex raises error."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        test_run = Run(
            id=UUID("00000000-0000-0000-0000-000000000081"),
            name="test",
            run_type="chain",
            start_time=datetime.now(timezone.utc),
            status="success",
            latency=1.0,
        )
        mock_client.list_runs.return_value = [test_run]

        # Invalid regex pattern
        result = runner.invoke(cli, ["runs", "list", "--name-regex", "[invalid("])
        assert result.exit_code != 0
        assert "Invalid regex pattern" in result.output


def test_runs_get_rich_output(runner):
    """Test runs get with Rich console output (not JSON mode)."""
    import re

    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        test_run = Run(
            id=UUID("12345678-0000-0000-0000-000000000123"),
            name="Rich Output Test",
            run_type="chain",
            start_time=datetime.now(timezone.utc),
            status="success",
            inputs={"query": "test"},
            outputs={"result": "success"},
        )
        mock_client.read_run.return_value = test_run

        # No --json flag, should use Rich output
        result = runner.invoke(
            cli, ["runs", "get", "12345678-0000-0000-0000-000000000123"]
        )

        assert result.exit_code == 0

        # Strip ANSI color codes for clean assertions
        # ANSI codes are like \x1b[1;36m (start) and \x1b[0m (reset)
        ansi_escape = re.compile(r"\x1b\[[0-9;]*m")
        clean_output = ansi_escape.sub("", result.output)

        assert "12345678-0000-0000-0000-000000000123" in clean_output
        assert "Rich Output Test" in clean_output
        assert "status" in clean_output or "success" in clean_output
        assert "inputs" in clean_output or "query" in clean_output


def test_runs_get_with_complex_data_types(runner):
    """Test runs get handles dict and list data types."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        test_run = Run(
            id=UUID("12345678-0000-0000-0000-000000999999"),
            name="Complex Data",
            run_type="chain",
            start_time=datetime.now(timezone.utc),
            metadata={"key": "value", "nested": {"deep": "data"}},
            tags=["tag1", "tag2"],
            extra={"simple_field": "simple_value"},
        )
        mock_client.read_run.return_value = test_run

        result = runner.invoke(
            cli, ["runs", "get", "12345678-0000-0000-0000-000000999999"]
        )

        assert result.exit_code == 0
        # Check that tags are displayed
        assert "tags" in result.output or "tag1" in result.output
        # Check that extra field simple_value is displayed
        assert "simple_value" in result.output


def test_runs_stats_table_output(runner):
    """Test runs stats with table output."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        mock_project = MagicMock()
        mock_project.id = "project-123"
        mock_client.read_project.return_value = mock_project
        mock_client.get_run_stats.return_value = {
            "run_count": 100,
            "error_count": 5,
            "avg_latency": 1.5,
        }

        result = runner.invoke(cli, ["runs", "stats", "--project", "test-project"])

        assert result.exit_code == 0
        assert "100" in result.output
        assert "5" in result.output


def test_runs_stats_json_output(runner):
    """Test runs stats with JSON output."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        mock_project = MagicMock()
        mock_project.id = "project-456"
        mock_client.read_project.return_value = mock_project
        mock_client.get_run_stats.return_value = {
            "run_count": 50,
            "error_count": 2,
        }

        result = runner.invoke(
            cli, ["--json", "runs", "stats", "--project", "my-project"]
        )

        assert result.exit_code == 0
        import json

        data = json.loads(result.output)
        assert data["run_count"] == 50
        assert data["error_count"] == 2


def test_runs_stats_fallback_to_project_id(runner):
    """Test runs stats falls back to using project name as ID on error."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        # read_project raises error, should fallback to using name as ID
        mock_client.read_project.side_effect = Exception("Not found")
        mock_client.get_run_stats.return_value = {"run_count": 10}

        result = runner.invoke(
            cli, ["--json", "runs", "stats", "--project", "fallback-id"]
        )

        assert result.exit_code == 0
        # Should have called get_run_stats with the project name as ID
        mock_client.get_run_stats.assert_called_once()


def test_runs_open_command(runner):
    """Test runs open command opens browser."""
    with patch("webbrowser.open") as mock_browser:
        result = runner.invoke(cli, ["runs", "open", "test-run-id"])

        assert result.exit_code == 0
        assert "Opening run test-run-id" in result.output
        assert "https://smith.langchain.com/r/test-run-id" in result.output
        mock_browser.assert_called_once_with(
            "https://smith.langchain.com/r/test-run-id"
        )


def test_runs_watch_keyboard_interrupt(runner):
    """Test runs watch handles keyboard interrupt gracefully."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        # Mock list_projects - returns empty since we're only using --project
        mock_client.list_projects.return_value = []

        test_run = Run(
            id=UUID("00000000-0000-0000-0000-000000000091"),
            name="Watched Run",
            run_type="chain",
            start_time=datetime.fromisoformat("2024-01-01T00:00:00+00:00"),
            status="success",
            latency=1.0,
            session_id=UUID(
                "00000000-0000-0000-0000-000000000092"
            ),  # session_id must be UUID
            total_tokens=100,
        )

        # Make list_runs raise KeyboardInterrupt after first call
        mock_client.list_runs.side_effect = [
            [test_run],  # First call succeeds
            KeyboardInterrupt(),  # Second call interrupted
        ]

        # Use timeout to prevent hanging
        with patch("time.sleep") as mock_sleep:
            mock_sleep.side_effect = KeyboardInterrupt()
            result = runner.invoke(cli, ["runs", "watch", "--project", "test"])

        # Should exit cleanly on KeyboardInterrupt
        assert result.exit_code == 0


# Unit tests for helper functions


def test_parse_grouping_field_valid():
    """Test parsing valid grouping field specifications."""
    from langsmith_cli.commands.runs import parse_grouping_field

    assert parse_grouping_field("tag:length_category") == ("tag", "length_category")
    assert parse_grouping_field("metadata:user_tier") == ("metadata", "user_tier")
    assert parse_grouping_field("tag:env") == ("tag", "env")


def test_parse_grouping_field_invalid():
    """Test parsing invalid grouping field specifications."""
    import click
    from langsmith_cli.commands.runs import parse_grouping_field

    with pytest.raises(click.BadParameter, match="Invalid grouping format"):
        parse_grouping_field("invalid")

    with pytest.raises(click.BadParameter, match="Invalid grouping type"):
        parse_grouping_field("unknown:field")

    with pytest.raises(click.BadParameter, match="Field name cannot be empty"):
        parse_grouping_field("tag:")


def test_build_grouping_fql_filter_tag():
    """Test building FQL filter for tag-based grouping."""
    from langsmith_cli.commands.runs import build_grouping_fql_filter

    filter_str = build_grouping_fql_filter("tag", "length_category", "short")
    assert filter_str == 'has(tags, "length_category:short")'


def test_build_grouping_fql_filter_metadata():
    """Test building FQL filter for metadata-based grouping."""
    from langsmith_cli.commands.runs import build_grouping_fql_filter

    filter_str = build_grouping_fql_filter("metadata", "user_tier", "premium")
    assert (
        filter_str
        == 'and(in(metadata_key, ["user_tier"]), eq(metadata_value, "premium"))'
    )


def test_extract_group_value_from_tags():
    """Test extracting group value from run tags."""
    from langsmith_cli.commands.runs import extract_group_value

    run = Run(
        id=UUID("00000000-0000-0000-0000-000000000001"),
        name="test",
        run_type="chain",
        start_time=datetime.now(timezone.utc),
        tags=["env:prod", "length_category:short", "user:123"],
    )

    value = extract_group_value(run, "tag", "length_category")
    assert value == "short"

    # Non-existent tag
    value = extract_group_value(run, "tag", "nonexistent")
    assert value is None


def test_extract_group_value_from_metadata():
    """Test extracting group value from run metadata."""
    from langsmith_cli.commands.runs import extract_group_value

    run = Run(
        id=UUID("00000000-0000-0000-0000-000000000002"),
        name="test",
        run_type="chain",
        start_time=datetime.now(timezone.utc),
        extra={"metadata": {"user_tier": "premium", "region": "us-east"}},
    )

    value = extract_group_value(run, "metadata", "user_tier")
    assert value == "premium"

    # Non-existent metadata key
    value = extract_group_value(run, "metadata", "nonexistent")
    assert value is None


def test_compute_metrics_count():
    """Test computing count metric."""
    from langsmith_cli.commands.runs import compute_metrics

    runs = [
        Run(
            id=UUID(f"00000000-0000-0000-0000-00000000000{i}"),
            name=f"run-{i}",
            run_type="chain",
            start_time=datetime.now(timezone.utc),
        )
        for i in range(5)
    ]

    metrics = compute_metrics(runs, ["count"])
    assert metrics["count"] == 5


def test_compute_metrics_error_rate():
    """Test computing error rate metric."""
    from langsmith_cli.commands.runs import compute_metrics

    runs = [
        Run(
            id=UUID("00000000-0000-0000-0000-000000000001"),
            name="success",
            run_type="chain",
            start_time=datetime.now(timezone.utc),
            error=None,
        ),
        Run(
            id=UUID("00000000-0000-0000-0000-000000000002"),
            name="error",
            run_type="chain",
            start_time=datetime.now(timezone.utc),
            error="Failed",
        ),
        Run(
            id=UUID("00000000-0000-0000-0000-000000000003"),
            name="success2",
            run_type="chain",
            start_time=datetime.now(timezone.utc),
            error=None,
        ),
    ]

    metrics = compute_metrics(runs, ["error_rate"])
    assert metrics["error_rate"] == pytest.approx(1 / 3, rel=0.01)


def test_compute_metrics_latency_percentiles():
    """Test computing latency percentile metrics."""
    from langsmith_cli.commands.runs import compute_metrics

    start_time = datetime.now(timezone.utc)
    runs = [
        Run(
            id=UUID(int=i),
            name=f"run-{i}",
            run_type="chain",
            start_time=start_time,
            end_time=start_time + timedelta(seconds=float(i)),
        )
        for i in range(1, 101)
    ]

    metrics = compute_metrics(runs, ["p50_latency", "p95_latency", "p99_latency"])
    assert metrics["p50_latency"] == pytest.approx(50.5, rel=0.1)
    assert metrics["p95_latency"] >= 95.0
    assert metrics["p99_latency"] >= 99.0


# Integration tests for runs sample


def test_runs_sample_basic(runner):
    """Test basic runs sample command."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        # Create runs with tags
        short_run = Run(
            id=UUID("00000000-0000-0000-0000-000000000001"),
            name="short-run",
            run_type="chain",
            start_time=datetime.now(timezone.utc),
            tags=["length_category:short"],
            inputs={"text": "hi"},
            outputs={"result": "ok"},
        )

        medium_run = Run(
            id=UUID("00000000-0000-0000-0000-000000000002"),
            name="medium-run",
            run_type="chain",
            start_time=datetime.now(timezone.utc),
            tags=["length_category:medium"],
            inputs={"text": "hello world"},
            outputs={"result": "ok"},
        )

        # Mock list_runs to return appropriate runs based on filter
        def list_runs_side_effect(*args, **kwargs):
            filter_str = kwargs.get("filter", "")
            if "length_category:short" in filter_str:
                return [short_run]
            elif "length_category:medium" in filter_str:
                return [medium_run]
            return []

        mock_client.list_runs.side_effect = list_runs_side_effect

        result = runner.invoke(
            cli,
            [
                "runs",
                "sample",
                "--stratify-by",
                "tag:length_category",
                "--values",
                "short,medium",
                "--samples-per-stratum",
                "1",
            ],
        )

        assert result.exit_code == 0

        # Parse JSONL output
        lines = result.output.strip().split("\n")
        assert len(lines) == 2

        # Verify stratum fields
        sample1 = json.loads(lines[0])
        sample2 = json.loads(lines[1])

        assert "stratum" in sample1
        assert "stratum" in sample2
        assert sample1["stratum"] in ["length_category:short", "length_category:medium"]


def test_runs_sample_with_output_file(runner, tmp_path):
    """Test runs sample writing to output file."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        test_run = Run(
            id=UUID("00000000-0000-0000-0000-000000000003"),
            name="test-run",
            run_type="chain",
            start_time=datetime.now(timezone.utc),
            tags=["category:test"],
        )

        mock_client.list_runs.return_value = [test_run]

        output_file = tmp_path / "sample.jsonl"

        result = runner.invoke(
            cli,
            [
                "runs",
                "sample",
                "--stratify-by",
                "tag:category",
                "--values",
                "test",
                "--samples-per-stratum",
                "1",
                "--output",
                str(output_file),
            ],
        )

        assert result.exit_code == 0
        assert output_file.exists()

        # Verify file contents
        with open(output_file) as f:
            line = f.readline()
            data = json.loads(line)
            assert "stratum" in data
            assert data["stratum"] == "category:test"


def test_runs_sample_with_fields_pruning(runner):
    """Test runs sample with field pruning."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        test_run = Run(
            id=UUID("00000000-0000-0000-0000-000000000004"),
            name="test-run",
            run_type="chain",
            start_time=datetime.now(timezone.utc),
            tags=["category:test"],
            inputs={"query": "test"},
            outputs={"result": "ok"},
            extra={"large_field": "huge_data"},
        )

        mock_client.list_runs.return_value = [test_run]

        result = runner.invoke(
            cli,
            [
                "runs",
                "sample",
                "--stratify-by",
                "tag:category",
                "--values",
                "test",
                "--samples-per-stratum",
                "1",
                "--fields",
                "id,name,stratum",
            ],
        )

        assert result.exit_code == 0

        # Parse output
        line = result.output.strip()
        data = json.loads(line)

        # Should have id, name, stratum
        assert "id" in data
        assert "name" in data
        assert "stratum" in data

        # Should NOT have inputs, outputs, extra
        assert "inputs" not in data
        assert "outputs" not in data
        assert "huge_data" not in result.output


def test_runs_sample_invalid_stratify_by(runner):
    """Test runs sample with invalid stratify-by format."""
    result = runner.invoke(
        cli,
        [
            "runs",
            "sample",
            "--stratify-by",
            "invalid",
            "--values",
            "a,b",
        ],
    )

    assert result.exit_code != 0
    assert "Invalid grouping format" in result.output


# Integration tests for runs analyze


def test_runs_analyze_basic(runner):
    """Test basic runs analyze command."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        # Create runs with tags
        start_time = datetime.now(timezone.utc)
        runs = [
            Run(
                id=UUID(int=i + 1),
                name=f"run-{i}",
                run_type="chain",
                start_time=start_time,
                end_time=start_time + timedelta(seconds=1.0 + i * 0.1),
                tags=["length_category:short"],
                error=None if i % 2 == 0 else "Error",
            )
            for i in range(10)
        ]

        mock_client.list_runs.return_value = runs

        result = runner.invoke(
            cli,
            [
                "--json",
                "runs",
                "analyze",
                "--group-by",
                "tag:length_category",
                "--metrics",
                "count,error_rate,p50_latency",
            ],
        )

        assert result.exit_code == 0

        # Parse JSON output
        data = json.loads(result.output)
        assert isinstance(data, list)
        assert len(data) == 1

        group = data[0]
        assert group["group"] == "length_category:short"
        assert group["count"] == 10
        assert group["error_rate"] == 0.5


def test_runs_analyze_multiple_groups(runner):
    """Test runs analyze with multiple groups."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        # Create runs with different groups
        start_time = datetime.now(timezone.utc)
        short_runs = [
            Run(
                id=UUID(int=i + 1),
                name=f"short-{i}",
                run_type="chain",
                start_time=start_time,
                end_time=start_time + timedelta(seconds=1.0),
                tags=["length_category:short"],
            )
            for i in range(5)
        ]

        long_runs = [
            Run(
                id=UUID(int=i + 100),
                name=f"long-{i}",
                run_type="chain",
                start_time=start_time,
                end_time=start_time + timedelta(seconds=5.0),
                tags=["length_category:long"],
            )
            for i in range(3)
        ]

        mock_client.list_runs.return_value = short_runs + long_runs

        result = runner.invoke(
            cli,
            [
                "--json",
                "runs",
                "analyze",
                "--group-by",
                "tag:length_category",
                "--metrics",
                "count,avg_latency",
            ],
        )

        assert result.exit_code == 0

        # Parse JSON output
        data = json.loads(result.output)
        assert len(data) == 2

        # Find groups
        short_group = next(g for g in data if g["group"] == "length_category:short")
        long_group = next(g for g in data if g["group"] == "length_category:long")

        assert short_group["count"] == 5
        assert long_group["count"] == 3
        assert short_group["avg_latency"] == 1.0
        assert long_group["avg_latency"] == 5.0


def test_runs_analyze_table_output(runner):
    """Test runs analyze with table output."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        start_time = datetime.now(timezone.utc)
        runs = [
            Run(
                id=UUID("00000000-0000-0000-0000-000000000011"),
                name="run",
                run_type="chain",
                start_time=start_time,
                end_time=start_time + timedelta(seconds=2.5),
                extra={"metadata": {"tier": "premium"}},
            )
        ]

        mock_client.list_runs.return_value = runs

        result = runner.invoke(
            cli,
            [
                "runs",
                "analyze",
                "--group-by",
                "metadata:tier",
                "--metrics",
                "count,p50_latency",
            ],
        )

        assert result.exit_code == 0
        assert "Analysis:" in result.output
        assert "premium" in result.output
        assert "2.5" in result.output or "2.50" in result.output


def test_runs_analyze_with_filter(runner):
    """Test runs analyze with additional FQL filter."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        mock_client.list_runs.return_value = []

        result = runner.invoke(
            cli,
            [
                "--json",
                "runs",
                "analyze",
                "--group-by",
                "tag:category",
                "--metrics",
                "count",
                "--filter",
                'gte(start_time, "2026-01-01")',
            ],
        )

        assert result.exit_code == 0

        # Verify filter was passed to list_runs
        mock_client.list_runs.assert_called_once()
        args, kwargs = mock_client.list_runs.call_args
        assert 'gte(start_time, "2026-01-01")' in kwargs["filter"]


def test_runs_analyze_invalid_group_by(runner):
    """Test runs analyze with invalid group-by format."""
    result = runner.invoke(
        cli,
        [
            "runs",
            "analyze",
            "--group-by",
            "unknown:field",
        ],
    )

    assert result.exit_code != 0
    assert "Invalid grouping type" in result.output


# =============================================================================
# Multi-Dimensional Stratification Tests
# =============================================================================


def test_parse_grouping_field_single_dimension():
    """Test parsing single dimension returns tuple."""
    from langsmith_cli.commands.runs import parse_grouping_field

    result = parse_grouping_field("tag:length")
    assert result == ("tag", "length")

    result = parse_grouping_field("metadata:user_tier")
    assert result == ("metadata", "user_tier")


def test_parse_grouping_field_multi_dimensional():
    """Test parsing multiple dimensions returns list of tuples."""
    from langsmith_cli.commands.runs import parse_grouping_field

    result = parse_grouping_field("tag:length,tag:content_type")
    assert result == [("tag", "length"), ("tag", "content_type")]

    result = parse_grouping_field("tag:length,metadata:user_tier")
    assert result == [("tag", "length"), ("metadata", "user_tier")]


def test_build_multi_dimensional_fql_filter():
    """Test building combined FQL filters."""
    from langsmith_cli.commands.runs import build_multi_dimensional_fql_filter

    result = build_multi_dimensional_fql_filter(
        [("tag", "length"), ("tag", "content_type")], ["short", "news"]
    )
    assert result == 'and(has(tags, "length:short"), has(tags, "content_type:news"))'

    # Test single dimension (should not use 'and')
    result = build_multi_dimensional_fql_filter([("tag", "length")], ["medium"])
    assert result == 'has(tags, "length:medium")'

    # Test mixed tag and metadata
    result = build_multi_dimensional_fql_filter(
        [("tag", "length"), ("metadata", "user_tier")], ["short", "premium"]
    )
    assert (
        'has(tags, "length:short")' in result
        and 'in(metadata_key, ["user_tier"])' in result
    )


def test_build_multi_dimensional_fql_filter_validation():
    """Test validation of dimension/value length mismatch."""
    from langsmith_cli.commands.runs import build_multi_dimensional_fql_filter
    import pytest

    with pytest.raises(ValueError, match="Dimensions and values must have same length"):
        build_multi_dimensional_fql_filter(
            [("tag", "length"), ("tag", "content_type")],
            ["short"],  # Missing value
        )


def test_runs_sample_multi_dimensional_cartesian_product(runner):
    """Test multi-dimensional sampling with Cartesian product."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        # Mock runs for different combinations
        def mock_list_runs(project_name, limit, filter, order_by):
            # Return different runs based on filter
            if "length:short" in filter and "content_type:news" in filter:
                return [
                    create_run("short-news", tags=["length:short", "content_type:news"])
                ]
            elif "length:long" in filter and "content_type:gaming" in filter:
                return [
                    create_run(
                        "long-gaming", tags=["length:long", "content_type:gaming"]
                    )
                ]
            return []

        mock_client.list_runs.side_effect = mock_list_runs

        result = runner.invoke(
            cli,
            [
                "--json",
                "runs",
                "sample",
                "--stratify-by",
                "tag:length,tag:content_type",
                "--dimension-values",
                "short|long,news|gaming",
                "--samples-per-combination",
                "1",
            ],
        )

        assert result.exit_code == 0

        # Parse output
        lines = result.output.strip().split("\n")
        samples = [json.loads(line) for line in lines]

        # Should have samples from different combinations
        strata = {s["stratum"] for s in samples}
        # At least some combinations should be present
        assert any("length:short" in s and "content_type:news" in s for s in strata)


def test_runs_sample_multi_dimensional_manual_combinations(runner):
    """Test multi-dimensional sampling with manual combinations."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        mock_client.list_runs.return_value = [
            create_run("run1", tags=["length:short", "content_type:news"])
        ]

        result = runner.invoke(
            cli,
            [
                "--json",
                "runs",
                "sample",
                "--stratify-by",
                "tag:length,tag:content_type",
                "--values",
                "short:news,medium:gaming",
                "--samples-per-stratum",
                "1",
            ],
        )

        assert result.exit_code == 0

        # Should call list_runs for each combination
        assert mock_client.list_runs.call_count >= 2


def test_runs_sample_multi_dimensional_validation(runner):
    """Test validation of multi-dimensional parameters."""
    # Missing both --values and --dimension-values
    result = runner.invoke(
        cli,
        [
            "runs",
            "sample",
            "--stratify-by",
            "tag:length,tag:content_type",
            "--samples-per-stratum",
            "1",
        ],
    )

    assert result.exit_code != 0
    assert "requires --values or --dimension-values" in result.output


def test_runs_analyze_multi_dimensional_not_supported(runner):
    """Test that multi-dimensional grouping is not yet supported in analyze."""
    result = runner.invoke(
        cli,
        [
            "runs",
            "analyze",
            "--group-by",
            "tag:length,tag:content_type",
        ],
    )

    assert result.exit_code != 0
    assert "not yet supported" in result.output


# =============================================================================
# Discovery Commands Tests
# =============================================================================


def test_runs_tags_discovery(runner):
    """Test runs tags command discovers tag patterns."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        mock_client.list_runs.return_value = [
            create_run("run1", tags=["length:short", "env:prod"]),
            create_run("run2", tags=["length:medium", "env:dev"]),
            create_run("run3", tags=["length:long", "env:prod", "schema:v2"]),
        ]

        result = runner.invoke(
            cli,
            [
                "--json",
                "runs",
                "tags",
            ],
        )

        assert result.exit_code == 0

        data = json.loads(result.output)
        assert "tag_patterns" in data

        patterns = data["tag_patterns"]
        assert "length" in patterns
        assert set(patterns["length"]) == {"short", "medium", "long"}

        assert "env" in patterns
        assert set(patterns["env"]) == {"prod", "dev"}

        assert "schema" in patterns
        assert patterns["schema"] == ["v2"]


def test_runs_tags_discovery_table_output(runner):
    """Test runs tags command table output."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        mock_client.list_runs.return_value = [
            create_run("run1", tags=["length:short", "env:prod"]),
        ]

        result = runner.invoke(
            cli,
            [
                "runs",
                "tags",
            ],
        )

        assert result.exit_code == 0
        assert "Tag Patterns" in result.output
        assert "length" in result.output
        assert "short" in result.output


def test_runs_tags_discovery_no_tags(runner):
    """Test runs tags command with no structured tags."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        mock_client.list_runs.return_value = [
            create_run("run1", tags=["unstructured-tag"]),
        ]

        result = runner.invoke(
            cli,
            [
                "runs",
                "tags",
            ],
        )

        assert result.exit_code == 0
        assert "No structured tags found" in result.output


def test_runs_metadata_keys_discovery(runner):
    """Test runs metadata-keys command discovers metadata keys."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        # Create runs with different metadata
        run1 = create_run(
            "run1", metadata={"user_tier": "premium", "region": "us-east"}
        )
        run2 = create_run(
            "run2", metadata={"user_tier": "free", "channel_id": "abc123"}
        )
        run3 = create_run("run3", extra={"metadata": {"session_id": "xyz789"}})

        mock_client.list_runs.return_value = [run1, run2, run3]

        result = runner.invoke(
            cli,
            [
                "--json",
                "runs",
                "metadata-keys",
            ],
        )

        assert result.exit_code == 0

        data = json.loads(result.output)
        assert "metadata_keys" in data

        keys = set(data["metadata_keys"])
        assert "user_tier" in keys
        assert "region" in keys
        assert "channel_id" in keys
        assert "session_id" in keys


def test_runs_metadata_keys_discovery_table_output(runner):
    """Test runs metadata-keys command table output."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        run1 = create_run("run1", metadata={"user_tier": "premium"})

        mock_client.list_runs.return_value = [run1]

        result = runner.invoke(
            cli,
            [
                "runs",
                "metadata-keys",
            ],
        )

        assert result.exit_code == 0
        assert "Metadata Keys" in result.output
        assert "user_tier" in result.output


def test_runs_metadata_keys_discovery_no_metadata(runner):
    """Test runs metadata-keys command with no metadata."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        mock_client.list_runs.return_value = [create_run("run1")]

        result = runner.invoke(
            cli,
            [
                "runs",
                "metadata-keys",
            ],
        )

        assert result.exit_code == 0
        assert "No metadata keys found" in result.output


def test_runs_tags_discovery_sample_size(runner):
    """Test runs tags command respects --sample-size option."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        mock_client.list_runs.return_value = []

        runner.invoke(
            cli,
            [
                "runs",
                "tags",
                "--sample-size",
                "5000",
            ],
        )

        # Verify sample-size was passed as limit
        mock_client.list_runs.assert_called_once()
        args, kwargs = mock_client.list_runs.call_args
        assert kwargs["limit"] == 5000


def test_runs_list_no_truncate_flag(runner):
    """Test runs list --no-truncate flag works and shows more content."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        # Create run with very long model name
        test_run = Run(
            id=UUID("12345678-1234-5678-1234-567812345678"),
            name="Very Long Run Name That Would Normally Be Truncated",
            run_type="llm",
            start_time=datetime.now(timezone.utc),
            status="success",
            latency=1.5,
            total_tokens=1000,
            extra={
                "invocation_params": {
                    "model_name": "very-long-model-name-that-would-be-truncated-normally"
                }
            },
        )
        mock_client.list_runs.return_value = [test_run]

        result = runner.invoke(cli, ["runs", "list", "--no-truncate"])
        assert result.exit_code == 0
        # With --no-truncate, more of the model name should appear (beyond 20 chars)
        # Rich may still truncate to fit terminal width, but we should see more than the default 20-char limit
        assert "very-long-model-name-that-would-be-trunca" in result.output


def test_runs_list_default_truncate_behavior(runner):
    """Test runs list default behavior truncates long model names."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        test_run = Run(
            id=UUID("12345678-1234-5678-1234-567812345678"),
            name="Test Run",
            run_type="llm",
            start_time=datetime.now(timezone.utc),
            status="success",
            latency=1.5,
            total_tokens=1000,
            extra={
                "invocation_params": {
                    "model_name": "very-long-model-name-that-exceeds-twenty-characters"
                }
            },
        )
        mock_client.list_runs.return_value = [test_run]

        result = runner.invoke(cli, ["runs", "list"])
        assert result.exit_code == 0
        # Without --no-truncate, long model names should be truncated with "..."
        assert (
            "very-long-model-name..." in result.output
            or "very-long-model-n..." in result.output
        )


@pytest.fixture
def sample_runs_file(tmp_path):
    """Create a JSONL file with sample runs for testing."""
    test_file = tmp_path / "test_runs.jsonl"
    run1 = Run(
        id=UUID("12345678-1234-5678-1234-567812345678"),
        name="Test Run 1",
        run_type="llm",
        start_time=datetime.now(timezone.utc),
        status="success",
        latency=1.5,
        total_tokens=1000,
    )
    run2 = Run(
        id=UUID("87654321-4321-8765-4321-876543218765"),
        name="Test Run 2",
        run_type="chain",
        start_time=datetime.now(timezone.utc),
        status="error",
        latency=2.3,
        error="Test error",
    )

    with open(test_file, "w") as f:
        f.write(json.dumps(run1.model_dump(mode="json")) + "\n")
        f.write(json.dumps(run2.model_dump(mode="json")) + "\n")

    return test_file


def test_runs_view_file_basic(runner, sample_runs_file):
    """Test basic view-file command with single JSONL file."""
    from conftest import strip_ansi

    result = runner.invoke(cli, ["runs", "view-file", str(sample_runs_file)])
    assert result.exit_code == 0
    assert "Test Run 1" in result.output
    assert "Test Run 2" in result.output
    assert "success" in result.output
    assert "error" in result.output
    # Strip ANSI codes from Rich output for plain text matching
    assert "Loaded 2 runs from 1 file(s)" in strip_ansi(result.output)


def test_runs_view_file_glob_pattern(runner, tmp_path):
    """Test view-file with glob pattern matching multiple files."""
    from conftest import strip_ansi

    # Create multiple test files
    for i in range(3):
        test_file = tmp_path / f"runs_{i}.jsonl"
        run = Run(
            id=UUID(f"1234567{i}-1234-5678-1234-567812345678"),
            name=f"Run {i}",
            run_type="llm",
            start_time=datetime.now(timezone.utc),
            status="success",
        )
        with open(test_file, "w") as f:
            f.write(json.dumps(run.model_dump(mode="json")) + "\n")

    result = runner.invoke(cli, ["runs", "view-file", str(tmp_path / "runs_*.jsonl")])
    assert result.exit_code == 0
    assert "Run 0" in result.output
    assert "Run 1" in result.output
    assert "Run 2" in result.output
    # Strip ANSI codes from Rich output for plain text matching
    assert "Loaded 3 runs from 3 file(s)" in strip_ansi(result.output)


@pytest.mark.parametrize(
    "extra_args,expected_fields",
    [
        ([], None),  # All fields
        (["--fields", "id,name"], {"id", "name"}),
    ],
)
def test_runs_view_file_output_modes(
    runner, sample_runs_file, extra_args, expected_fields
):
    """Test view-file with different output modes and field filtering."""
    result = runner.invoke(
        cli, ["--json", "runs", "view-file", str(sample_runs_file)] + extra_args
    )
    assert result.exit_code == 0

    data = json.loads(result.output)
    assert isinstance(data, list)
    assert len(data) == 2
    assert data[0]["name"] == "Test Run 1"

    if expected_fields:
        assert set(data[0].keys()) == expected_fields


def test_runs_view_file_no_truncate(runner, tmp_path):
    """Test view-file with --no-truncate flag."""
    test_file = tmp_path / "test_runs.jsonl"
    run = Run(
        id=UUID("12345678-1234-5678-1234-567812345678"),
        name="Very Long Run Name That Would Normally Be Truncated In Tables",
        run_type="llm",
        start_time=datetime.now(timezone.utc),
        status="success",
        extra={
            "invocation_params": {
                "model_name": "very-long-model-name-that-would-be-truncated-normally"
            }
        },
    )

    with open(test_file, "w") as f:
        f.write(json.dumps(run.model_dump(mode="json")) + "\n")

    result = runner.invoke(cli, ["runs", "view-file", str(test_file), "--no-truncate"])
    assert result.exit_code == 0
    # With --no-truncate, more of the model name should appear
    assert "very-long-model-name-that-would-be-trunca" in result.output


@pytest.mark.parametrize(
    "file_content,expected_msg",
    [
        ("nonexistent.jsonl", "No files match pattern"),
        ('{"invalid json\n', "Warning"),
        ("", "No valid runs found"),
    ],
)
def test_runs_view_file_error_handling(runner, tmp_path, file_content, expected_msg):
    """Test view-file handles various error cases gracefully."""
    if file_content == "nonexistent.jsonl":
        # Test missing file
        result = runner.invoke(cli, ["runs", "view-file", file_content])
        assert result.exit_code != 0
    else:
        # Create test file with given content
        test_file = tmp_path / "test.jsonl"
        test_file.write_text(file_content)
        result = runner.invoke(cli, ["runs", "view-file", str(test_file)])
        assert result.exit_code == 0

    assert expected_msg in result.output


# Hebrew/Unicode Preservation Tests


def test_runs_list_json_preserves_hebrew(runner):
    """Test that runs list --json preserves Hebrew characters without escaping."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        # Create run with Hebrew text
        test_run = Run(
            id=UUID("12345678-1234-5678-1234-567812345678"),
            name="תופים זה החיים",  # Hebrew: "Drums is life"
            run_type="chain",
            start_time=datetime.now(timezone.utc),
            status="success",
            latency=0.5,
            inputs={"stream_title": "תופים זה החיים", "language": "he-IL"},
            outputs={"message": "היי, מה קורה?"},  # Hebrew: "Hi, what's up?"
        )
        mock_client.list_runs.return_value = [test_run]

        result = runner.invoke(cli, ["--json", "runs", "list", "--limit", "1"])
        assert result.exit_code == 0

        # Parse JSON
        data = json.loads(result.output)
        assert len(data) == 1

        # Verify Hebrew is preserved (not escaped as \u05ea etc)
        assert data[0]["name"] == "תופים זה החיים"
        assert data[0]["inputs"]["stream_title"] == "תופים זה החיים"
        assert data[0]["outputs"]["message"] == "היי, מה קורה?"

        # Verify it's NOT escaped Unicode
        assert "\\u05" not in result.output


def test_runs_get_json_preserves_hebrew(runner):
    """Test that runs get --json preserves Hebrew characters."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        test_run = Run(
            id=UUID("019bc1d8-ba71-7120-babf-1b41dfaeaa40"),
            name="task-simulate-chat-chain",
            run_type="chain",
            start_time=datetime.now(timezone.utc),
            status="success",
            inputs={
                "stream_title": "תופים זה החיים",
                "transcripts": '- aviad: "היי, מה קורה? האם אתם יפים?"',
            },
            outputs={
                "messages": [
                    {
                        "message": "מה הקיק שאתה משתמש בלייבים?",
                        "user_name": "persona_alice",
                    },
                    {"message": "אני מת לנסות גרוב ב-7/8", "user_name": "persona_bob"},
                ]
            },
        )
        mock_client.read_run.return_value = test_run

        result = runner.invoke(
            cli, ["--json", "runs", "get", "019bc1d8-ba71-7120-babf-1b41dfaeaa40"]
        )
        assert result.exit_code == 0

        # Parse JSON
        data = json.loads(result.output)

        # Verify Hebrew is preserved
        assert data["inputs"]["stream_title"] == "תופים זה החיים"
        assert "היי, מה קורה?" in data["inputs"]["transcripts"]
        assert (
            data["outputs"]["messages"][0]["message"] == "מה הקיק שאתה משתמש בלייבים?"
        )

        # Verify it's NOT escaped Unicode
        assert "\\u05" not in result.output


def test_runs_sample_file_output_preserves_hebrew(runner, tmp_path):
    """Test that runs sample --output preserves Hebrew when writing to file."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        # Create runs with Hebrew
        runs_with_hebrew = [
            Run(
                id=UUID("12345678-1234-5678-1234-567812345678"),
                name="תופים זה החיים",
                run_type="chain",
                start_time=datetime.now(timezone.utc),
                status="success",
                tags=["length:short"],
                inputs={"text": "היי, מה קורה?"},
                outputs={"response": "שלום!"},
            ),
            Run(
                id=UUID("12345678-1234-5678-1234-567812345679"),
                name="רוק אנד רול",
                run_type="chain",
                start_time=datetime.now(timezone.utc),
                status="success",
                tags=["length:short"],
                inputs={"text": "אני אוהב מוזיקה"},
                outputs={"response": "גם אני!"},
            ),
        ]

        mock_client.list_runs.return_value = iter(runs_with_hebrew)

        output_file = tmp_path / "hebrew_samples.jsonl"
        result = runner.invoke(
            cli,
            [
                "runs",
                "sample",
                "--stratify-by",
                "tag:length",
                "--values",
                "short",
                "--samples-per-stratum",
                "2",
                "--output",
                str(output_file),
            ],
        )
        assert result.exit_code == 0

        # Read file and verify Hebrew is preserved
        with open(output_file, "r", encoding="utf-8") as f:
            lines = f.readlines()
            assert len(lines) == 2

            # Parse first line
            data1 = json.loads(lines[0])
            assert data1["name"] == "תופים זה החיים"
            assert data1["inputs"]["text"] == "היי, מה קורה?"
            assert data1["outputs"]["response"] == "שלום!"

            # Parse second line
            data2 = json.loads(lines[1])
            assert data2["name"] == "רוק אנד רול"

        # Verify file content doesn't have escaped Unicode
        file_content = output_file.read_text(encoding="utf-8")
        assert "\\u05" not in file_content
        assert "תופים" in file_content


def test_runs_view_file_preserves_hebrew_round_trip(runner, tmp_path):
    """Test complete round-trip: write file with Hebrew → read with view-file → display preserves Hebrew."""
    # Create JSONL file with Hebrew content
    test_file = tmp_path / "hebrew_runs.jsonl"

    run1 = Run(
        id=UUID("12345678-1234-5678-1234-567812345678"),
        name="תופים זה החיים",
        run_type="chain",
        start_time=datetime.now(timezone.utc),
        status="success",
        inputs={"text": "היי, מה קורה?"},
        outputs={"response": "שלום מישראל!"},
    )

    run2 = Run(
        id=UUID("12345678-1234-5678-1234-567812345679"),
        name="רוק אנד רול",
        run_type="chain",
        start_time=datetime.now(timezone.utc),
        status="error",
        inputs={"text": "אני אוהב מוזיקה"},
        outputs={"response": "גם אני אוהב!"},
    )

    # Write to file with Hebrew preserved (using ensure_ascii=False)
    with open(test_file, "w", encoding="utf-8") as f:
        f.write(
            json.dumps(run1.model_dump(mode="json"), ensure_ascii=False, default=str)
            + "\n"
        )
        f.write(
            json.dumps(run2.model_dump(mode="json"), ensure_ascii=False, default=str)
            + "\n"
        )

    # Test table output
    result = runner.invoke(cli, ["runs", "view-file", str(test_file)])
    assert result.exit_code == 0
    assert "תופים זה החיים" in result.output
    assert "רוק אנד רול" in result.output

    # Test JSON output preserves Hebrew
    result_json = runner.invoke(cli, ["--json", "runs", "view-file", str(test_file)])
    assert result_json.exit_code == 0

    data = json.loads(result_json.output)
    assert len(data) == 2
    assert data[0]["name"] == "תופים זה החיים"
    assert data[0]["inputs"]["text"] == "היי, מה קורה?"
    assert data[0]["outputs"]["response"] == "שלום מישראל!"
    assert data[1]["name"] == "רוק אנד רול"

    # Verify no Unicode escaping
    assert "\\u05" not in result_json.output


def test_runs_view_file_with_fields_preserves_hebrew(runner, tmp_path):
    """Test that --fields selection still preserves Hebrew."""
    test_file = tmp_path / "hebrew_runs.jsonl"

    run1 = Run(
        id=UUID("12345678-1234-5678-1234-567812345678"),
        name="תופים זה החיים",
        run_type="chain",
        start_time=datetime.now(timezone.utc),
        status="success",
        inputs={"stream_title": "תוכנית תופים", "language": "he-IL"},
    )

    with open(test_file, "w", encoding="utf-8") as f:
        f.write(
            json.dumps(run1.model_dump(mode="json"), ensure_ascii=False, default=str)
            + "\n"
        )

    result = runner.invoke(
        cli,
        ["--json", "runs", "view-file", str(test_file), "--fields", "id,name,inputs"],
    )
    assert result.exit_code == 0

    data = json.loads(result.output)
    assert len(data) == 1
    assert data[0]["name"] == "תופים זה החיים"
    assert data[0]["inputs"]["stream_title"] == "תוכנית תופים"

    # Verify no Unicode escaping
    assert "\\u05" not in result.output


def test_runs_get_latest_basic(runner):
    """Test basic runs get-latest command."""
    from conftest import create_run

    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        run = create_run(
            name="Latest Run", id_str="12345678-1234-5678-1234-567812345678"
        )
        mock_client.list_runs.return_value = iter([run])

        result = runner.invoke(cli, ["runs", "get-latest", "--project", "test"])
        assert result.exit_code == 0
        assert "Latest Run" in result.output
        assert "12345678-1234-5678-1234-567812345678" in result.output


def test_runs_get_latest_json_output(runner):
    """Test runs get-latest with JSON output."""
    from conftest import create_run

    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        run = create_run(name="Latest Run")
        mock_client.list_runs.return_value = iter([run])

        result = runner.invoke(
            cli, ["--json", "runs", "get-latest", "--project", "test"]
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["name"] == "Latest Run"


def test_runs_get_latest_with_fields(runner):
    """Test runs get-latest with --fields option."""
    from conftest import create_run

    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        run = create_run(
            name="Latest Run",
            inputs={"text": "test input"},
            outputs={"response": "test output"},
        )
        mock_client.list_runs.return_value = iter([run])

        result = runner.invoke(
            cli,
            [
                "--json",
                "runs",
                "get-latest",
                "--project",
                "test",
                "--fields",
                "inputs,outputs",
            ],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "inputs" in data
        assert "outputs" in data
        assert data["inputs"]["text"] == "test input"
        # Should not include other fields like 'id', 'name'
        assert "id" not in data
        assert "name" not in data


def test_runs_get_latest_with_status_filter(runner):
    """Test runs get-latest with --failed flag."""
    from conftest import create_run

    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        run = create_run(name="Failed Run", error="Test error")
        mock_client.list_runs.return_value = iter([run])

        result = runner.invoke(
            cli, ["--json", "runs", "get-latest", "--project", "test", "--failed"]
        )
        assert result.exit_code == 0
        # Verify error filter was passed to API
        mock_client.list_runs.assert_called_once()
        call_kwargs = mock_client.list_runs.call_args[1]
        assert call_kwargs["error"] is True


def test_runs_get_latest_with_succeeded_flag(runner):
    """Test runs get-latest with --succeeded flag."""
    from conftest import create_run

    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        run = create_run(name="Success Run")
        mock_client.list_runs.return_value = iter([run])

        result = runner.invoke(
            cli, ["--json", "runs", "get-latest", "--project", "test", "--succeeded"]
        )
        assert result.exit_code == 0
        # Verify error filter was passed to API
        call_kwargs = mock_client.list_runs.call_args[1]
        assert call_kwargs["error"] is False


def test_runs_get_latest_with_roots_flag(runner):
    """Test runs get-latest with --roots flag."""
    from conftest import create_run

    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        run = create_run(name="Root Run")
        mock_client.list_runs.return_value = iter([run])

        result = runner.invoke(
            cli, ["--json", "runs", "get-latest", "--project", "test", "--roots"]
        )
        assert result.exit_code == 0
        # Verify is_root was passed to API
        call_kwargs = mock_client.list_runs.call_args[1]
        assert call_kwargs["is_root"] is True


def test_runs_get_latest_no_runs_found(runner):
    """Test runs get-latest when no runs match filters."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        mock_client.list_runs.return_value = iter([])  # Empty iterator

        result = runner.invoke(
            cli, ["runs", "get-latest", "--project", "test", "--failed"]
        )
        assert result.exit_code == 1
        assert "No runs found" in result.output


def test_runs_get_latest_with_multiple_projects(runner):
    """Test runs get-latest with project pattern searching multiple projects."""
    from conftest import create_run, create_project

    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        # First project has no runs, second has a run - use real Pydantic models
        mock_client.list_projects.return_value = [
            create_project(name="prd/project1"),
            create_project(name="prd/project2"),
        ]

        def list_runs_side_effect(**kwargs):
            if kwargs["project_name"] == "prd/project1":
                return iter([])  # No runs
            else:
                return iter([create_run(name="Run from project2")])

        mock_client.list_runs.side_effect = list_runs_side_effect

        result = runner.invoke(
            cli, ["--json", "runs", "get-latest", "--project-name-pattern", "prd/*"]
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["name"] == "Run from project2"


def test_runs_get_latest_with_tag_filter(runner):
    """Test runs get-latest with --tag filter."""
    from conftest import create_run

    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        run = create_run(name="Tagged Run", tags=["prod", "critical"])
        mock_client.list_runs.return_value = iter([run])

        result = runner.invoke(
            cli,
            [
                "--json",
                "runs",
                "get-latest",
                "--project",
                "test",
                "--tag",
                "prod",
                "--tag",
                "critical",
            ],
        )
        assert result.exit_code == 0
        # Verify filter was built correctly with tags
        call_kwargs = mock_client.list_runs.call_args[1]
        assert call_kwargs["filter"] is not None
        assert 'has(tags, "prod")' in call_kwargs["filter"]
        assert 'has(tags, "critical")' in call_kwargs["filter"]
