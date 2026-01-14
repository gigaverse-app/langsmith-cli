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
