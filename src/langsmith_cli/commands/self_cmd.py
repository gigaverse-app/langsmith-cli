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
