import json
from typing import Any

import click

from langsmith_cli.main import cli


def _wrapped_langsmith_http_error(status_code: int, reason: str) -> Exception:
    """Build the SDK shape where LangSmithError wraps requests.HTTPError."""
    import requests
    from langsmith.utils import LangSmithError

    response = requests.Response()
    response.status_code = status_code
    response.reason = reason
    response.url = "https://api.smith.langchain.com/sessions"

    try:
        try:
            raise requests.HTTPError(
                f"{status_code} Client Error: {reason}", response=response
            )
        except requests.HTTPError as inner:
            raise LangSmithError(f"Failed to GET /sessions: {reason}") from inner
    except LangSmithError as wrapped:
        return wrapped


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


def test_help_mentions_json_can_appear_anywhere(runner):
    """Root help should not claim --json must be first."""
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "Pass --json anywhere" in result.output
    assert "Always pass --json FIRST" not in result.output


def test_http_status_extraction_from_httpx_error():
    """HTTP status extraction uses exception contracts, not message matching."""
    import httpx
    from langsmith_cli.main import _http_status_from_exception

    request = httpx.Request("GET", "https://api.smith.langchain.com/sessions")
    response = httpx.Response(403, request=request)
    error = httpx.HTTPStatusError("forbidden", request=request, response=response)

    assert _http_status_from_exception(error) == 403


def test_http_status_extraction_unknown_error():
    """Non-HTTP errors have no extracted status."""
    from langsmith_cli.main import _http_status_from_exception

    assert _http_status_from_exception(RuntimeError("boom")) is None


def test_json_mode_helper_defaults_false_without_obj_or_flag():
    """JSON-mode detection should be explicit and safe for early Click errors."""
    from langsmith_cli.main import _is_json_mode

    command = click.Command("cmd")
    no_obj_ctx = click.Context(command)
    assert _is_json_mode(no_obj_ctx) is False

    no_json_key_ctx = click.Context(command)
    no_json_key_ctx.obj = {}
    assert _is_json_mode(no_json_key_ctx) is False


def test_close_cached_client_noops_without_cached_client():
    """Cleanup should be harmless for commands that never create a client."""
    from langsmith_cli.main import _close_cached_client

    command = click.Command("cmd")
    no_obj_ctx = click.Context(command)
    _close_cached_client(no_obj_ctx)

    no_client_ctx = click.Context(command)
    no_client_ctx.obj = {}
    _close_cached_client(no_client_ctx)


def test_command_path_helpers_preserve_nested_subcommands():
    """Structured errors need the nested command path, not just the root command."""
    from langsmith_cli.main import (
        _command_path_for_ctx,
        _command_path_from_args,
        _command_path_from_exception,
    )

    root = click.Group("langsmith-cli")
    runs = click.Group("runs")
    get = click.Command("get")
    runs.add_command(get)
    root.add_command(runs)

    assert (
        _command_path_from_args(
            "langsmith-cli", root, ["--json", "runs", "get", "run-id"]
        )
        == "langsmith-cli runs get"
    )
    assert (
        _command_path_from_args("langsmith-cli", root, ["runs", "unknown"])
        == "langsmith-cli runs"
    )

    root_ctx = click.Context(root, info_name="langsmith-cli")
    runs_ctx = click.Context(runs, info_name="runs", parent=root_ctx)
    get_ctx = click.Context(get, info_name="get", parent=runs_ctx)
    assert _command_path_for_ctx(get_ctx) == "langsmith-cli runs get"

    def raise_with_click_context() -> None:
        ctx = get_ctx
        if ctx.info_name == "":
            raise AssertionError("unreachable")
        raise RuntimeError("boom")

    try:
        raise_with_click_context()
    except RuntimeError as exc:
        assert _command_path_from_exception(exc) == "langsmith-cli runs get"


