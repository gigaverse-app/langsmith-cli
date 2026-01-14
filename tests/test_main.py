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
