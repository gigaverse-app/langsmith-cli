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

        # Create mock runs with different names
        run1 = MagicMock()
        run1.name = "test-auth-v1"
        run1.id = "1"
        run1.status = "success"
        run1.latency = 0.5

        run2 = MagicMock()
        run2.name = "test-auth-v2"
        run2.id = "2"
        run2.status = "success"
        run2.latency = 0.6

        run3 = MagicMock()
        run3.name = "prod-checkout"
        run3.id = "3"
        run3.status = "success"
        run3.latency = 0.7

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

        run1 = MagicMock()
        run1.name = "auth-service"
        run1.id = "1"
        run1.status = "success"
        run1.latency = 0.5

        run2 = MagicMock()
        run2.name = "test-auth"
        run2.id = "2"
        run2.status = "success"
        run2.latency = 0.6

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

        run1 = MagicMock()
        run1.name = "zebra"
        run1.id = "1"
        run1.status = "success"
        run1.latency = 2.0

        run2 = MagicMock()
        run2.name = "alpha"
        run2.id = "2"
        run2.status = "error"
        run2.latency = 1.0

        run3 = MagicMock()
        run3.name = "beta"
        run3.id = "3"
        run3.status = "success"
        run3.latency = 3.0

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
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        run1 = MagicMock()
        run1.name = "fast"
        run1.id = "1"
        run1.status = "success"
        run1.latency = 1.0

        run2 = MagicMock()
        run2.name = "slow"
        run2.id = "2"
        run2.status = "success"
        run2.latency = 5.0

        mock_client.list_runs.return_value = iter([run1, run2])

        # Sort by latency descending
        result = runner.invoke(cli, ["runs", "list", "--sort-by", "-latency"])

        assert result.exit_code == 0
        # Slow (5.0s) should appear before fast (1.0s)
        slow_pos = result.output.find("slow")
        fast_pos = result.output.find("fast")
        assert slow_pos < fast_pos


def test_runs_list_with_csv_format(runner):
    """Test runs list with CSV export."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        run1 = MagicMock()
        run1.dict.return_value = {"id": "1", "name": "test-run", "status": "success"}

        mock_client.list_runs.return_value = iter([run1])

        result = runner.invoke(cli, ["runs", "list", "--format", "csv"])

        assert result.exit_code == 0
        # CSV should have header and data rows
        assert "id,name,status" in result.output
        assert "1,test-run,success" in result.output


def test_runs_list_with_yaml_format(runner):
    """Test runs list with YAML export."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        run1 = MagicMock()
        run1.dict.return_value = {"id": "1", "name": "test-run", "status": "success"}

        mock_client.list_runs.return_value = iter([run1])

        result = runner.invoke(cli, ["runs", "list", "--format", "yaml"])

        assert result.exit_code == 0
        # YAML should contain the data
        assert "id:" in result.output
        assert "name: test-run" in result.output


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
        mock_run = MagicMock()
        mock_run.name = "test"
        mock_run.id = "1"
        mock_run.status = "success"
        mock_run.latency = 1.0
        mock_client.list_runs.return_value = [mock_run]

        # Invalid regex pattern
        result = runner.invoke(cli, ["runs", "list", "--name-regex", "[invalid("])
        assert result.exit_code != 0
        assert "Invalid regex pattern" in result.output


def test_runs_get_rich_output(runner):
    """Test runs get with Rich console output (not JSON mode)."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        mock_run = MagicMock()
        mock_run.dict.return_value = {
            "id": "run-rich-123",
            "name": "Rich Output Test",
            "status": "success",
            "inputs": {"query": "test"},
            "outputs": {"result": "success"},
        }
        mock_client.read_run.return_value = mock_run

        # No --json flag, should use Rich output
        result = runner.invoke(cli, ["runs", "get", "run-rich-123"])

        assert result.exit_code == 0
        assert "run-rich-123" in result.output
        assert "Rich Output Test" in result.output
        assert "status" in result.output
        assert "inputs" in result.output


def test_runs_get_with_complex_data_types(runner):
    """Test runs get handles dict and list data types."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        mock_run = MagicMock()
        mock_run.dict.return_value = {
            "id": "run-complex",
            "name": "Complex Data",
            "metadata": {"key": "value", "nested": {"deep": "data"}},
            "tags": ["tag1", "tag2"],
            "simple_field": "simple_value",
        }
        mock_client.read_run.return_value = mock_run

        result = runner.invoke(cli, ["runs", "get", "run-complex"])

        assert result.exit_code == 0
        assert "metadata" in result.output
        assert "tags" in result.output
        assert "simple_field" in result.output


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
        mock_run = MagicMock()
        mock_run.id = "watch-run"
        mock_run.name = "Watched Run"
        mock_run.status = "success"
        mock_run.latency = 1.0

        # Make list_runs raise KeyboardInterrupt after first call
        mock_client.list_runs.side_effect = [
            [mock_run],  # First call succeeds
            KeyboardInterrupt(),  # Second call interrupted
        ]

        # Use timeout to prevent hanging
        with patch("time.sleep") as mock_sleep:
            mock_sleep.side_effect = KeyboardInterrupt()
            result = runner.invoke(cli, ["runs", "watch", "--project", "test"])

        # Should exit cleanly on KeyboardInterrupt
        assert result.exit_code == 0
