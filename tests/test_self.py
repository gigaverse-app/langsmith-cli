"""Tests for self detect command."""

import json
import sys
from unittest.mock import MagicMock, patch

from langsmith_cli.main import cli


class TestDetectInstallation:
    """Unit tests for detect_installation() pure function."""

    def test_editable_install_detected(self):
        """INVARIANT: When direct_url.json has editable=true, install method is 'development (editable)'."""
        from langsmith_cli.commands.self_cmd import detect_installation

        mock_dist = MagicMock()
        mock_dist.metadata = {"Version": "0.3.0"}
        mock_dist._path = MagicMock()
        mock_dist._path.parent = "/some/site-packages"
        mock_dist.read_text.return_value = json.dumps(
            {"url": "file:///home/user/project", "dir_info": {"editable": True}}
        )

        with (
            patch("importlib.metadata.distribution", return_value=mock_dist),
            patch("shutil.which", return_value="/usr/bin/langsmith-cli"),
        ):
            result = detect_installation()

        assert result["install_method"] == "development (editable)"
        assert result["version"] == "0.3.0"

    def test_uv_tool_install_detected(self):
        """INVARIANT: When sys.prefix contains '/uv/tools/', install method is 'uv tool'."""
        from langsmith_cli.commands.self_cmd import detect_installation

        mock_dist = MagicMock()
        mock_dist.metadata = {"Version": "0.3.0"}
        mock_dist._path = MagicMock()
        mock_dist._path.parent = "/home/user/.local/share/uv/tools/langsmith-cli/lib"
        mock_dist.read_text.return_value = None  # No direct_url.json

        with (
            patch("importlib.metadata.distribution", return_value=mock_dist),
            patch("shutil.which", return_value="/home/user/.local/bin/langsmith-cli"),
            patch.object(
                sys,
                "prefix",
                "/home/user/.local/share/uv/tools/langsmith-cli",
            ),
        ):
            result = detect_installation()

        assert result["install_method"] == "uv tool"

    def test_pipx_install_detected(self):
        """INVARIANT: When sys.prefix contains '/pipx/', install method is 'pipx'."""
        from langsmith_cli.commands.self_cmd import detect_installation

        mock_dist = MagicMock()
        mock_dist.metadata = {"Version": "0.3.0"}
        mock_dist._path = MagicMock()
        mock_dist._path.parent = "/home/user/.local/pipx/venvs/langsmith-cli/lib"
        mock_dist.read_text.return_value = None

        with (
            patch("importlib.metadata.distribution", return_value=mock_dist),
            patch("shutil.which", return_value="/home/user/.local/bin/langsmith-cli"),
            patch.object(sys, "prefix", "/home/user/.local/pipx/venvs/langsmith-cli"),
        ):
            result = detect_installation()

        assert result["install_method"] == "pipx"

    def test_pip_virtualenv_detected(self):
        """INVARIANT: When in a venv with no other markers, install method is 'pip (virtualenv)'."""
        from langsmith_cli.commands.self_cmd import detect_installation

        mock_dist = MagicMock()
        mock_dist.metadata = {"Version": "0.3.0"}
        mock_dist._path = MagicMock()
        mock_dist._path.parent = (
            "/home/user/myproject/.venv/lib/python3.12/site-packages"
        )
        mock_dist.read_text.return_value = None

        with (
            patch("importlib.metadata.distribution", return_value=mock_dist),
            patch(
                "shutil.which",
                return_value="/home/user/myproject/.venv/bin/langsmith-cli",
            ),
            patch.object(sys, "prefix", "/home/user/myproject/.venv"),
            patch.object(sys, "base_prefix", "/usr"),
        ):
            result = detect_installation()

        assert result["install_method"] == "pip (virtualenv)"

    def test_pip_system_detected(self):
        """INVARIANT: When sys.prefix == sys.base_prefix with no markers, install method is 'pip (system)'."""
        from langsmith_cli.commands.self_cmd import detect_installation

        mock_dist = MagicMock()
        mock_dist.metadata = {"Version": "0.3.0"}
        mock_dist._path = MagicMock()
        mock_dist._path.parent = "/usr/lib/python3.12/site-packages"
        mock_dist.read_text.return_value = None

        with (
            patch("importlib.metadata.distribution", return_value=mock_dist),
            patch("shutil.which", return_value="/usr/bin/langsmith-cli"),
            patch.object(sys, "prefix", "/usr"),
            patch.object(sys, "base_prefix", "/usr"),
        ):
            result = detect_installation()

        assert result["install_method"] == "pip (system)"

    def test_package_not_found_handled_gracefully(self):
        """INVARIANT: When package metadata is missing, version is 'unknown' and method is 'source (not installed)'."""
        import importlib.metadata

        from langsmith_cli.commands.self_cmd import detect_installation

        with (
            patch(
                "importlib.metadata.distribution",
                side_effect=importlib.metadata.PackageNotFoundError("langsmith-cli"),
            ),
            patch("shutil.which", return_value=None),
        ):
            result = detect_installation()

        assert result["version"] == "unknown"
        assert result["install_method"] == "source (not installed)"
        assert result["install_path"] == "unknown"

    def test_executable_not_in_path(self):
        """INVARIANT: When shutil.which returns None, executable_path is 'not found in PATH'."""
        from langsmith_cli.commands.self_cmd import detect_installation

        mock_dist = MagicMock()
        mock_dist.metadata = {"Version": "0.3.0"}
        mock_dist._path = MagicMock()
        mock_dist._path.parent = "/some/path"
        mock_dist.read_text.return_value = None

        with (
            patch("importlib.metadata.distribution", return_value=mock_dist),
            patch("shutil.which", return_value=None),
        ):
            result = detect_installation()

        assert result["executable_path"] == "not found in PATH"

    def test_python_details_populated(self):
        """INVARIANT: python_path and python_version are always populated from sys and platform."""
        import platform

        from langsmith_cli.commands.self_cmd import detect_installation

        mock_dist = MagicMock()
        mock_dist.metadata = {"Version": "1.0.0"}
        mock_dist._path = MagicMock()
        mock_dist._path.parent = "/some/path"
        mock_dist.read_text.return_value = None

        with (
            patch("importlib.metadata.distribution", return_value=mock_dist),
            patch("shutil.which", return_value="/bin/langsmith-cli"),
        ):
            result = detect_installation()

        assert result["python_path"] == sys.executable
        assert result["python_version"] == platform.python_version()

    def test_local_non_editable_detected(self):
        """INVARIANT: When direct_url.json has file:// URL but no editable flag, method is 'local (non-editable)'."""
        from langsmith_cli.commands.self_cmd import detect_installation

        mock_dist = MagicMock()
        mock_dist.metadata = {"Version": "0.3.0"}
        mock_dist._path = MagicMock()
        mock_dist._path.parent = "/some/path"
        mock_dist.read_text.return_value = json.dumps(
            {"url": "file:///tmp/langsmith-cli-0.3.0.tar.gz", "dir_info": {}}
        )

        with (
            patch("importlib.metadata.distribution", return_value=mock_dist),
            patch("shutil.which", return_value="/bin/langsmith-cli"),
        ):
            result = detect_installation()

        assert result["install_method"] == "local (non-editable)"

    def test_malformed_direct_url_falls_through(self):
        """INVARIANT: When direct_url.json is malformed, detection falls through to path heuristics."""
        from langsmith_cli.commands.self_cmd import detect_installation

        mock_dist = MagicMock()
        mock_dist.metadata = {"Version": "0.3.0"}
        mock_dist._path = MagicMock()
        mock_dist._path.parent = "/some/path"
        mock_dist.read_text.return_value = "not valid json"

        with (
            patch("importlib.metadata.distribution", return_value=mock_dist),
            patch("shutil.which", return_value="/bin/langsmith-cli"),
            patch.object(sys, "prefix", "/usr"),
            patch.object(sys, "base_prefix", "/usr"),
        ):
            result = detect_installation()

        # Should fall through to pip (system) since prefix == base_prefix
        assert result["install_method"] == "pip (system)"


