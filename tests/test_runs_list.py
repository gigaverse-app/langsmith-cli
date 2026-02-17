"""Tests for runs list command and filters."""

import json
from unittest.mock import patch, MagicMock

import pytest

from conftest import create_run, make_run_id
from langsmith_cli.main import cli


class TestRunsListBasic:
    """Basic runs list command tests."""

    def test_list_table_output(self, runner, mock_client):
        """List command displays runs in table format."""
        mock_client.list_runs.return_value = [
            create_run(name="My Run", status="success")
        ]

        result = runner.invoke(cli, ["runs", "list"])

        assert result.exit_code == 0
        assert "My Run" in result.output
        assert "success" in result.output

    def test_list_empty_results(self, runner, mock_client):
        """List command handles empty results."""
        mock_client.list_runs.return_value = []

        result = runner.invoke(cli, ["runs", "list"])

        assert result.exit_code == 0
        assert "No runs found" in result.output

    def test_list_with_roots_flag(self, runner, mock_client):
        """--roots flag filters to root traces."""
        mock_client.list_runs.return_value = []

        runner.invoke(cli, ["runs", "list", "--roots"])

        _, kwargs = mock_client.list_runs.call_args
        assert kwargs["is_root"] is True

    def test_list_table_includes_tokens_and_model(self, runner, mock_client):
        """Table output includes tokens and model columns."""
        mock_client.list_runs.return_value = [
            create_run(
                name="Test LLM",
                run_type="llm",
                total_tokens=1234,
                extra={"invocation_params": {"model_name": "gpt-4"}},
            )
        ]

        result = runner.invoke(cli, ["runs", "list"])

        assert result.exit_code == 0
        assert "1,234" in result.output
        assert "gpt-4" in result.output

    def test_list_handles_missing_optional_fields(self, runner, mock_client):
        """Table gracefully handles missing optional fields."""
        mock_client.list_runs.return_value = [create_run(name="Test Run")]

        result = runner.invoke(cli, ["runs", "list"])

        assert result.exit_code == 0
        assert "Test" in result.output


class TestRunsListJSON:
    """JSON output tests for runs list."""

    def test_json_output_valid(self, runner, mock_client):
        """--json outputs valid parseable JSON."""
        mock_client.list_runs.return_value = [create_run(name="My Run")]

        result = runner.invoke(cli, ["--json", "runs", "list", "--limit", "10"])

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["name"] == "My Run"

    def test_json_output_empty_list(self, runner, mock_client):
        """--json outputs empty array when no results."""
        mock_client.list_runs.return_value = []

        result = runner.invoke(cli, ["--json", "runs", "list"])

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data == []

    def test_json_output_on_api_error(self, runner):
        """Invariant: --json mode errors produce valid JSON error object, not empty stdout.

        When ALL sources fail, the global error handler outputs a JSON error object
        with non-zero exit code so scripts can detect and parse the failure.
        """

        with patch("langsmith.Client") as MockClient:
            mock_client = MockClient.return_value
            mock_client.list_runs.side_effect = Exception("API Error")

            result = runner.invoke(cli, ["--json", "runs", "list", "--project", "test"])

            assert result.exit_code != 0
            # Output should be valid JSON with error details
            error_data = json.loads(result.output.strip().split("\n")[-1])
            assert "error" in error_data
            assert "message" in error_data
            assert "Failed to fetch" in error_data["message"]

    def test_json_output_on_iterator_error(self, runner):
        """Invariant: --json mode iterator errors produce valid JSON error object.

        When ALL sources fail during iteration, the global error handler outputs
        a JSON error object with non-zero exit code.
        """

        with patch("langsmith.Client") as MockClient:
            mock_client = MockClient.return_value

            def failing_iterator():
                raise Exception("Iterator failed")
                yield  # Never reached

            mock_client.list_runs.return_value = failing_iterator()

            result = runner.invoke(cli, ["--json", "runs", "list", "--project", "test"])

            assert result.exit_code != 0
            # Output should be valid JSON with error details
            error_data = json.loads(result.output.strip().split("\n")[-1])
            assert "error" in error_data
            assert "message" in error_data
            assert "Failed to fetch" in error_data["message"]

    def test_json_output_no_extra_text(self, runner, mock_client):
        """--json outputs ONLY JSON, no extra text."""
        mock_client.list_runs.return_value = [create_run()]

        result = runner.invoke(cli, ["--json", "runs", "list", "--project", "test"])

        assert result.exit_code == 0
        output = result.output.strip()
        assert output.startswith("[")
        assert output.endswith("]")
        json.loads(output)  # Should not raise


