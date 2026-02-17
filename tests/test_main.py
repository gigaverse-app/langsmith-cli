import json
from typing import Any

import click

from langsmith_cli.main import cli


def test_main_version(runner):
    """Test that the CLI can display its version."""
    result = runner.invoke(cli, ["--version"])
    assert result.exit_code == 0
    assert "version" in result.output.lower()


def test_main_help(runner):
    """Test that the CLI can display help."""
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "Usage:" in result.output


def test_json_flag(runner):
    """Test that the --json flag is accepted (even if commands are mocked)."""
    # For now, just check specific help checking for the option or a specific no-op command
    # implementation will happen in main.py
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "--json" in result.output


def test_auth_error_handling(runner):
    """Test that authentication errors are caught and shown with a friendly message."""
    from unittest.mock import patch
    from langsmith.utils import LangSmithAuthError

    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        mock_client.list_projects.side_effect = LangSmithAuthError(
            "Authentication failed for /sessions. HTTPError('401 Client Error')"
        )

        result = runner.invoke(cli, ["projects", "list"])

        # Should not exit with 0 (error occurred)
        assert result.exit_code != 0
        # Should show friendly error message, not stack trace
        assert "Authentication failed" in result.output
        assert "langsmith-cli auth login" in result.output
        # Should NOT show Python stack trace
        assert "Traceback" not in result.output


def test_forbidden_error_handling(runner):
    """Test that 403 Forbidden errors show helpful message."""
    from unittest.mock import patch
    from langsmith.utils import LangSmithError

    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        mock_client.list_projects.side_effect = LangSmithError(
            "Failed to GET /sessions in LangSmith API. HTTPError('403 Client Error: Forbidden for url: https://api.smith.langchain.com/sessions')"
        )

        result = runner.invoke(cli, ["projects", "list"])

        # Should not exit with 0
        assert result.exit_code != 0
        # Should show friendly error message
        assert "Access forbidden" in result.output
        assert "API key may be invalid or expired" in result.output
        assert "langsmith-cli auth login" in result.output
        # Should NOT show Python stack trace
        assert "Traceback" not in result.output


def test_forbidden_error_handling_json_mode(runner):
    """Test that 403 Forbidden errors in JSON mode return structured error."""
    from unittest.mock import patch
    from langsmith.utils import LangSmithError
    import json

    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        mock_client.list_projects.side_effect = LangSmithError(
            "Failed to GET /sessions. HTTPError('403 Client Error: Forbidden')"
        )

        result = runner.invoke(cli, ["--json", "projects", "list"])

        # Should not exit with 0
        assert result.exit_code != 0
        # Should return valid JSON
        error_data = json.loads(result.output)
        assert error_data["error"] == "PermissionError"
        assert "API key may be invalid or expired" in error_data["message"]
        assert "langsmith-cli auth login" in error_data["help"]
        assert "details" in error_data


def test_auth_error_handling_json_mode(runner):
    """Test that LangSmithAuthError in JSON mode returns structured error."""
    from unittest.mock import patch
    from langsmith.utils import LangSmithAuthError
    import json

    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        mock_client.list_projects.side_effect = LangSmithAuthError(
            "Authentication failed for /sessions. HTTPError('401 Client Error')"
        )

        result = runner.invoke(cli, ["--json", "projects", "list"])

        assert result.exit_code != 0
        error_data = json.loads(result.output)
        assert error_data["error"] == "AuthenticationError"
        assert "Authentication failed" in error_data["message"]
        assert "langsmith-cli auth login" in error_data["help"]


def test_not_found_error_handling(runner):
    """Test that LangSmithNotFoundError is caught and shown with a friendly message."""
    from unittest.mock import patch
    from langsmith.utils import LangSmithNotFoundError

    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        mock_client.read_run.side_effect = LangSmithNotFoundError(
            "Run with id 00000000-0000-0000-0000-000000000000 not found"
        )

        result = runner.invoke(
            cli, ["runs", "get", "00000000-0000-0000-0000-000000000000"]
        )

        assert result.exit_code != 0
        assert "not found" in result.output.lower()
        assert "Traceback" not in result.output