def test_cached_client_is_closed_after_invocation(runner, mock_client):
    """LangSmith client resources are closed after command invocation when supported."""
    from conftest import create_project

    mock_client.list_projects.return_value = iter([create_project("test-project")])

    result = runner.invoke(cli, ["projects", "list"])

    assert result.exit_code == 0
    mock_client.close.assert_called_once()


class TestJsonFlagPlacement:
    """INVARIANT: --json produces JSON output regardless of where it appears in the command."""

    def test_json_flag_before_subcommand(self, runner, mock_client):
        """--json before subcommand (canonical position) produces JSON output."""
        from conftest import create_project

        mock_client.list_projects.return_value = iter([create_project("test-project")])

        result = runner.invoke(cli, ["--json", "projects", "list"])

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, list)
        assert data[0]["name"] == "test-project"

    def test_json_flag_after_subcommand(self, runner, mock_client):
        """--json after subcommand (intuitive but previously broken) produces JSON output."""
        from conftest import create_project

        mock_client.list_projects.return_value = iter([create_project("test-project")])

        result = runner.invoke(cli, ["projects", "list", "--json"])

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, list)
        assert data[0]["name"] == "test-project"

    def test_json_flag_between_subcommands(self, runner, mock_client):
        """--json between command group and subcommand also works."""
        from conftest import create_project

        mock_client.list_projects.return_value = iter([create_project("test-project")])

        result = runner.invoke(cli, ["projects", "--json", "list"])

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, list)
        assert data[0]["name"] == "test-project"


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

    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        mock_client.list_projects.side_effect = _wrapped_langsmith_http_error(
            403, "Forbidden"
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
    import json

    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        mock_client.list_projects.side_effect = _wrapped_langsmith_http_error(
            403, "Forbidden"
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
        # INVARIANT: structured errors carry the invoked command path so
        # callers can correlate the failure with the call that produced it.
        assert error_data["command"].endswith("runs get")


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


def test_global_conflict_handler_emits_structured_json(runner):
    """Unhandled SDK conflicts should still produce sparse structured JSON."""
    from langsmith.utils import LangSmithConflictError
    from langsmith_cli.main import LangSmithCLIGroup

    @click.group(cls=LangSmithCLIGroup)
    @click.option("--json", "json_mode", is_flag=True)
    @click.pass_context
    def command_group(ctx, json_mode):
        ctx.ensure_object(dict)
        ctx.obj["json"] = json_mode

    @command_group.command("conflict")
    def conflict_command():
        raise LangSmithConflictError("resource already exists")

    result = runner.invoke(command_group, ["--json", "conflict"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload == {
        "error": "ConflictError",
        "message": "resource already exists",
        "command": "command conflict",
    }


def test_unauthorized_error_handling(runner):
    """Test that 401 Unauthorized errors show helpful message."""
    from unittest.mock import patch

    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        mock_client.list_projects.side_effect = _wrapped_langsmith_http_error(
            401, "Unauthorized"
        )

        result = runner.invoke(cli, ["projects", "list"])

        assert result.exit_code != 0
        assert "Authentication failed" in result.output
        assert "langsmith-cli auth login" in result.output
        assert "Traceback" not in result.output


def test_unauthorized_error_handling_json_mode(runner):
    """Test that 401 Unauthorized errors in JSON mode return structured error."""
    from unittest.mock import patch
    import json

    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        mock_client.list_projects.side_effect = _wrapped_langsmith_http_error(
            401, "Unauthorized"
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
        from langsmith.utils import LangSmithError

        mock_client.list_runs.side_effect = LangSmithError("Project not found")
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
        from langsmith.utils import LangSmithError

        mock_client.list_runs.side_effect = LangSmithError("Project not found")
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
        from langsmith.utils import LangSmithError

        mock_client.list_runs.side_effect = LangSmithError("SDK error message")
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
        from langsmith.utils import LangSmithError

        mock_client.list_runs.side_effect = LangSmithError("Project not found")
        mock_client.list_projects.return_value = []

        result = runner.invoke(cli, ["runs", "list", "--project", "nonexistent"])

        assert result.exit_code != 0
        assert "Failed to fetch" in result.output
