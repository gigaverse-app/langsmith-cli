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
