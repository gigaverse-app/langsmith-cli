"""Self-inspection commands for langsmith-cli."""

import json as json_mod
import platform
import shutil
import sys
from typing import Any

import click

from langsmith_cli.utils import json_dumps


def _detect_install_method(dist: Any) -> str:
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
            url_data = json_mod.loads(url_text)
            dir_info = url_data.get("dir_info", {})
            if dir_info.get("editable"):
                return "development (editable)"
            url = url_data.get("url", "")
            if url.startswith("file://"):
                return "local (non-editable)"
    except Exception:
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
        try:
            result["install_path"] = str(dist._path.parent)  # type: ignore[attr-defined]
        except AttributeError:
            result["install_path"] = "unknown"
        result["install_method"] = _detect_install_method(dist)
    except importlib.metadata.PackageNotFoundError:
        result["version"] = "unknown"
        result["install_path"] = "unknown"
        result["install_method"] = "source (not installed)"

    return result


_UPDATE_COMMANDS: dict[str, str] = {
    "uv tool": "uv tool upgrade langsmith-cli",
    "pipx": "pipx upgrade langsmith-cli",
    "pip (virtualenv)": "pip install --upgrade langsmith-cli",
    "pip (system)": "pip install --upgrade langsmith-cli",
}


def get_update_command(install_method: str) -> str | None:
    """Return the shell command to update langsmith-cli for the given install method.

    Returns None for install methods that can't be auto-updated (editable, unknown).
    """
    return _UPDATE_COMMANDS.get(install_method)


def check_latest_version() -> str | None:
    """Check PyPI for the latest version of langsmith-cli.

    Returns the version string, or None if the check fails.
    """
    import urllib.request

    try:
        with urllib.request.urlopen(  # noqa: S310
            "https://pypi.org/pypi/langsmith-cli/json", timeout=5
        ) as resp:
            data = json_mod.loads(resp.read())
            return data["info"]["version"]
    except Exception:
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

    if ctx.obj.get("json"):
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
        table.add_row(label, data.get(key, "unknown"))

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
    except Exception:
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
        if ctx.obj.get("json"):
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

    text = path.read_text()
    if ctx.obj.get("json"):
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
    json_mode = ctx.obj.get("json")

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

    result = subprocess.run(cmd.split(), capture_output=True, text=True)

    if result.returncode == 0:
        if json_mode:
            click.echo(
                json_dumps(
                    {
                        "status": "updated",
                        "current_version": current,
                        "latest_version": latest,
                        "command": cmd,
                    }
                )
            )
        else:
            if result.stdout.strip():
                click.echo(result.stdout.strip())
            click.echo("Update complete.")
    else:
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
        ctx.exit(1)