class TestSelfDetectCLI:
    """CLI integration tests for self detect command."""

    def test_self_detect_table_output(self, runner):
        """INVARIANT: 'self detect' shows installation details as a table."""
        result = runner.invoke(cli, ["self", "detect"])

        assert result.exit_code == 0
        assert "Version" in result.output
        assert "Install method" in result.output
        assert "Python" in result.output

    def test_self_detect_json_output(self, runner):
        """INVARIANT: '--json self detect' returns valid JSON with all required keys."""
        result = runner.invoke(cli, ["--json", "self", "detect"])

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, dict)
        expected_keys = {
            "version",
            "install_method",
            "install_path",
            "executable_path",
            "python_path",
            "python_version",
        }
        assert expected_keys == set(data.keys())

    def test_self_detect_json_version_not_empty(self, runner):
        """INVARIANT: JSON output version field is a non-empty string."""
        result = runner.invoke(cli, ["--json", "self", "detect"])

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data["version"], str)
        assert len(data["version"]) > 0

    def test_self_group_help(self, runner):
        """INVARIANT: 'self --help' shows available subcommands including 'detect'."""
        result = runner.invoke(cli, ["self", "--help"])

        assert result.exit_code == 0
        assert "detect" in result.output

    def test_self_detect_in_dev_environment(self, runner):
        """INVARIANT: In the dev environment with editable install, detect reports 'development (editable)'."""
        result = runner.invoke(cli, ["--json", "self", "detect"])

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["install_method"] == "development (editable)"