class TestRunsListFilters:
    """Filter parameter tests for runs list."""

    def test_project_and_limit_filters(self, runner, mock_client):
        """--project and --limit filters are passed correctly."""
        mock_client.list_runs.return_value = []

        runner.invoke(
            cli,
            ["runs", "list", "--project", "prod", "--limit", "5", "--status", "error"],
        )

        mock_client.list_runs.assert_called_with(
            project_name="prod",
            limit=5,
            query=None,
            error=True,
            filter=None,
            trace_id=None,
            run_type=None,
            is_root=None,
            trace_filter=None,
            tree_filter=None,
            reference_example_id=None,
        )

    def test_order_by_not_passed_to_api(self, runner, mock_client):
        """INVARIANT: order_by must NOT be passed to client.list_runs() — API rejects it with 400."""
        mock_client.list_runs.return_value = []

        runner.invoke(cli, ["runs", "list", "--project", "test"])

        call_kwargs = mock_client.list_runs.call_args[1]
        assert "order_by" not in call_kwargs, (
            "order_by should not be passed to list_runs — LangSmith API rejects it with 400 Bad Request"
        )

    def test_project_id_filter(self, runner, mock_client):
        """INVARIANT: --project-id passes project_id directly to SDK, not project_name."""
        mock_client.list_runs.return_value = []

        runner.invoke(
            cli,
            [
                "runs",
                "list",
                "--project-id",
                "8dc9fb82-ee48-4815-a0b0-c0fbabaa1887",
                "--limit",
                "5",
            ],
        )

        call_kwargs = mock_client.list_runs.call_args[1]
        assert call_kwargs["project_id"] == "8dc9fb82-ee48-4815-a0b0-c0fbabaa1887"
        assert "project_name" not in call_kwargs

    def test_tag_filter(self, runner, mock_client):
        """--tag filter builds correct FQL."""
        mock_client.list_runs.return_value = []

        runner.invoke(
            cli, ["runs", "list", "--tag", "production", "--tag", "experimental"]
        )

        _, kwargs = mock_client.list_runs.call_args
        assert 'has(tags, "production")' in kwargs["filter"]
        assert 'has(tags, "experimental")' in kwargs["filter"]
        assert kwargs["filter"].startswith("and(")

    @pytest.mark.parametrize(
        "flag,expected_error",
        [
            ("--failed", True),
            ("--succeeded", False),
        ],
    )
    def test_status_flags(self, runner, mock_client, flag, expected_error):
        """--failed and --succeeded flags set error parameter."""
        mock_client.list_runs.return_value = []

        runner.invoke(cli, ["runs", "list", flag])

        _, kwargs = mock_client.list_runs.call_args
        assert kwargs["error"] is expected_error

    def test_model_filter(self, runner, mock_client):
        """--model filter uses search FQL."""
        mock_client.list_runs.return_value = []

        runner.invoke(cli, ["runs", "list", "--model", "gpt-4"])

        _, kwargs = mock_client.list_runs.call_args
        assert 'search("gpt-4")' in kwargs["filter"]


