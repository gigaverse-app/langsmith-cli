"""Self-inspection commands for langsmith-cli."""

import json as json_mod
import platform
import shutil
import shlex
import sys
from typing import Any, Protocol, TypedDict

import click

from langsmith_cli.utils import is_json_context, json_dumps


class DistributionProtocol(Protocol):
    """Subset of importlib.metadata.Distribution used by self commands."""

    metadata: Any

    def read_text(self, filename: str) -> str | None:
        """Read package metadata text."""
        ...

    def locate_file(self, path: str) -> Any:
        """Return a path inside the distribution."""
        ...


class DirectURLDirInfo(TypedDict, total=False):
    """PEP 610 dir_info subset."""

    editable: bool


class DirectURLMetadata(TypedDict, total=False):
    """PEP 610 direct_url.json subset used for install detection."""

    dir_info: DirectURLDirInfo
    url: str


class PyPIInfo(TypedDict):
    """PyPI package info subset."""

    version: str


class PyPIResponse(TypedDict):
    """PyPI JSON response subset."""

    info: PyPIInfo


def _parse_direct_url_metadata(raw_text: str) -> DirectURLMetadata:
    parsed = json_mod.loads(raw_text)
    if not isinstance(parsed, dict):
        raise ValueError("direct_url.json must contain an object")

    result: DirectURLMetadata = {}
    if "dir_info" in parsed:
        dir_info = parsed["dir_info"]
        if not isinstance(dir_info, dict):
            raise ValueError("direct_url.json dir_info must contain an object")
        parsed_dir_info: DirectURLDirInfo = {}
        if "editable" in dir_info:
            parsed_dir_info["editable"] = bool(dir_info["editable"])
        result["dir_info"] = parsed_dir_info

    if "url" in parsed:
        url = parsed["url"]
        if not isinstance(url, str):
            raise ValueError("direct_url.json url must be a string")
        result["url"] = url

    return result


def _parse_pypi_response(raw_bytes: bytes) -> PyPIResponse:
    parsed = json_mod.loads(raw_bytes)
    if not isinstance(parsed, dict):
        raise ValueError("PyPI response must contain an object")
    if "info" not in parsed or not isinstance(parsed["info"], dict):
        raise ValueError("PyPI response missing info object")
    info = parsed["info"]
    if "version" not in info or not isinstance(info["version"], str):
        raise ValueError("PyPI response missing info.version")
    return {"info": {"version": info["version"]}}


def _detect_install_method(dist: DistributionProtocol) -> str:
    """Detect how langsmith-cli was installed based on metadata and paths.

    Priority:
    1. direct_url.json (PEP 610) - editable, local installs
    2. Path-based heuristics - uv tool, pipx
    3. Virtual environment detection - pip in venv vs system
    """
    # Priority 1: Check direct_url.json (PEP 610)
    try:
        url_text = dist.read_text("direct_url.json")
        if url_text:
            url_data = _parse_direct_url_metadata(url_text)
            if "dir_info" in url_data:
                dir_info = url_data["dir_info"]
                if "editable" in dir_info and dir_info["editable"]:
                    return "development (editable)"
            url = url_data["url"] if "url" in url_data else ""
            if url.startswith("file://"):
                return "local (non-editable)"
    except (json_mod.JSONDecodeError, OSError, ValueError):
        pass  # Fall through to path-based detection

    # Priority 2: Path-based heuristics
    prefix = sys.prefix
    if "/uv/tools/" in prefix or "\\uv\\tools\\" in prefix:
        return "uv tool"
    if "/pipx/" in prefix or "\\pipx\\" in prefix:
        return "pipx"

    # Priority 3: Virtual environment detection
    if sys.prefix != sys.base_prefix:
        return "pip (virtualenv)"

    return "pip (system)"


def detect_installation() -> dict[str, str]:
    """Detect installation details for langsmith-cli.

    Returns a dict with keys: version, install_method, install_path,
    executable_path, python_path, python_version.
    """
    import importlib.metadata

    result: dict[str, str] = {
        "python_path": sys.executable,
        "python_version": platform.python_version(),
    }

    # Executable path
    exe_path = shutil.which("langsmith-cli")
    result["executable_path"] = exe_path or "not found in PATH"

    # Try to get package metadata
    try:
        dist = importlib.metadata.distribution("langsmith-cli")
        result["version"] = dist.metadata["Version"]
        result["install_path"] = str(dist.locate_file(""))
        result["install_method"] = _detect_install_method(dist)
    except importlib.metadata.PackageNotFoundError:
        result["version"] = "unknown"
        result["install_path"] = "unknown"
        result["install_method"] = "source (not installed)"

    return result