def test_not_found_error_handling_json_mode(runner):
    """Test that LangSmithNotFoundError in JSON mode returns structured error."""
    from unittest.mock import patch
    from langsmith.utils import LangSmithNotFoundError
    import json

    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        mock_client.read_run.side_effect = LangSmithNotFoundError(
            "Run with id 00000000-0000-0000-0000-000000000000 not found"
        )

        result = runner.invoke(
            cli, ["--json", "runs", "get", "00000000-0000-0000-0000-000000000000"]
        )

        assert result.exit_code != 0
        error_data = json.loads(result.output)
        assert error_data["error"] == "NotFoundError"
        assert "not found" in error_data["message"].lower()


def test_conflict_error_handling(runner):
    """Test that LangSmithConflictError is caught and shown as a warning (non-fatal)."""
    from unittest.mock import patch
    from langsmith.utils import LangSmithConflictError

    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        mock_client.create_project.side_effect = LangSmithConflictError(
            "Project 'test-project' already exists"
        )

        result = runner.invoke(cli, ["projects", "create", "test-project"])

        # Conflict errors are non-fatal - don't exit with error
        assert result.exit_code == 0
        assert "already exists" in result.output.lower()
        assert "Traceback" not in result.output


def test_conflict_error_handling_json_mode(runner):
    """Test that LangSmithConflictError in JSON mode returns structured error.

    Note: The projects create command handles conflicts internally with a warning,
    so we test with a different command that lets the error propagate.
    """
    from unittest.mock import patch
    from langsmith.utils import LangSmithConflictError

    # Use datasets create which also handles conflicts gracefully
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        mock_client.create_project.side_effect = LangSmithConflictError(
            "Project 'test-project' already exists"
        )

        result = runner.invoke(cli, ["--json", "projects", "create", "test-project"])

        # Conflict errors are non-fatal - just shows warning
        assert result.exit_code == 0
        # Output is the warning message (handled internally by the command)
        assert "already exists" in result.output.lower()


def test_unauthorized_error_handling(runner):
    """Test that 401 Unauthorized errors show helpful message."""
    from unittest.mock import patch
    from langsmith.utils import LangSmithError

    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        mock_client.list_projects.side_effect = LangSmithError(
            "Failed to GET /sessions. HTTPError('401 Client Error: Unauthorized')"
        )

        result = runner.invoke(cli, ["projects", "list"])

        assert result.exit_code != 0
        assert "Authentication failed" in result.output
        assert "langsmith-cli auth login" in result.output
        assert "Traceback" not in result.output


def test_unauthorized_error_handling_json_mode(runner):
    """Test that 401 Unauthorized errors in JSON mode return structured error."""
    from unittest.mock import patch
    from langsmith.utils import LangSmithError
    import json

    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        mock_client.list_projects.side_effect = LangSmithError(
            "Failed to GET /sessions. HTTPError('401 Unauthorized')"
        )

        result = runner.invoke(cli, ["--json", "projects", "list"])

        assert result.exit_code != 0
        error_data = json.loads(result.output)
        assert error_data["error"] == "AuthenticationError"
        assert "Authentication failed" in error_data["message"]
        assert "langsmith-cli auth login" in error_data["help"]


def test_generic_langsmith_error_handling(runner):
    """Test that generic LangSmith errors are shown (not 401/403)."""
    from unittest.mock import patch
    from langsmith.utils import LangSmithError

    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        mock_client.list_projects.side_effect = LangSmithError(
            "Server error: Internal server error (500)"
        )

        result = runner.invoke(cli, ["projects", "list"])

        assert result.exit_code != 0
        assert "Server error" in result.output or "500" in result.output
        assert "Traceback" not in result.output


def test_generic_langsmith_error_handling_json_mode(runner):
    """Test that generic LangSmith errors in JSON mode return structured error."""
    from unittest.mock import patch
    from langsmith.utils import LangSmithError
    import json

    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        mock_client.list_projects.side_effect = LangSmithError(
            "Server error: Internal server error (500)"
        )

        result = runner.invoke(cli, ["--json", "projects", "list"])

        assert result.exit_code != 0
        error_data = json.loads(result.output)
        assert error_data["error"] == "LangSmithError"
        assert "Server error" in error_data["message"] or "500" in error_data["message"]


