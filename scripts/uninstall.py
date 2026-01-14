#!/usr/bin/env python3
"""
Uninstaller for langsmith-cli standalone installations.

This script removes langsmith-cli that was installed via the standalone installer.

Usage:
    python3 uninstall.py
    curl -sSL https://raw.githubusercontent.com/langchain-ai/langsmith-cli/main/scripts/uninstall.py | python3 -
"""

import json
import os
import platform
import re
import shutil
import sys
from pathlib import Path
from typing import Optional

# Colors for terminal output
BOLD = "\033[1m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
RESET = "\033[0m"


def log_info(msg: str) -> None:
    """Print info message."""
    print(f"{GREEN}✓{RESET} {msg}")


def log_warning(msg: str) -> None:
    """Print warning message."""
    print(f"{YELLOW}⚠{RESET} {msg}", file=sys.stderr)


def log_error(msg: str) -> None:
    """Print error message."""
    print(f"{RED}✗{RESET} {msg}", file=sys.stderr)


def get_install_receipt_path() -> Path:
    """Get path to install receipt file."""
    os_name = platform.system().lower()

    if os_name == "windows":
        config_dir = (
            Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
            / "langsmith-cli"
        )
    else:
        # Unix (Linux/macOS)
        config_home = os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))
        config_dir = Path(config_home) / "langsmith-cli"

    return config_dir / "install_receipt.json"


def load_install_receipt() -> Optional[dict]:
    """Load install receipt if it exists."""
    receipt_path = get_install_receipt_path()

    if not receipt_path.exists():
        return None

    try:
        with receipt_path.open() as f:
            return json.load(f)
    except Exception as e:
        log_warning(f"Could not read install receipt: {e}")
        return None


def remove_directory(path: Path, description: str) -> bool:
    """Remove a directory and all its contents."""
    if not path.exists():
        log_warning(f"{description} not found at {path}")
        return True

    try:
        shutil.rmtree(path)
        log_info(f"Removed {description}: {path}")
        return True
    except Exception as e:
        log_error(f"Failed to remove {description}: {e}")
        return False


def remove_file(path: Path, description: str) -> bool:
    """Remove a single file."""
    if not path.exists():
        log_warning(f"{description} not found at {path}")
        return True

    try:
        path.unlink()
        log_info(f"Removed {description}: {path}")
        return True
    except Exception as e:
        log_error(f"Failed to remove {description}: {e}")
        return False


def clean_path_unix(wrapper_dir: Path) -> bool:
    """Remove langsmith-cli from PATH in Unix shell profiles."""
    home = Path.home()
    profiles = [
        home / ".bashrc",
        home / ".bash_profile",
        home / ".zshrc",
        home / ".profile",
    ]

    pattern = re.compile(rf".*{re.escape(str(wrapper_dir))}.*\n?")
    comment_pattern = re.compile(r"# Added by langsmith-cli installer\n")

    modified = False

    for profile in profiles:
        if not profile.exists():
            continue

        try:
            content = profile.read_text()
            original_content = content

            # Remove PATH export lines
            content = pattern.sub("", content)

            # Remove comment lines
            content = comment_pattern.sub("", content)

            if content != original_content:
                profile.write_text(content)
                log_info(f"Cleaned PATH from {profile}")
                modified = True

        except Exception as e:
            log_warning(f"Could not clean {profile}: {e}")

    return modified


def clean_path_windows(wrapper_dir: Path) -> bool:
    """Remove langsmith-cli from PATH in Windows registry."""
    try:
        import winreg

        # Open user environment variables key
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            "Environment",
            0,
            winreg.KEY_READ | winreg.KEY_WRITE,
        )

        try:
            # Get current PATH
            path_value, _ = winreg.QueryValueEx(key, "Path")

            # Remove wrapper_dir from PATH
            paths = path_value.split(";")
            new_paths = [p for p in paths if Path(p) != wrapper_dir and p.strip()]
            new_path = ";".join(new_paths)

            if new_path != path_value:
                winreg.SetValueEx(key, "Path", 0, winreg.REG_EXPAND_SZ, new_path)
                log_info("Cleaned PATH from Windows registry")
                return True

            return False

        finally:
            winreg.CloseKey(key)

    except Exception as e:
        log_warning(f"Could not clean Windows PATH: {e}")
        return False


