"""
Cross-platform configuration and credential management for langsmith-cli.

This module uses platformdirs to determine appropriate config directories
across Linux, macOS, and Windows following OS-specific conventions.
"""

import os
from pathlib import Path
from platformdirs import user_config_dir


def get_config_dir() -> Path:
    """
    Get the cross-platform user config directory for langsmith-cli.

    Returns:
        Path to config directory:
        - Linux: ~/.config/langsmith-cli/
        - macOS: ~/Library/Application Support/langsmith-cli/
        - Windows: %APPDATA%\\Local\\langsmith-cli\\
    """
    return Path(user_config_dir("langsmith-cli", appauthor=False))


def get_credentials_file() -> Path:
    """
    Get the path to the credentials file.

    Returns:
        Path to credentials file (contains LANGSMITH_API_KEY)
    """
    return get_config_dir() / "credentials"


def save_api_key(api_key: str) -> Path:
    """
    Save API key to credentials file with secure permissions.

    On Unix/Linux/macOS, sets file permissions to 0600 (owner read/write only).
    On Windows, relies on AppData\\Local default user-only permissions.

    Args:
        api_key: The LangSmith API key to save

    Returns:
        Path to the created credentials file
    """
    config_dir = get_config_dir()
    config_dir.mkdir(parents=True, exist_ok=True)

    # Set secure permissions on config directory (Unix/macOS only)
    if os.name != "nt":
        config_dir.chmod(0o700)  # drwx------

    creds_file = get_credentials_file()
    creds_file.write_text(f"LANGSMITH_API_KEY={api_key}\n", encoding="utf-8")

    # Set secure permissions on credentials file (Unix/macOS only)
    if os.name != "nt":
        creds_file.chmod(0o600)  # -rw-------

    return creds_file


def load_api_key() -> str | None:
    """
    Load API key from credentials file or environment.

    Priority order:
    1. LANGSMITH_API_KEY environment variable
    2. Credentials file in user config directory
    3. None if not found

    Returns:
        API key if found, None otherwise
    """
    # Priority 1: Environment variable
    if api_key := os.environ.get("LANGSMITH_API_KEY"):
        return api_key

    # Priority 2: Credentials file
    creds_file = get_credentials_file()
    if creds_file.exists():
        from dotenv import dotenv_values

        config = dotenv_values(creds_file)
        return config.get("LANGSMITH_API_KEY")

    return None


def credentials_file_exists() -> bool:
    """
    Check if credentials file exists.

    Returns:
        True if credentials file exists, False otherwise
    """
    return get_credentials_file().exists()