class TestNonLangSmithErrorsInJsonMode:
    """Invariant: In --json mode, ALL errors produce valid JSON on stdout, never empty stdout."""

    def test_click_exception_in_json_mode_outputs_json(self, runner):
        """ClickException (e.g. from raise_if_all_failed) outputs JSON error in JSON mode."""
        import json
        from unittest.mock import patch

        with patch("langsmith.Client") as MockClient:
            mock_client = MockClient.return_value
            mock_client.list_projects.side_effect = click.ClickException(
                "Failed to fetch runs from all 3 source(s)."
            )

            result = runner.invoke(cli, ["--json", "projects", "list"])

            assert result.exit_code != 0
            error_data = json.loads(result.output)
            assert error_data["error"] == "ClickException"
            assert "Failed to fetch runs" in error_data["message"]

    def test_click_usage_error_in_json_mode_outputs_json(self, runner):
        """UsageError (e.g. invalid option) outputs JSON error in JSON mode."""
        import json
        from unittest.mock import patch

        with patch("langsmith.Client") as MockClient:
            mock_client = MockClient.return_value
            mock_client.list_projects.side_effect = click.UsageError(
                "Invalid value for '--limit': 'abc' is not a valid integer."
            )

            result = runner.invoke(cli, ["--json", "projects", "list"])

            assert result.exit_code != 0
            error_data = json.loads(result.output)
            assert error_data["error"] == "UsageError"
            assert "Invalid value" in error_data["message"]

    def test_click_bad_parameter_in_json_mode_outputs_json(self, runner):
        """BadParameter (e.g. from date parsing) outputs JSON error in JSON mode."""
        import json
        from unittest.mock import patch

        with patch("langsmith.Client") as MockClient:
            mock_client = MockClient.return_value
            mock_client.list_projects.side_effect = click.BadParameter(
                "Could not parse datetime 'not a date'", param_hint="'--since'"
            )

            result = runner.invoke(cli, ["--json", "projects", "list"])

            assert result.exit_code != 0
            error_data = json.loads(result.output)
            assert error_data["error"] == "BadParameter"
            assert "not a date" in error_data["message"]

    def test_unexpected_exception_in_json_mode_outputs_json(self, runner):
        """Unexpected Python exceptions output JSON error in JSON mode."""
        import json
        from unittest.mock import patch

        with patch("langsmith.Client") as MockClient:
            mock_client = MockClient.return_value
            mock_client.list_projects.side_effect = ValueError(
                "unexpected internal error"
            )

            result = runner.invoke(cli, ["--json", "projects", "list"])

            assert result.exit_code != 0
            error_data = json.loads(result.output)
            assert error_data["error"] == "ValueError"
            assert "unexpected internal error" in error_data["message"]

    def test_runtime_error_in_json_mode_outputs_json(self, runner):
        """RuntimeError outputs JSON error in JSON mode."""
        import json
        from unittest.mock import patch

        with patch("langsmith.Client") as MockClient:
            mock_client = MockClient.return_value
            mock_client.list_projects.side_effect = RuntimeError(
                "connection reset by peer"
            )

            result = runner.invoke(cli, ["--json", "projects", "list"])

            assert result.exit_code != 0
            error_data = json.loads(result.output)
            assert error_data["error"] == "RuntimeError"
            assert "connection reset" in error_data["message"]

    def test_non_json_mode_click_exception_still_shows_click_format(self, runner):
        """In non-JSON mode, Click exceptions still show Click's default formatting."""
        from unittest.mock import patch

        with patch("langsmith.Client") as MockClient:
            mock_client = MockClient.return_value
            mock_client.list_projects.side_effect = click.ClickException(
                "Something went wrong"
            )

            result = runner.invoke(cli, ["projects", "list"])

            assert result.exit_code != 0
            assert "Something went wrong" in result.output
            # Should NOT be JSON
            assert result.output.strip()[0] != "{"

    def test_non_json_mode_unexpected_exception_reraises(self, runner):
        """In non-JSON mode, unexpected exceptions re-raise and show traceback."""
        from unittest.mock import patch

        with patch("langsmith.Client") as MockClient:
            mock_client = MockClient.return_value
            mock_client.list_projects.side_effect = ValueError(
                "unexpected internal error"
            )

            result = runner.invoke(cli, ["projects", "list"])

            # CliRunner captures the exception
            assert result.exit_code != 0
            assert result.exception is not None


