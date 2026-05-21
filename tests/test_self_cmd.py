"""Tests for self-inspection command helpers."""

import urllib.error
import urllib.request
from pathlib import Path

import pytest

from langsmith_cli.commands import self_cmd


class FakeDistribution:
    """Small importlib.metadata.Distribution stand-in for install detection."""

    metadata = {"Version": "1.2.3"}

    def __init__(self, direct_url_text: str | None) -> None:
        self._direct_url_text = direct_url_text

    def read_text(self, filename: str) -> str | None:
        assert filename == "direct_url.json"
        return self._direct_url_text

    def locate_file(self, path: str) -> Path:
        assert path == ""
        return Path("/tmp/langsmith-cli")


def test_detect_install_method_editable_direct_url():
    dist = FakeDistribution('{"dir_info": {"editable": true}}')

    assert self_cmd._detect_install_method(dist) == "development (editable)"


def test_detect_install_method_local_direct_url():
    dist = FakeDistribution('{"url": "file:///tmp/langsmith-cli"}')

    assert self_cmd._detect_install_method(dist) == "local (non-editable)"


def test_detect_install_method_malformed_direct_url_falls_back(monkeypatch):
    dist = FakeDistribution('{"dir_info": "bad"}')
    monkeypatch.setattr(self_cmd.sys, "prefix", "/home/user/.local/pipx/venvs/tool")
    monkeypatch.setattr(self_cmd.sys, "base_prefix", "/usr")

    assert self_cmd._detect_install_method(dist) == "pipx"


def test_parse_pypi_response_requires_version():
    with pytest.raises(ValueError, match="info.version"):
        self_cmd._parse_pypi_response(b'{"info": {}}')


def test_check_latest_version_expected_network_failure_returns_none(monkeypatch):
    def fail_urlopen(url: str, timeout: int):
        raise urllib.error.URLError("offline")

    monkeypatch.setattr(urllib.request, "urlopen", fail_urlopen)

    assert self_cmd.check_latest_version() is None
