import subprocess
import os
import pytest
from dotenv import load_dotenv


def run_cli_cmd(args):
    """Run CLI command via uv run."""
    import os

    env = os.environ.copy()
    # Ensure current directory is in PYTHONPATH if needed, but uv run handles package installation
    result = subprocess.run(
        ["uv", "run", "langsmith-cli"] + args, capture_output=True, text=True, env=env
    )
    return result


def test_projects_list_e2e():
    """E2E test for projects list if API KEY is available."""
    load_dotenv()
    if not os.getenv("LANGSMITH_API_KEY"):
        pytest.skip("LANGSMITH_API_KEY not set")

    result = run_cli_cmd(["projects", "list"])
    assert result.returncode == 0
    assert "Projects" in result.stdout


def test_projects_list_json_e2e():
    """E2E test for JSON output."""
    load_dotenv()
    if not os.getenv("LANGSMITH_API_KEY"):
        pytest.skip("LANGSMITH_API_KEY not set")

    result = run_cli_cmd(["--json", "projects", "list"])
    assert result.returncode == 0
    import json

    data = json.loads(result.stdout)
    assert isinstance(data, list)


def test_runs_list_e2e():
    """E2E test for runs list."""
    load_dotenv()
    if not os.getenv("LANGSMITH_API_KEY"):
        pytest.skip("LANGSMITH_API_KEY not set")

    result = run_cli_cmd(["runs", "list", "--limit", "1"])
    assert result.returncode == 0
    assert "Runs" in result.stdout or "No runs found" in result.stdout