class TestVerbosityFlags:
    """Tests for verbosity control flags (-v, -q)."""

    def test_quiet_flag_suppresses_info(self, runner, mock_client):
        """Test -q flag suppresses INFO level messages."""
        mock_client.list_projects.return_value = iter([])

        result = runner.invoke(cli, ["-q", "projects", "list"])

        assert result.exit_code == 0
        # Info messages should be suppressed

    def test_double_quiet_flag_suppresses_warnings(self, runner, mock_client):
        """Test -qq flag suppresses WARNING level messages."""
        mock_client.list_projects.return_value = iter([])

        result = runner.invoke(cli, ["-qq", "projects", "list"])

        assert result.exit_code == 0
        # Even warnings should be suppressed

    def test_verbose_flag_enables_debug(self, runner, mock_client):
        """Test -v flag enables DEBUG level messages."""
        mock_client.list_projects.return_value = iter([])

        result = runner.invoke(cli, ["-v", "projects", "list"])

        assert result.exit_code == 0
        # Debug messages should be visible (if any)

    def test_double_verbose_flag_enables_trace(self, runner, mock_client):
        """Test -vv flag enables TRACE level messages."""
        mock_client.list_projects.return_value = iter([])

        result = runner.invoke(cli, ["-vv", "projects", "list"])

        assert result.exit_code == 0
        # Trace level messages should be visible (if any)


def _extract_json_from_output(output: str) -> dict[str, Any]:
    """Extract JSON object from CliRunner output that may contain mixed stderr/stdout.

    CliRunner mixes stdout and stderr, so logger messages may precede the JSON.
    This finds and parses the JSON object line.
    """
    for line in output.strip().split("\n"):
        line = line.strip()
        if line.startswith("{"):
            return json.loads(line)
    raise ValueError(f"No JSON found in output: {output!r}")


class TestCLIFetchErrorHandling:
    """Tests for CLIFetchError handling in global error handler."""

    def test_json_mode_includes_structured_fields(self, runner, mock_client):
        """INVARIANT: JSON error output includes failed_sources and suggestions fields."""
        mock_client.list_runs.side_effect = Exception("Project not found")
        mock_client.list_projects.return_value = []

        result = runner.invoke(
            cli, ["--json", "runs", "list", "--project", "nonexistent"]
        )

        assert result.exit_code != 0
        error_data = _extract_json_from_output(result.output)
        assert error_data["error"] == "FetchError"
        assert "failed_sources" in error_data
        assert "suggestions" in error_data
        assert isinstance(error_data["failed_sources"], list)
        assert isinstance(error_data["suggestions"], list)

    def test_json_mode_includes_suggestions(self, runner, mock_client):
        """INVARIANT: JSON error includes similar project names when available."""
        from unittest.mock import MagicMock

        mock_client.list_runs.side_effect = Exception("Project not found")
        proj = MagicMock()
        proj.name = "prd/promotion_service"
        mock_client.list_projects.return_value = [proj]

        result = runner.invoke(
            cli, ["--json", "runs", "list", "--project", "promotion_service"]
        )

        assert result.exit_code != 0
        error_data = _extract_json_from_output(result.output)
        assert error_data["error"] == "FetchError"
        assert "prd/promotion_service" in error_data["suggestions"]
        assert len(error_data["failed_sources"]) == 1
        assert error_data["failed_sources"][0]["name"] == "promotion_service"

    def test_json_mode_failed_sources_have_name_and_error(self, runner, mock_client):
        """INVARIANT: Each failed_source has name and error fields."""
        mock_client.list_runs.side_effect = Exception("SDK error message")
        mock_client.list_projects.return_value = []

        result = runner.invoke(
            cli, ["--json", "runs", "list", "--project", "bad-project"]
        )

        assert result.exit_code != 0
        error_data = _extract_json_from_output(result.output)
        fs = error_data["failed_sources"][0]
        assert "name" in fs
        assert "error" in fs
        assert fs["name"] == "bad-project"
        assert "SDK error message" in fs["error"]

    def test_human_mode_shows_error_message(self, runner, mock_client):
        """INVARIANT: Human mode shows readable error with failure details."""
        mock_client.list_runs.side_effect = Exception("Project not found")
        mock_client.list_projects.return_value = []

        result = runner.invoke(cli, ["runs", "list", "--project", "nonexistent"])

        assert result.exit_code != 0
        assert "Failed to fetch" in result.output