def main() -> int:
    """Main uninstallation function."""
    print(f"\n{BOLD}langsmith-cli Uninstaller{RESET}\n")

    # Load install receipt
    receipt = load_install_receipt()

    if receipt is None:
        log_warning("Install receipt not found.")
        print("\nNo standalone installation detected.")
        print("If you installed via pip, run: pip uninstall langsmith-cli")
        print("If you installed via uv, run: uv tool uninstall langsmith-cli")
        print()
        response = input("Continue with manual uninstall? [y/N]: ").strip().lower()
        if response not in ("y", "yes"):
            return 0

    # Get paths from receipt or use defaults
    os_name = platform.system().lower()

    if receipt:
        venv_path = Path(receipt.get("venv_path", ""))
        wrapper_path = Path(receipt.get("wrapper_path", ""))
    else:
        # Use default paths
        if os_name == "windows":
            local_appdata = Path(
                os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")
            )
            venv_path = local_appdata / "langsmith-cli"
            wrapper_path = local_appdata / "Programs" / "langsmith-cli"
        else:
            data_home = Path(
                os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")
            )
            venv_path = data_home / "langsmith-cli"
            wrapper_path = Path.home() / ".local" / "bin"

    config_dir = get_install_receipt_path().parent

    print(f"{BOLD}Will remove:{RESET}")
    print(f"  Virtual environment: {venv_path}")
    print(
        f"  Wrapper script:      {wrapper_path / 'langsmith-cli' if os_name != 'windows' else wrapper_path / 'langsmith-cli.exe'}"
    )
    print(f"  Configuration:       {config_dir}")
    print()

    # Confirm uninstallation
    response = input(f"{BOLD}Continue with uninstall? [y/N]:{RESET} ").strip().lower()
    if response not in ("y", "yes"):
        print("Uninstall cancelled.")
        return 0

    print()

    success = True

    # Remove virtual environment
    if not remove_directory(venv_path, "virtual environment"):
        success = False

    # Remove wrapper script
    wrapper_file = (
        wrapper_path / "langsmith-cli.exe"
        if os_name == "windows"
        else wrapper_path / "langsmith-cli"
    )

    if not remove_file(wrapper_file, "wrapper script"):
        success = False

    # Remove wrapper directory if empty (Windows only)
    if os_name == "windows":
        try:
            if wrapper_path.exists() and not any(wrapper_path.iterdir()):
                wrapper_path.rmdir()
                log_info(f"Removed empty directory: {wrapper_path}")
        except Exception:
            pass

    # Remove configuration directory
    if not remove_directory(config_dir, "configuration directory"):
        success = False

    # Clean PATH
    print()
    response = input("Remove from PATH? [Y/n]: ").strip().lower()
    if response in ("", "y", "yes"):
        if os_name == "windows":
            if clean_path_windows(wrapper_path):
                print(
                    f"\n{YELLOW}Note:{RESET} You may need to restart your terminal for PATH changes to take effect."
                )
        else:
            if clean_path_unix(wrapper_path):
                print(
                    f"\n{YELLOW}Note:{RESET} Run 'source ~/.bashrc' (or equivalent) to reload your shell profile."
                )

    # Final message
    print()
    if success:
        print(f"{BOLD}{GREEN}✓ Uninstall complete!{RESET}\n")
    else:
        print(f"{BOLD}{YELLOW}⚠ Uninstall completed with some errors{RESET}\n")
        print("You may need to manually remove remaining files.")
        print()

    return 0 if success else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n\nUninstall cancelled by user.")
        sys.exit(130)
    except Exception as e:
        log_error(f"Unexpected error: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
