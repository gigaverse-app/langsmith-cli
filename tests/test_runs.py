from langsmith_cli.main import cli
from unittest.mock import patch, MagicMock


def test_runs_list(runner):
    """Test the runs list command."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        mock_run = MagicMock()
        mock_run.id = "run-123"
        mock_run.name = "My Run"
        mock_run.status = "success"
        mock_run.latency = 0.5
        mock_run.error = None
        mock_client.list_runs.return_value = [mock_run]

        result = runner.invoke(cli, ["runs", "list"])
        assert result.exit_code == 0
        assert "run-123" in result.output
        assert "My Run" in result.output
        assert "success" in result.output


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
        mock_run = MagicMock()
        mock_run.id = "run-456"
        mock_run.name = "Detailed Run"
        mock_run.inputs = {"q": "hello"}
        mock_run.outputs = {"a": "world"}
        mock_run.dict.return_value = {
            "id": "run-456",
            "name": "Detailed Run",
            "inputs": {"q": "hello"},
            "outputs": {"a": "world"},
        }
        mock_client.read_run.return_value = mock_run

        # Use --json to checking the raw output mostly, but default is table/text
        result = runner.invoke(cli, ["--json", "runs", "get", "run-456"])
        assert result.exit_code == 0, f"Exit code {result.exit_code}: {result.output}"
        assert "run-456" in result.output
        assert "hello" in result.output


def test_runs_get_fields(runner):
    """Test runs get with pruning fields."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        mock_run = MagicMock()
        # Full dict
        full_data = {
            "id": "run-789",
            "inputs": "foo",
            "outputs": "bar",
            "extra_heavy_field": "huge_data",
        }
        mock_run.dict.return_value = full_data
        mock_client.read_run.return_value = mock_run

        result = runner.invoke(
            cli, ["--json", "runs", "get", "run-789", "--fields", "inputs"]
        )
        assert result.exit_code == 0, f"Exit code {result.exit_code}: {result.output}"

        # Should contain inputs
        assert "foo" in result.output
        # Should NOT contain extra_heavy_field
        assert "huge_data" not in result.output


def test_runs_search(runner):
    """Test the runs search command."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        mock_run = MagicMock()
        mock_run.name = "search-result"
        mock_run.id = "search-id"
        mock_run.status = "success"
        mock_run.latency = 0.5
        mock_client.list_runs.return_value = [mock_run]

        # Use the search command
        result = runner.invoke(cli, ["runs", "search", "--filter", "eq(name, 'test')"])
        assert result.exit_code == 0
        assert "search-result" in result.output
        # Verify list_runs was called with the filter
        mock_client.list_runs.assert_called_once()
        args, kwargs = mock_client.list_runs.call_args
        assert kwargs["filter"] == "eq(name, 'test')"


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
        mock_client.list_runs.return_value = []

        runner.invoke(cli, ["runs", "list", "--name-pattern", "*auth*"])

        # Verify FQL search filter was constructed
        mock_client.list_runs.assert_called_once()
        args, kwargs = mock_client.list_runs.call_args
        assert 'search("auth")' in kwargs["filter"]


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
        mock_client.list_runs.return_value = []

        runner.invoke(
            cli,
            ["runs", "list", "--tag", "prod", "--slow", "--name-pattern", "*api*"],
        )

        # Verify all filters are combined with AND
        args, kwargs = mock_client.list_runs.call_args
        assert 'has(tags, "prod")' in kwargs["filter"]
        assert 'gt(latency, "5s")' in kwargs["filter"]
        assert 'search("api")' in kwargs["filter"]
        assert kwargs["filter"].startswith("and(")


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

        runner.invoke(cli, ["runs", "list", "--min-latency", "1s", "--max-latency", "5s"])

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
        assert 'T' in kwargs["filter"]


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