# `--refresh` is the answer to issue #119's root cause: `uv tool upgrade` reads
# the package's available-version list from ~/.cache/uv/simple-v*/pypi/<name>.rkyv,
# which is invalidated using HTTP cache semantics from PyPI's simple index. For
# minutes after a new release ships, uv can resolve against a stale view and
# silently no-op exit 0. `--refresh` forces a fresh index fetch every time, so
# `self update` never relies on a cache that hasn't seen the new version yet.
# Verification (`_verify_installed_version`) still guards the post-condition for
# any *other* reason the installer might no-op.
_UPDATE_COMMANDS: dict[str, str] = {
    "uv tool": "uv tool upgrade --refresh langsmith-cli",
    "pipx": "pipx upgrade langsmith-cli",
    "pip (virtualenv)": "pip install --upgrade langsmith-cli",
    "pip (system)": "pip install --upgrade langsmith-cli",
}


_REMEDIATION_COMMANDS: dict[str, str] = {
    "uv tool": "uv tool install --force langsmith-cli && hash -r",
    "pipx": "pipx install --force langsmith-cli && hash -r",
    "pip (virtualenv)": "pip install --upgrade --force-reinstall langsmith-cli",
    "pip (system)": "pip install --upgrade --force-reinstall langsmith-cli",
}


def get_update_command(install_method: str) -> str | None:
    """Return the shell command to update langsmith-cli for the given install method.

    Returns None for install methods that can't be auto-updated (editable, unknown).
    """
    if install_method in _UPDATE_COMMANDS:
        return _UPDATE_COMMANDS[install_method]
    return None


def get_remediation_command(install_method: str) -> str:
    """Return a force-reinstall fallback for when an upgrade subprocess no-ops."""
    return _REMEDIATION_COMMANDS.get(
        install_method, "Reinstall langsmith-cli manually."
    )


