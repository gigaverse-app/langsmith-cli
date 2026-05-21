"""Tests for self detect and self update commands."""

import json
import sys
import urllib.error
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


class TestGetUpdateCommand:
    """Unit tests for get_update_command() pure function."""

    def test_uv_tool_returns_uv_upgrade(self):
        """INVARIANT: uv tool installs use 'uv tool upgrade --reinstall langsmith-cli'.

        `--reinstall` is required to bypass uv's simple-index cache, which was
        the root cause behind #119: without it, uv can resolve against a stale
        view of PyPI for minutes after a new release and silently no-op exit 0.
        `--reinstall` implies `--refresh` (per `uv tool upgrade --help`). The
        bare `--refresh` flag is not a valid argument to `uv tool upgrade` in
        uv 0.9.x — using it causes uv to error with 'unexpected argument'.
        """
        from langsmith_cli.commands.self_cmd import get_update_command

        assert (
            get_update_command("uv tool") == "uv tool upgrade --reinstall langsmith-cli"
        )

    def test_uv_tool_upgrade_actually_accepts_the_flag(self):
        """REGRESSION FROM v0.10.1: the previously-shipped `--refresh` flag is
        rejected by `uv tool upgrade` ("unexpected argument"). This test invokes
        the real `uv` binary with `--help` and asserts that the flag we use
        appears in the supported-flags list. Skips silently if uv isn't installed."""
        import shutil
        import subprocess

        uv = shutil.which("uv")
        if uv is None:
            import pytest

            pytest.skip("uv not on PATH")

        help_output = subprocess.run(
            [uv, "tool", "upgrade", "--help"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert help_output.returncode == 0
        assert "--reinstall" in help_output.stdout, (
            "uv tool upgrade no longer supports --reinstall; revisit "
            "_UPDATE_COMMANDS['uv tool'] in self_cmd.py"
        )

    def test_pipx_returns_pipx_upgrade(self):
        """INVARIANT: pipx installs use 'pipx upgrade langsmith-cli'."""
        from langsmith_cli.commands.self_cmd import get_update_command

        assert get_update_command("pipx") == "pipx upgrade langsmith-cli"

    def test_pip_virtualenv_returns_pip_install(self):
        """INVARIANT: pip virtualenv installs use 'pip install --upgrade langsmith-cli'."""
        from langsmith_cli.commands.self_cmd import get_update_command

        assert (
            get_update_command("pip (virtualenv)")
            == "pip install --upgrade langsmith-cli"
        )

    def test_pip_system_returns_pip_install(self):
        """INVARIANT: pip system installs use 'pip install --upgrade langsmith-cli'."""
        from langsmith_cli.commands.self_cmd import get_update_command

        assert (
            get_update_command("pip (system)") == "pip install --upgrade langsmith-cli"
        )

    def test_editable_returns_none(self):
        """INVARIANT: Editable installs return None (user should update manually)."""
        from langsmith_cli.commands.self_cmd import get_update_command

        assert get_update_command("development (editable)") is None

    def test_unknown_returns_none(self):
        """INVARIANT: Unknown install methods return None."""
        from langsmith_cli.commands.self_cmd import get_update_command

        assert get_update_command("source (not installed)") is None


class TestCheckLatestVersion:
    """Unit tests for check_latest_version()."""

    def test_returns_latest_from_pypi(self):
        """INVARIANT: Returns the latest version string from PyPI JSON API."""
        from langsmith_cli.commands.self_cmd import check_latest_version

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.read.return_value = json.dumps(
            {"info": {"version": "1.2.3"}}
        ).encode()
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_response):
            assert check_latest_version() == "1.2.3"

    def test_returns_none_on_network_error(self):
        """INVARIANT: Returns None when PyPI is unreachable."""
        from langsmith_cli.commands.self_cmd import check_latest_version

        with patch(
            "urllib.request.urlopen", side_effect=urllib.error.URLError("timeout")
        ):
            assert check_latest_version() is None


class TestSelfUpdateCLI:
    """CLI integration tests for self update command."""

    def test_update_shows_in_help(self, runner):
        """INVARIANT: 'self --help' lists the update subcommand."""
        result = runner.invoke(cli, ["self", "--help"])

        assert result.exit_code == 0
        assert "update" in result.output

    def test_update_editable_shows_manual_instructions(self, runner):
        """INVARIANT: In dev/editable mode, update tells user to update manually."""
        # We're in an editable install in the test env
        with patch(
            "langsmith_cli.commands.self_cmd.check_latest_version",
            return_value="99.0.0",
        ):
            result = runner.invoke(cli, ["self", "update"])

        assert result.exit_code == 0
        assert (
            "development" in result.output.lower()
            or "editable" in result.output.lower()
        )
        assert "git pull" in result.output or "manually" in result.output.lower()

    def test_update_already_up_to_date(self, runner):
        """INVARIANT: When current version matches latest, reports up to date."""
        from langsmith_cli.commands.self_cmd import detect_installation

        current = detect_installation()["version"]

        with patch(
            "langsmith_cli.commands.self_cmd.check_latest_version",
            return_value=current,
        ):
            result = runner.invoke(cli, ["self", "update"])

        assert result.exit_code == 0
        assert "up to date" in result.output.lower()

    def test_update_uv_tool_runs_upgrade(self, runner):
        """INVARIANT: For uv tool installs, runs 'uv tool upgrade langsmith-cli'."""
        mock_info = {
            "version": "0.1.0",
            "install_method": "uv tool",
            "install_path": "/some/path",
            "executable_path": "/some/bin/langsmith-cli",
            "python_path": sys.executable,
            "python_version": "3.12.0",
        }

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = "Updated langsmith-cli to 0.3.1"
        mock_proc.stderr = ""

        with (
            patch(
                "langsmith_cli.commands.self_cmd.detect_installation",
                return_value=mock_info,
            ),
            patch(
                "langsmith_cli.commands.self_cmd.check_latest_version",
                return_value="0.3.1",
            ),
            patch(
                "langsmith_cli.commands.self_cmd._verify_installed_version",
                return_value="0.3.1",
            ),
            patch("subprocess.run", return_value=mock_proc) as mock_run,
        ):
            result = runner.invoke(cli, ["self", "update"])

        assert result.exit_code == 0
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert cmd == ["uv", "tool", "upgrade", "--reinstall", "langsmith-cli"]
        # Lock in the #119 root-cause defense: --reinstall must be present.
        # Without it, uv's simple-index cache can silently no-op exit 0.
        # (`--reinstall` implies `--refresh`; the bare `--refresh` flag is
        # rejected by `uv tool upgrade` in uv 0.9.x.)
        assert "--reinstall" in cmd

    def test_update_json_output(self, runner):
        """INVARIANT: --json self update returns structured JSON with status."""
        from langsmith_cli.commands.self_cmd import detect_installation

        current = detect_installation()["version"]

        with patch(
            "langsmith_cli.commands.self_cmd.check_latest_version",
            return_value=current,
        ):
            result = runner.invoke(cli, ["--json", "self", "update"])

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "status" in data
        assert "current_version" in data

    def test_update_pypi_unreachable(self, runner):
        """INVARIANT: When PyPI is unreachable, still attempts update and treats a
        version change as success."""
        mock_info = {
            "version": "0.1.0",
            "install_method": "uv tool",
            "install_path": "/some/path",
            "executable_path": "/some/bin/langsmith-cli",
            "python_path": sys.executable,
            "python_version": "3.12.0",
        }

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = "Updated"
        mock_proc.stderr = ""

        with (
            patch(
                "langsmith_cli.commands.self_cmd.detect_installation",
                return_value=mock_info,
            ),
            patch(
                "langsmith_cli.commands.self_cmd.check_latest_version",
                return_value=None,
            ),
            patch(
                "langsmith_cli.commands.self_cmd._verify_installed_version",
                return_value="0.1.1",
            ),
            patch("subprocess.run", return_value=mock_proc),
        ):
            result = runner.invoke(cli, ["self", "update"])

        assert result.exit_code == 0


class TestVerifyInstalledVersion:
    """Unit tests for _verify_installed_version()."""

    def test_returns_parsed_version_from_subprocess(self):
        """INVARIANT: Parses 'langsmith-cli, version X.Y.Z' from a fresh subprocess."""
        from langsmith_cli.commands import self_cmd

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = "langsmith-cli, version 0.10.0\n"

        with (
            patch.object(
                self_cmd.shutil,
                "which",
                return_value="/home/u/.local/bin/langsmith-cli",
            ),
            patch("subprocess.run", return_value=mock_proc) as mock_run,
        ):
            assert self_cmd._verify_installed_version() == "0.10.0"
            # Must spawn the resolved executable, not the running interpreter.
            assert mock_run.call_args[0][0] == [
                "/home/u/.local/bin/langsmith-cli",
                "--version",
            ]

    def test_returns_none_when_executable_not_found(self):
        """INVARIANT: When shutil.which returns None, verification yields None."""
        from langsmith_cli.commands import self_cmd

        with patch.object(self_cmd.shutil, "which", return_value=None):
            assert self_cmd._verify_installed_version() is None

    def test_returns_none_when_subprocess_returncode_nonzero(self):
        """INVARIANT: A non-zero exit from the version probe yields None."""
        from langsmith_cli.commands import self_cmd

        mock_proc = MagicMock()
        mock_proc.returncode = 2
        mock_proc.stdout = ""

        with (
            patch.object(self_cmd.shutil, "which", return_value="/bin/langsmith-cli"),
            patch("subprocess.run", return_value=mock_proc),
        ):
            assert self_cmd._verify_installed_version() is None

    def test_returns_none_when_subprocess_raises(self):
        """INVARIANT: A crashing or timing-out probe yields None, not an exception."""
        import subprocess

        from langsmith_cli.commands import self_cmd

        with (
            patch.object(self_cmd.shutil, "which", return_value="/bin/langsmith-cli"),
            patch(
                "subprocess.run",
                side_effect=subprocess.TimeoutExpired(cmd="langsmith-cli", timeout=10),
            ),
        ):
            assert self_cmd._verify_installed_version() is None

    def test_returns_none_when_stdout_has_no_version(self):
        """INVARIANT: Malformed --version stdout yields None instead of crashing."""
        from langsmith_cli.commands import self_cmd

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = "no version here\n"

        with (
            patch.object(self_cmd.shutil, "which", return_value="/bin/langsmith-cli"),
            patch("subprocess.run", return_value=mock_proc),
        ):
            assert self_cmd._verify_installed_version() is None


class TestRemediationCommand:
    """Unit tests for get_remediation_command()."""

    def test_uv_tool_remediation_uses_force_install(self):
        """INVARIANT: uv tool failure suggests 'uv tool install --force'."""
        from langsmith_cli.commands.self_cmd import get_remediation_command

        assert (
            get_remediation_command("uv tool")
            == "uv tool install --force langsmith-cli && hash -r"
        )

    def test_pipx_remediation_uses_force_install(self):
        """INVARIANT: pipx failure suggests 'pipx install --force'."""
        from langsmith_cli.commands.self_cmd import get_remediation_command

        assert (
            get_remediation_command("pipx")
            == "pipx install --force langsmith-cli && hash -r"
        )

    def test_pip_remediation_uses_force_reinstall(self):
        """INVARIANT: pip failure suggests '--force-reinstall'."""
        from langsmith_cli.commands.self_cmd import get_remediation_command

        assert (
            get_remediation_command("pip (virtualenv)")
            == "pip install --upgrade --force-reinstall langsmith-cli"
        )
        assert (
            get_remediation_command("pip (system)")
            == "pip install --upgrade --force-reinstall langsmith-cli"
        )

    def test_unknown_install_method_returns_manual_hint(self):
        """INVARIANT: Unknown install methods return a manual-reinstall hint."""
        from langsmith_cli.commands.self_cmd import get_remediation_command

        assert "manually" in get_remediation_command("source (not installed)")


def _mk_info(version: str = "0.9.1", method: str = "uv tool") -> dict[str, str]:
    """Minimal detect_installation() stub for self update CliRunner tests."""
    return {
        "version": version,
        "install_method": method,
        "install_path": "/some/path",
        "executable_path": "/home/u/.local/bin/langsmith-cli",
        "python_path": sys.executable,
        "python_version": "3.12.0",
    }


def _mk_proc(returncode: int = 0, stdout: str = "", stderr: str = "") -> MagicMock:
    proc = MagicMock()
    proc.returncode = returncode
    proc.stdout = stdout
    proc.stderr = stderr
    return proc


class TestSelfUpdateVerification:
    """CLI integration tests for the post-upgrade verification gate (#119)."""

    def test_success_when_version_moves_to_latest(self, runner):
        """INVARIANT: Upgrade exit 0 + verify==latest → text success, exit 0."""
        with (
            patch(
                "langsmith_cli.commands.self_cmd.detect_installation",
                return_value=_mk_info("0.9.1"),
            ),
            patch(
                "langsmith_cli.commands.self_cmd.check_latest_version",
                return_value="0.10.0",
            ),
            patch(
                "langsmith_cli.commands.self_cmd._verify_installed_version",
                return_value="0.10.0",
            ),
            patch("subprocess.run", return_value=_mk_proc(0)),
        ):
            result = runner.invoke(cli, ["self", "update"])

        assert result.exit_code == 0
        assert "Update complete" in result.output
        assert "0.9.1" in result.output and "0.10.0" in result.output

    def test_success_json_includes_previous_and_current(self, runner):
        """INVARIANT: JSON success carries previous_version + current_version."""
        with (
            patch(
                "langsmith_cli.commands.self_cmd.detect_installation",
                return_value=_mk_info("0.9.1"),
            ),
            patch(
                "langsmith_cli.commands.self_cmd.check_latest_version",
                return_value="0.10.0",
            ),
            patch(
                "langsmith_cli.commands.self_cmd._verify_installed_version",
                return_value="0.10.0",
            ),
            patch("subprocess.run", return_value=_mk_proc(0)),
        ):
            result = runner.invoke(cli, ["--json", "self", "update"])

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["status"] == "updated"
        assert data["previous_version"] == "0.9.1"
        assert data["current_version"] == "0.10.0"
        assert data["latest_version"] == "0.10.0"

    def test_failure_when_version_unchanged_text_mode(self, runner):
        """INVARIANT: The exact #119 scenario — upgrade exit 0 but verify==previous
        → exit 1, output names the executable, install method, and remediation."""
        with (
            patch(
                "langsmith_cli.commands.self_cmd.detect_installation",
                return_value=_mk_info("0.9.1"),
            ),
            patch(
                "langsmith_cli.commands.self_cmd.check_latest_version",
                return_value="0.10.0",
            ),
            patch(
                "langsmith_cli.commands.self_cmd._verify_installed_version",
                return_value="0.9.1",
            ),
            patch("subprocess.run", return_value=_mk_proc(0)),
        ):
            result = runner.invoke(cli, ["self", "update"])

        assert result.exit_code != 0
        assert "Update complete" not in result.output
        assert "0.9.1" in result.output  # observed
        assert "0.10.0" in result.output  # expected
        assert "/home/u/.local/bin/langsmith-cli" in result.output  # exe path
        assert "uv tool" in result.output  # install method
        assert "uv tool install --force langsmith-cli" in result.output  # remediation
        assert "hash -r" in result.output

    def test_failure_when_version_unchanged_json_mode(self, runner):
        """INVARIANT: JSON failure has a distinct verification_failed status with
        the full structured payload required by the issue."""
        with (
            patch(
                "langsmith_cli.commands.self_cmd.detect_installation",
                return_value=_mk_info("0.9.1"),
            ),
            patch(
                "langsmith_cli.commands.self_cmd.check_latest_version",
                return_value="0.10.0",
            ),
            patch(
                "langsmith_cli.commands.self_cmd._verify_installed_version",
                return_value="0.9.1",
            ),
            patch("subprocess.run", return_value=_mk_proc(0)),
        ):
            result = runner.invoke(cli, ["--json", "self", "update"])

        assert result.exit_code != 0
        data = json.loads(result.output)
        assert data["status"] == "verification_failed"
        assert data["previous_version"] == "0.9.1"
        assert data["current_version"] == "0.9.1"
        assert data["expected_version"] == "0.10.0"
        assert data["executable_path"] == "/home/u/.local/bin/langsmith-cli"
        assert data["install_method"] == "uv tool"
        assert data["command"] == "uv tool upgrade --reinstall langsmith-cli"
        assert data["remediation"] == (
            "uv tool install --force langsmith-cli && hash -r"
        )

    def test_failure_when_version_cannot_be_read(self, runner):
        """INVARIANT: If the post-upgrade executable cannot be probed at all,
        treat as verification_failed with current_version=null."""
        with (
            patch(
                "langsmith_cli.commands.self_cmd.detect_installation",
                return_value=_mk_info("0.9.1"),
            ),
            patch(
                "langsmith_cli.commands.self_cmd.check_latest_version",
                return_value="0.10.0",
            ),
            patch(
                "langsmith_cli.commands.self_cmd._verify_installed_version",
                return_value=None,
            ),
            patch("subprocess.run", return_value=_mk_proc(0)),
        ):
            result = runner.invoke(cli, ["--json", "self", "update"])

        assert result.exit_code != 0
        data = json.loads(result.output)
        assert data["status"] == "verification_failed"
        assert data["current_version"] is None

    def test_pypi_unreachable_with_version_change_is_success(self, runner):
        """INVARIANT: latest=None + new_version != current → success."""
        with (
            patch(
                "langsmith_cli.commands.self_cmd.detect_installation",
                return_value=_mk_info("0.1.0"),
            ),
            patch(
                "langsmith_cli.commands.self_cmd.check_latest_version",
                return_value=None,
            ),
            patch(
                "langsmith_cli.commands.self_cmd._verify_installed_version",
                return_value="0.1.1",
            ),
            patch("subprocess.run", return_value=_mk_proc(0)),
        ):
            result = runner.invoke(cli, ["--json", "self", "update"])

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["status"] == "updated"
        assert data["current_version"] == "0.1.1"
        assert data["latest_version"] is None

    def test_pypi_unreachable_with_no_version_change_is_failure(self, runner):
        """INVARIANT: latest=None + new_version == current → verification_failed.
        Without PyPI data we must still refuse to print success when nothing moved."""
        with (
            patch(
                "langsmith_cli.commands.self_cmd.detect_installation",
                return_value=_mk_info("0.1.0"),
            ),
            patch(
                "langsmith_cli.commands.self_cmd.check_latest_version",
                return_value=None,
            ),
            patch(
                "langsmith_cli.commands.self_cmd._verify_installed_version",
                return_value="0.1.0",
            ),
            patch("subprocess.run", return_value=_mk_proc(0)),
        ):
            result = runner.invoke(cli, ["--json", "self", "update"])

        assert result.exit_code != 0
        data = json.loads(result.output)
        assert data["status"] == "verification_failed"
        assert data["previous_version"] == "0.1.0"
        assert data["current_version"] == "0.1.0"

    def test_upgrade_subprocess_failure_path_unchanged(self, runner):
        """INVARIANT: Subprocess non-zero exit still reports status=error (not
        verification_failed). Verification is only attempted on subprocess success."""
        with (
            patch(
                "langsmith_cli.commands.self_cmd.detect_installation",
                return_value=_mk_info("0.9.1"),
            ),
            patch(
                "langsmith_cli.commands.self_cmd.check_latest_version",
                return_value="0.10.0",
            ),
            patch(
                "langsmith_cli.commands.self_cmd._verify_installed_version",
                return_value="0.10.0",  # should NOT be consulted
            ) as mock_verify,
            patch(
                "subprocess.run",
                return_value=_mk_proc(1, stderr="network error"),
            ),
        ):
            result = runner.invoke(cli, ["--json", "self", "update"])

        assert result.exit_code != 0
        data = json.loads(result.output)
        assert data["status"] == "error"
        mock_verify.assert_not_called()


class TestSkillCommand:
    """Tests for self skill command."""

    def test_skill_outputs_main_guide(self, runner):
        """INVARIANT: self skill with no args prints main SKILL.md content."""
        result = runner.invoke(cli, ["self", "skill"])
        assert result.exit_code == 0
        assert "langsmith" in result.output
        assert "--json" in result.output

    def test_skill_json_mode(self, runner):
        """INVARIANT: --json mode wraps skill content in {"doc": ..., "content": ...}."""
        result = runner.invoke(cli, ["--json", "self", "skill"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "content" in data
        assert "--json" in data["content"]

    def test_skill_list_shows_discovered_docs(self, runner):
        """INVARIANT: --list auto-discovers docs from skill_docs/ directory."""
        result = runner.invoke(cli, ["self", "skill", "--list"])
        assert result.exit_code == 0
        assert "runs" in result.output
        assert "fql" in result.output

    def test_skill_list_json_mode(self, runner):
        """INVARIANT: --list --json returns sorted list of doc names."""
        result = runner.invoke(cli, ["--json", "self", "skill", "--list"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "docs" in data
        assert "runs" in data["docs"]
        assert "fql" in data["docs"]

    def test_skill_reference_doc(self, runner):
        """INVARIANT: self skill <name> returns that reference doc."""
        result = runner.invoke(cli, ["self", "skill", "runs"])
        assert result.exit_code == 0
        assert len(result.output) > 100

    def test_skill_unknown_doc_error(self, runner):
        """INVARIANT: Unknown doc name gives a clear error with available list."""
        result = runner.invoke(cli, ["self", "skill", "nonexistent"])
        assert result.exit_code != 0
        assert "nonexistent" in result.output