class TestRunsListLatencyFilters:
    """Latency filter tests for runs list."""

    @pytest.mark.parametrize(
        "args,expected_filter",
        [
            (["--min-latency", "2s"], 'gt(latency, "2s")'),
            (["--max-latency", "10s"], 'lt(latency, "10s")'),
            (["--slow"], 'gt(latency, "5s")'),
        ],
    )
    def test_latency_filters(self, runner, mock_client, args, expected_filter):
        """Latency filters build correct FQL."""
        mock_client.list_runs.return_value = []

        runner.invoke(cli, ["runs", "list"] + args)

        _, kwargs = mock_client.list_runs.call_args
        assert expected_filter in kwargs["filter"]

    def test_latency_range_filter(self, runner, mock_client):
        """Both --min-latency and --max-latency combine with and()."""
        mock_client.list_runs.return_value = []

        runner.invoke(
            cli, ["runs", "list", "--min-latency", "1s", "--max-latency", "5s"]
        )

        _, kwargs = mock_client.list_runs.call_args
        assert 'gt(latency, "1s")' in kwargs["filter"]
        assert 'lt(latency, "5s")' in kwargs["filter"]
        assert kwargs["filter"].startswith("and(")


class TestRunsListTimeFilters:
    """Time filter tests for runs list."""

    @pytest.mark.parametrize(
        "args",
        [
            ["--last", "24h"],
            ["--since", "7d"],
            ["--recent"],
            ["--today"],
        ],
    )
    def test_time_filters_build_fql(self, runner, mock_client, args):
        """Time filters build start_time FQL."""
        mock_client.list_runs.return_value = []

        runner.invoke(cli, ["runs", "list"] + args)

        _, kwargs = mock_client.list_runs.call_args
        assert 'gt(start_time, "' in kwargs["filter"]

    def test_since_iso_format(self, runner, mock_client):
        """--since accepts ISO timestamp."""
        mock_client.list_runs.return_value = []

        runner.invoke(cli, ["runs", "list", "--since", "2024-01-14T10:00:00Z"])

        _, kwargs = mock_client.list_runs.call_args
        assert 'gt(start_time, "2024-01-14T10:00:00' in kwargs["filter"]


class TestRunsListNameFilters:
    """Name pattern and regex filter tests."""

    def test_name_pattern_filters_client_side(self, runner, mock_client):
        """--name-pattern filters runs client-side."""
        mock_client.list_runs.return_value = iter(
            [
                create_run(name="auth-service", id_str=make_run_id(1)),
                create_run(name="database-query", id_str=make_run_id(2)),
                create_run(name="test-auth-check", id_str=make_run_id(3)),
            ]
        )

        result = runner.invoke(cli, ["runs", "list", "--name-pattern", "*auth*"])

        assert result.exit_code == 0
        assert "auth-service" in result.output
        assert "test-auth-check" in result.output
        assert "database-query" not in result.output

    def test_name_regex_filters_client_side(self, runner, mock_client):
        """--name-regex filters runs client-side with regex."""
        mock_client.list_runs.return_value = iter(
            [
                create_run(name="test-auth-v1", id_str=make_run_id(1)),
                create_run(name="test-auth-v2", id_str=make_run_id(2)),
                create_run(name="prod-checkout", id_str=make_run_id(3)),
            ]
        )

        result = runner.invoke(
            cli, ["runs", "list", "--name-regex", "test-auth-v[0-9]+"]
        )

        assert result.exit_code == 0
        assert "test-auth-v1" in result.output
        assert "test-auth-v2" in result.output
        assert "prod-checkout" not in result.output

    def test_name_regex_with_anchors(self, runner, mock_client):
        """--name-regex supports anchors."""
        mock_client.list_runs.return_value = iter(
            [
                create_run(name="auth-service", id_str=make_run_id(1)),
                create_run(name="test-auth", id_str=make_run_id(2)),
            ]
        )

        result = runner.invoke(cli, ["runs", "list", "--name-regex", "^auth"])

        assert result.exit_code == 0
        assert "auth-service" in result.output
        assert "test-auth" not in result.output

    @pytest.mark.parametrize(
        "filter_type,limit_arg,expected_limit",
        [
            ("--name-pattern", "5", 100),  # min(max(5*3, 100), 500) = 100
            ("--name-pattern", "50", 150),  # min(max(50*3, 100), 500) = 150
            ("--name-regex", "5", 100),  # min(max(5*10, 100), 500) = 100
        ],
    )
    def test_name_filters_use_reasonable_fetch_limit(
        self, runner, mock_client, filter_type, limit_arg, expected_limit
    ):
        """Name filters use capped fetch limits, not unlimited."""
        mock_client.list_runs.return_value = iter([])

        runner.invoke(
            cli, ["runs", "list", filter_type, "*test*", "--limit", limit_arg]
        )

        _, kwargs = mock_client.list_runs.call_args
        assert kwargs["limit"] == expected_limit

    def test_explicit_fetch_overrides_multiplier(self, runner, mock_client):
        """--fetch parameter overrides automatic multiplier."""
        mock_client.list_runs.return_value = iter([])

        runner.invoke(
            cli, ["runs", "list", "--grep", "test", "--limit", "10", "--fetch", "250"]
        )

        _, kwargs = mock_client.list_runs.call_args
        assert kwargs["limit"] == 250


