import os
from unittest.mock import patch
from langsmith_cli.main import cli


def test_login_command_success(runner, tmp_path):
    """Test successful login writes to .env file."""
    # Use a temporary directory as the CWD to avoid messing with actual .env
    with runner.isolated_filesystem(), patch("webbrowser.open") as mock_open:
        # Input the key when prompted
        result = runner.invoke(cli, ["auth", "login"], input="lsv2_test_key\n")

        assert mock_open.called
        assert result.exit_code == 0
        assert "Successfully logged in" in result.output

        # Verify .env was created
        assert os.path.exists(".env")
        with open(".env", "r") as f:
            content = f.read()
            assert "LANGSMITH_API_KEY=lsv2_test_key" in content


def test_login_command_overwrite(runner):
    """Test login prompts for overwrite if .env exists."""
    with runner.isolated_filesystem(), patch("webbrowser.open"):
        # Create existing .env
        with open(".env", "w") as f:
            f.write("LANGSMITH_API_KEY=old_key")

        # Run login, verify prompt (y to overwrite)
        result = runner.invoke(cli, ["auth", "login"], input="lsv2_new_key\ny\n")

        assert result.exit_code == 0
        with open(".env", "r") as f:
            content = f.read()
            assert "LANGSMITH_API_KEY=lsv2_new_key" in content


def test_login_command_cancel_overwrite(runner):
    """Test login cancel overwrite."""
    with runner.isolated_filesystem(), patch("webbrowser.open"):
        # Create existing .env
        with open(".env", "w") as f:
            f.write("LANGSMITH_API_KEY=old_key")

        # Run login, verify prompt (n to cancel)
        result = runner.invoke(cli, ["auth", "login"], input="lsv2_new_key\nn\n")

        assert result.exit_code == 0
        assert "Aborted" in result.output

        with open(".env", "r") as f:
            content = f.read()
            assert "LANGSMITH_API_KEY=old_key" in content
