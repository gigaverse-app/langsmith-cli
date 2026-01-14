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


def test_runs_list_with_tag_e2e():
    """E2E test for runs list with tag filtering."""
    load_dotenv()
    if not os.getenv("LANGSMITH_API_KEY"):
        pytest.skip("LANGSMITH_API_KEY not set")

    result = run_cli_cmd(["runs", "list", "--tag", "production", "--limit", "5"])
    assert result.returncode == 0
    # Should succeed even if no results
    assert "Runs" in result.stdout or "No runs found" in result.stdout


def test_runs_list_with_multiple_tags_e2e():
    """E2E test for runs list with multiple tags."""
    load_dotenv()
    if not os.getenv("LANGSMITH_API_KEY"):
        pytest.skip("LANGSMITH_API_KEY not set")

    result = run_cli_cmd(
        ["runs", "list", "--tag", "production", "--tag", "experimental", "--limit", "5"]
    )
    assert result.returncode == 0
    assert "Runs" in result.stdout or "No runs found" in result.stdout


def test_runs_list_with_name_pattern_e2e():
    """E2E test for runs list with name pattern."""
    load_dotenv()
    if not os.getenv("LANGSMITH_API_KEY"):
        pytest.skip("LANGSMITH_API_KEY not set")

    result = run_cli_cmd(["runs", "list", "--name-pattern", "*test*", "--limit", "5"])
    assert result.returncode == 0
    assert "Runs" in result.stdout or "No runs found" in result.stdout


def test_runs_list_with_slow_filter_e2e():
    """E2E test for runs list with --slow filter."""
    load_dotenv()
    if not os.getenv("LANGSMITH_API_KEY"):
        pytest.skip("LANGSMITH_API_KEY not set")

    result = run_cli_cmd(["runs", "list", "--slow", "--limit", "5"])
    assert result.returncode == 0
    assert "Runs" in result.stdout or "No runs found" in result.stdout


def test_runs_list_with_recent_filter_e2e():
    """E2E test for runs list with --recent filter."""
    load_dotenv()
    if not os.getenv("LANGSMITH_API_KEY"):
        pytest.skip("LANGSMITH_API_KEY not set")

    result = run_cli_cmd(["runs", "list", "--recent", "--limit", "5"])
    assert result.returncode == 0
    assert "Runs" in result.stdout or "No runs found" in result.stdout


def test_runs_list_with_today_filter_e2e():
    """E2E test for runs list with --today filter."""
    load_dotenv()
    if not os.getenv("LANGSMITH_API_KEY"):
        pytest.skip("LANGSMITH_API_KEY not set")

    result = run_cli_cmd(["runs", "list", "--today", "--limit", "5"])
    assert result.returncode == 0
    assert "Runs" in result.stdout or "No runs found" in result.stdout


def test_runs_list_with_combined_filters_e2e():
    """E2E test for runs list with multiple QoL filters combined."""
    load_dotenv()
    if not os.getenv("LANGSMITH_API_KEY"):
        pytest.skip("LANGSMITH_API_KEY not set")

    result = run_cli_cmd(
        ["runs", "list", "--recent", "--name-pattern", "*", "--limit", "5"]
    )
    assert result.returncode == 0
    assert "Runs" in result.stdout or "No runs found" in result.stdout


def test_runs_list_qol_with_json_output_e2e():
    """E2E test for QoL filters with JSON output."""
    load_dotenv()
    if not os.getenv("LANGSMITH_API_KEY"):
        pytest.skip("LANGSMITH_API_KEY not set")

    result = run_cli_cmd(["--json", "runs", "list", "--recent", "--limit", "2"])
    assert result.returncode == 0

    import json

    data = json.loads(result.stdout)
    assert isinstance(data, list)
    # Data can be empty, but should be valid JSON list