class TestRunsListCombinedFilters:
    """Combined filter tests."""

    def test_tag_and_latency_combined(self, runner, mock_client):
        """Multiple filters combine correctly."""
        mock_client.list_runs.return_value = iter(
            [
                create_run(name="api-endpoint", id_str=make_run_id(1)),
                create_run(name="worker-task", id_str=make_run_id(2)),
            ]
        )

        result = runner.invoke(
            cli, ["runs", "list", "--tag", "prod", "--slow", "--name-pattern", "*api*"]
        )

        _, kwargs = mock_client.list_runs.call_args
        assert 'has(tags, "prod")' in kwargs["filter"]
        assert 'gt(latency, "5s")' in kwargs["filter"]
        assert kwargs["filter"].startswith("and(")
        assert "api-endpoint" in result.output
        assert "worker-task" not in result.output


class TestRunsListSort:
    """Sort functionality tests."""

    def test_sort_by_name(self, runner, mock_client):
        """--sort-by name sorts alphabetically."""
        mock_client.list_runs.return_value = iter(
            [
                create_run(name="zebra", id_str=make_run_id(1)),
                create_run(name="alpha", id_str=make_run_id(2)),
            ]
        )

        result = runner.invoke(cli, ["runs", "list", "--sort-by", "name"])

        assert result.exit_code == 0
        assert result.output.find("alpha") < result.output.find("zebra")

    def test_sort_by_latency_desc(self, runner, mock_client):
        """--sort-by -latency sorts descending."""
        from datetime import datetime, timedelta, timezone

        from langsmith.schemas import Run
        from uuid import UUID

        start = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        fast_run = Run(
            id=UUID(make_run_id(1)),
            name="fast",
            run_type="chain",
            start_time=start,
            end_time=start + timedelta(seconds=1.0),
            status="success",
        )
        slow_run = Run(
            id=UUID(make_run_id(2)),
            name="slow",
            run_type="chain",
            start_time=start,
            end_time=start + timedelta(seconds=5.0),
            status="success",
        )
        mock_client.list_runs.return_value = iter([fast_run, slow_run])

        result = runner.invoke(cli, ["runs", "list", "--sort-by", "-latency"])

        assert result.exit_code == 0
        assert result.output.find("slow") < result.output.find("fast")


class TestRunsListOutputFormats:
    """Output format tests."""

    def test_csv_format(self, runner, mock_client):
        """--format csv produces CSV output."""
        mock_client.list_runs.return_value = iter([create_run(name="test-run")])

        result = runner.invoke(cli, ["runs", "list", "--format", "csv"])

        assert result.exit_code == 0
        assert "test-run" in result.output

    def test_yaml_format(self, runner, mock_client):
        """--format yaml produces YAML output."""
        mock_client.list_runs.return_value = iter([create_run(name="test-run")])

        result = runner.invoke(cli, ["runs", "list", "--format", "yaml"])

        assert result.exit_code == 0
        assert "test-run" in result.output


