"""Tests for config module (cross-platform credential management)."""

import os
from pathlib import Path
from langsmith_cli.config import (
    get_config_dir,
    get_credentials_file,
    save_api_key,
    load_api_key,
    credentials_file_exists,
)


def test_get_config_dir():
    """Test that config dir returns a Path object."""
    config_dir = get_config_dir()
    assert isinstance(config_dir, Path)
    assert "langsmith-cli" in str(config_dir)


def test_get_credentials_file():
    """Test that credentials file path is correct."""
    creds_file = get_credentials_file()
    assert isinstance(creds_file, Path)
    assert creds_file.name == "credentials"
    assert "langsmith-cli" in str(creds_file)


def test_save_api_key(tmp_path, monkeypatch):
    """Test saving API key to credentials file."""
    # Mock the config dir to use temp path
    monkeypatch.setattr(
        "langsmith_cli.config.get_config_dir", lambda: tmp_path / "langsmith-cli"
    )

    api_key = "lsv2_test_key_12345"
    saved_path = save_api_key(api_key)

    # Verify file was created
    assert saved_path.exists()
    assert saved_path.parent == tmp_path / "langsmith-cli"
    assert saved_path.name == "credentials"

    # Verify content
    content = saved_path.read_text()
    assert f"LANGSMITH_API_KEY={api_key}" in content

    # Verify directory permissions on Unix (skip on Windows)
    if os.name != "nt":
        dir_stat = saved_path.parent.stat()
        dir_perms = oct(dir_stat.st_mode)[-3:]
        assert dir_perms == "700", f"Expected 700, got {dir_perms}"

        # Verify file permissions
        file_stat = saved_path.stat()
        file_perms = oct(file_stat.st_mode)[-3:]
        assert file_perms == "600", f"Expected 600, got {file_perms}"


def test_load_api_key_from_env(monkeypatch):
    """Test loading API key from environment variable."""
    test_key = "lsv2_env_key"
    monkeypatch.setenv("LANGSMITH_API_KEY", test_key)

    loaded_key = load_api_key()
    assert loaded_key == test_key


def test_load_api_key_from_file(tmp_path, monkeypatch):
    """Test loading API key from credentials file."""
    # Clear environment variable
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)

    # Mock config dir
    config_dir = tmp_path / "langsmith-cli"
    config_dir.mkdir(parents=True)
    creds_file = config_dir / "credentials"

    test_key = "lsv2_file_key"
    creds_file.write_text(f"LANGSMITH_API_KEY={test_key}\n")

    monkeypatch.setattr("langsmith_cli.config.get_config_dir", lambda: config_dir)

    loaded_key = load_api_key()
    assert loaded_key == test_key


def test_load_api_key_priority(tmp_path, monkeypatch):
    """Test that environment variable takes priority over file."""
    env_key = "lsv2_env_priority"
    file_key = "lsv2_file_priority"

    # Set up credentials file
    config_dir = tmp_path / "langsmith-cli"
    config_dir.mkdir(parents=True)
    creds_file = config_dir / "credentials"
    creds_file.write_text(f"LANGSMITH_API_KEY={file_key}\n")

    monkeypatch.setattr("langsmith_cli.config.get_config_dir", lambda: config_dir)
    monkeypatch.setenv("LANGSMITH_API_KEY", env_key)

    # Environment variable should win
    loaded_key = load_api_key()
    assert loaded_key == env_key


def test_load_api_key_not_found(tmp_path, monkeypatch):
    """Test loading API key when not set anywhere."""
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    monkeypatch.setattr(
        "langsmith_cli.config.get_config_dir", lambda: tmp_path / "nonexistent"
    )

    loaded_key = load_api_key()
    assert loaded_key is None


def test_credentials_file_exists(tmp_path, monkeypatch):
    """Test checking if credentials file exists."""
    config_dir = tmp_path / "langsmith-cli"
    monkeypatch.setattr("langsmith_cli.config.get_config_dir", lambda: config_dir)

    # Should not exist initially
    assert credentials_file_exists() is False

    # Create file
    config_dir.mkdir(parents=True)
    creds_file = config_dir / "credentials"
    creds_file.write_text("LANGSMITH_API_KEY=test\n")

    # Should exist now
    assert credentials_file_exists() is True


def test_save_api_key_creates_parent_dirs(tmp_path, monkeypatch):
    """Test that save_api_key creates parent directories if needed."""
    config_dir = tmp_path / "deep" / "nested" / "langsmith-cli"
    monkeypatch.setattr("langsmith_cli.config.get_config_dir", lambda: config_dir)

    api_key = "lsv2_nested_test"
    saved_path = save_api_key(api_key)

    assert saved_path.exists()
    assert saved_path.parent == config_dir
    content = saved_path.read_text()
    assert api_key in content