def _verify_installed_version() -> str | None:
    """Read the installed CLI version by invoking the executable as a fresh subprocess.

    The running Python process has the *old* install's metadata pinned on sys.path,
    so importlib.metadata cannot see the upgraded version. A fresh subprocess loads
    whatever is currently at the resolved executable path on disk.

    Returns the parsed version string, or None if verification cannot be performed.
    """
    import re
    import subprocess

    exe = shutil.which("langsmith-cli")
    if exe is None:
        return None
    try:
        result = subprocess.run(
            [exe, "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (subprocess.SubprocessError, OSError):
        return None
    if result.returncode != 0:
        return None
    # Click's default --version output: "<prog>, version X.Y.Z" — require a
    # digit-led token so garbage output doesn't masquerade as a version.
    match = re.search(r"version\s+(\d\S*)", result.stdout)
    return match.group(1) if match else None


def check_latest_version() -> str | None:
    """Check PyPI for the latest version of langsmith-cli.

    Returns the version string, or None if the check fails.
    """
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(  # noqa: S310
            "https://pypi.org/pypi/langsmith-cli/json", timeout=5
        ) as resp:
            data = _parse_pypi_response(resp.read())
            return data["info"]["version"]
    except (
        urllib.error.URLError,
        TimeoutError,
        json_mod.JSONDecodeError,
        OSError,
        ValueError,
    ):
        return None


@click.group("self")
def self_group():
    """Inspect and manage the langsmith-cli installation."""
    pass


@self_group.command("detect")
@click.pass_context
def detect(ctx: click.Context) -> None:
    """Show installation details (version, install method, paths)."""
    data = detect_installation()

    if is_json_context(ctx):
        click.echo(json_dumps(data))
        return

    from rich.console import Console
    from rich.table import Table

    console = Console()
    table = Table(title="Installation Details")
    table.add_column("Property", style="bold cyan")
    table.add_column("Value")

    display_names = {
        "version": "Version",
        "install_method": "Install method",
        "install_path": "Install path",
        "executable_path": "Executable",
        "python_path": "Python",
        "python_version": "Python version",
    }

    for key, label in display_names.items():
        table.add_row(label, data[key])

    console.print(table)


def _discover_skill_docs() -> dict[str, str]:
    """Discover available reference docs by scanning the skill_docs/ package directory."""
    import importlib.resources

    docs: dict[str, str] = {}
    skill_docs_pkg = importlib.resources.files("langsmith_cli").joinpath("skill_docs")
    try:
        for entry in skill_docs_pkg.iterdir():  # type: ignore[union-attr]
            name = entry.name  # type: ignore[union-attr]
            if name.endswith(".md"):
                docs[name[:-3]] = f"skill_docs/{name}"
    except (FileNotFoundError, ModuleNotFoundError, NotADirectoryError, OSError):
        pass
    return docs


@self_group.command("skill")
@click.argument("doc", required=False, default=None)
@click.option(
    "--list", "list_docs", is_flag=True, help="List available reference docs."
)
@click.pass_context
def skill_docs(ctx: click.Context, doc: str | None, list_docs: bool) -> None:
    """Print the agent usage guide or a specific reference doc.

    \b
    Without arguments: prints the main skill guide (SKILL.md).
    With a doc name: prints that reference document.
    With --list: shows all available reference doc names.

    \b
    Example:
      langsmith-cli self skill
      langsmith-cli self skill --list
      langsmith-cli self skill runs
      langsmith-cli self skill fql
    """
    import importlib.resources

    pkg = importlib.resources.files("langsmith_cli")
    available = _discover_skill_docs()

    if list_docs:
        if is_json_context(ctx):
            click.echo(json_dumps({"docs": sorted(available.keys()), "main": "skill"}))
        else:
            click.echo("Available skill docs (use: langsmith-cli self skill <name>):\n")
            click.echo("  (main)            Main agent guide (default)")
            for name in sorted(available.keys()):
                click.echo(f"  {name:<18}  self skill {name}")
        return

    if doc is None:
        path = pkg.joinpath("SKILL.md")
    elif doc in available:
        path = pkg.joinpath(available[doc])
    else:
        names = ", ".join(sorted(available.keys()))
        raise click.BadParameter(
            f"Unknown doc '{doc}'. Run 'self skill --list' to see available docs. "
            f"Available: {names}",
            param_hint="DOC",
        )

    text = path.read_text(encoding="utf-8")
    if is_json_context(ctx):
        click.echo(json_dumps({"doc": doc or "skill", "content": text}))
    else:
        click.echo(text)


@self_group.command("update")
@click.pass_context
def update(ctx: click.Context) -> None:
    """Update langsmith-cli to the latest version."""
    import subprocess

    info = detect_installation()
    current = info["version"]
    method = info["install_method"]
    json_mode = is_json_context(ctx)

    # Check latest version on PyPI
    latest = check_latest_version()

    # Already up to date?
    if latest and current == latest:
        if json_mode:
            click.echo(
                json_dumps(
                    {
                        "status": "up_to_date",
                        "current_version": current,
                        "latest_version": latest,
                    }
                )
            )
        else:
            click.echo(f"Already up to date (v{current}).")
        return

    # Get the update command for this install method
    cmd = get_update_command(method)

    if cmd is None:
        # Can't auto-update (editable or unknown)
        if json_mode:
            click.echo(
                json_dumps(
                    {
                        "status": "manual_update_required",
                        "current_version": current,
                        "latest_version": latest,
                        "install_method": method,
                        "hint": "Run 'git pull && uv sync' to update manually.",
                    }
                )
            )
        else:
            version_info = f"v{current}"
            if latest:
                version_info += f" -> v{latest}"
            click.echo(f"Current: {version_info}")
            click.echo(f"Install method: {method}")
            click.echo(
                "This install cannot be auto-updated. "
                "Run 'git pull && uv sync' to update manually."
            )
        return

    # Run the update command
    version_info = f"v{current}"
    if latest:
        version_info += f" -> v{latest}"

    if not json_mode:
        click.echo(f"Updating langsmith-cli ({version_info})...")
        click.echo(f"Running: {cmd}")

    result = subprocess.run(shlex.split(cmd), capture_output=True, text=True)

    if result.returncode != 0:
        if json_mode:
            click.echo(
                json_dumps(
                    {
                        "status": "error",
                        "current_version": current,
                        "latest_version": latest,
                        "command": cmd,
                        "error": result.stderr.strip(),
                    }
                )
            )
        else:
            click.echo(f"Update failed (exit code {result.returncode}).")
            if result.stderr.strip():
                click.echo(result.stderr.strip())
        # See note below: sys.exit avoids the root JSON-error handler double-emitting.
        sys.exit(1)

    # Upgrade subprocess exited 0 — verify the installed version actually moved.
    # The running process still has the *old* install pinned on sys.path, so we
    # spawn the installed executable as a fresh subprocess to read the new version.
    new_version = _verify_installed_version()
    verified = new_version is not None and (
        (latest is not None and new_version == latest)
        or (latest is None and new_version != current)
    )

    if verified:
        if json_mode:
            click.echo(
                json_dumps(
                    {
                        "status": "updated",
                        "previous_version": current,
                        "current_version": new_version,
                        "latest_version": latest,
                        "command": cmd,
                    }
                )
            )
        else:
            if result.stdout.strip():
                click.echo(result.stdout.strip())
            click.echo(f"Update complete (v{current} -> v{new_version}).")
        return

    # Verification failed: subprocess succeeded but the installed version did
    # not move to the expected target. This is the #119 false-success case.
    exe_path = info["executable_path"]
    remediation = get_remediation_command(method)
    if json_mode:
        click.echo(
            json_dumps(
                {
                    "status": "verification_failed",
                    "previous_version": current,
                    "current_version": new_version,
                    "expected_version": latest,
                    "executable_path": exe_path,
                    "install_method": method,
                    "command": cmd,
                    "remediation": remediation,
                }
            )
        )
    else:
        observed = new_version if new_version is not None else "unknown"
        expected = f"v{latest}" if latest else "a newer version"
        click.echo(
            f"Warning: '{cmd}' exited successfully but the installed CLI "
            f"is still at v{observed} (expected {expected})."
        )
        click.echo(f"  Executable: {exe_path}")
        click.echo(f"  Install method: {method}")
        click.echo("Try the following to force a clean reinstall:")
        click.echo(f"  {remediation}")
    # sys.exit (SystemExit) bypasses the root group's JSON-error handler, which
    # otherwise would append a second {"error":"Exit"...} line after our payload.
    sys.exit(1)