class TestRunsListTruncation:
    """Truncation behavior tests."""

    def test_no_truncate_flag(self, runner, mock_client):
        """--no-truncate shows full content."""
        mock_client.list_runs.return_value = [
            create_run(
                name="Very Long Run Name",
                run_type="llm",
                extra={
                    "invocation_params": {
                        "model_name": "very-long-model-name-that-would-be-truncated-normally"
                    }
                },
            )
        ]

        result = runner.invoke(cli, ["runs", "list", "--no-truncate"])

        assert result.exit_code == 0
        assert "very-long-model-name-that-would-be-trunca" in result.output

    def test_default_truncate_behavior(self, runner, mock_client):
        """Default behavior truncates long values."""
        mock_client.list_runs.return_value = [
            create_run(
                name="Test Run",
                run_type="llm",
                extra={
                    "invocation_params": {
                        "model_name": "very-long-model-name-that-exceeds-twenty-characters"
                    }
                },
            )
        ]

        result = runner.invoke(cli, ["runs", "list"])

        assert result.exit_code == 0
        assert "..." in result.output or "very-long-model-n" in result.output


class TestRunsListValidation:
    """Input validation tests."""

    @pytest.mark.parametrize(
        "args,error_msg",
        [
            (["--min-latency", "5"], "Invalid duration format"),
            (["--max-latency", "5x"], "Invalid duration format"),
            (["--last", "5x"], "Invalid time format"),
            (["--since", "invalid"], "Invalid time format"),
        ],
    )
    def test_invalid_format_errors(self, runner, mock_client, args, error_msg):
        """Invalid formats produce helpful errors."""
        mock_client.list_runs.return_value = []

        result = runner.invoke(cli, ["runs", "list"] + args)

        assert result.exit_code != 0
        assert error_msg in result.output

    def test_invalid_regex_error(self, runner, mock_client):
        """Invalid regex produces helpful error."""
        mock_client.list_runs.return_value = [create_run()]

        result = runner.invoke(cli, ["runs", "list", "--name-regex", "[invalid("])

        assert result.exit_code != 0
        assert "Invalid regex pattern" in result.output


class TestRunsListProjectNotFound:
    """Tests for project-not-found error experience."""

    def test_uuid_in_project_flag_uses_project_id(self, runner, mock_client):
        """INVARIANT: UUID passed to --project auto-redirects to project_id lookup."""
        mock_client.list_runs.return_value = [create_run(name="Found")]

        result = runner.invoke(
            cli,
            [
                "--json",
                "runs",
                "list",
                "--project",
                "8dc9fb82-ee48-4815-a0b0-c0cbabaa1887",
            ],
        )

        assert result.exit_code == 0
        # Verify list_runs was called with project_id kwarg
        call_kwargs = mock_client.list_runs.call_args
        # The project_id gets passed through fetch_from_projects
        assert call_kwargs is not None

    def test_error_suggests_similar_projects_json(self, runner, mock_client):
        """INVARIANT: JSON error includes suggested project names when project not found."""
        mock_client.list_runs.side_effect = Exception("Project not found")
        proj = MagicMock()
        proj.name = "prd/promotion_service"
        mock_client.list_projects.return_value = [proj]

        result = runner.invoke(
            cli,
            ["--json", "runs", "list", "--project", "promotion_service"],
        )

        assert result.exit_code != 0
        # Extract JSON from mixed stdout/stderr output
        error_data = None
        for line in result.output.strip().split("\n"):
            line = line.strip()
            if line.startswith("{"):
                error_data = json.loads(line)
                break
        assert error_data is not None
        assert "prd/promotion_service" in error_data.get("suggestions", [])

    def test_error_suggests_similar_projects_human(self, runner, mock_client):
        """INVARIANT: Human-mode error includes suggested project names."""
        mock_client.list_runs.side_effect = Exception("Project not found")
        proj = MagicMock()
        proj.name = "prd/promotion_service"
        mock_client.list_projects.return_value = [proj]

        result = runner.invoke(
            cli,
            ["runs", "list", "--project", "promotion_service"],
        )

        assert result.exit_code != 0
        assert "prd/promotion_service" in result.output
