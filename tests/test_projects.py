from langsmith_cli.main import cli
from unittest.mock import patch
from conftest import create_project
import json
import pytest


def test_projects_list(runner):
    """INVARIANT: Projects list should return all projects with correct structure."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        # Create real project instances
        p1 = create_project(name="proj-1", run_count=10)
        p2 = create_project(name="proj-2", run_count=0)

        mock_client.list_projects.return_value = iter([p1, p2])

        result = runner.invoke(cli, ["projects", "list"])
        assert result.exit_code == 0
        assert "proj-1" in result.output
        assert "proj-2" in result.output


def test_projects_list_json(runner):
    """INVARIANT: JSON output should be valid with project fields."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        p1 = create_project(name="proj-json")

        mock_client.list_projects.return_value = iter([p1])

        result = runner.invoke(cli, ["--json", "projects", "list"])
        assert result.exit_code == 0

        data = json.loads(result.output)
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["name"] == "proj-json"


def test_projects_create(runner):
    """INVARIANT: Create command should return success message."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        mock_project = create_project(name="created-proj")
        mock_client.create_project.return_value = mock_project

        result = runner.invoke(cli, ["projects", "create", "created-proj"])
        assert result.exit_code == 0
        assert "Created project created-proj" in result.output


def test_projects_list_with_name_pattern(runner):
    """INVARIANT: --name-pattern should filter by wildcard match."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        p1 = create_project(name="prod-api-v1")
        p2 = create_project(name="prod-web-v1")
        p3 = create_project(name="staging-api")

        mock_client.list_projects.return_value = iter([p1, p2, p3])

        # Filter with pattern "*prod*"
        result = runner.invoke(cli, ["projects", "list", "--name-pattern", "*prod*"])

        assert result.exit_code == 0
        # Should match p1 and p2, but not p3
        assert "prod-api-v1" in result.output
        assert "prod-web-v1" in result.output
        assert "staging-api" not in result.output


def test_projects_list_with_name_regex(runner):
    """INVARIANT: --name-regex should filter by regex pattern."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        p1 = create_project(name="prod-api-v1")
        p2 = create_project(name="prod-api-v2")
        p3 = create_project(name="staging-api")

        mock_client.list_projects.return_value = iter([p1, p2, p3])

        # Filter with regex "^prod-.*-v[0-9]+"
        result = runner.invoke(
            cli, ["projects", "list", "--name-regex", "^prod-.*-v[0-9]+"]
        )

        assert result.exit_code == 0
        # Should match p1 and p2, but not p3
        assert "prod-api-v1" in result.output
        assert "prod-api-v2" in result.output
        assert "staging-api" not in result.output


def test_projects_list_with_has_runs(runner):
    """INVARIANT: --has-runs should filter projects with run_count > 0."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        p1 = create_project(name="active-project", run_count=100)
        p2 = create_project(name="empty-project", run_count=0)
        p3 = create_project(name="another-active", run_count=50)

        mock_client.list_projects.return_value = iter([p1, p2, p3])

        # Filter with --has-runs
        result = runner.invoke(cli, ["projects", "list", "--has-runs"])

        assert result.exit_code == 0
        # Should match p1 and p3, but not p2
        assert "active-project" in result.output
        assert "another-active" in result.output
        assert "empty-project" not in result.output


def test_projects_list_with_sort_by_name(runner):
    """INVARIANT: --sort-by name should sort projects alphabetically."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        p1 = create_project(name="zebra-project")
        p2 = create_project(name="alpha-project")
        p3 = create_project(name="beta-project")

        mock_client.list_projects.return_value = iter([p1, p2, p3])

        # Sort by name ascending
        result = runner.invoke(cli, ["projects", "list", "--sort-by", "name"])

        assert result.exit_code == 0
        # Check order in output (alpha should appear before zebra)
        alpha_pos = result.output.find("alpha-project")
        zebra_pos = result.output.find("zebra-project")
        assert alpha_pos < zebra_pos


def test_projects_list_with_sort_by_run_count_desc(runner):
    """INVARIANT: --sort-by -run_count should sort by runs descending."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        p1 = create_project(name="low-activity", run_count=10)
        p2 = create_project(name="high-activity", run_count=1000)

        mock_client.list_projects.return_value = iter([p1, p2])

        # Sort by run_count descending
        result = runner.invoke(cli, ["projects", "list", "--sort-by", "-run_count"])

        assert result.exit_code == 0
        # High activity should appear before low activity
        high_pos = result.output.find("high-activity")
        low_pos = result.output.find("low-activity")
        assert high_pos < low_pos


@pytest.mark.parametrize(
    "format_type,expected_content",
    [
        ("csv", "test-project"),
        ("yaml", "name: test-project"),
    ],
)
def test_projects_list_with_format(runner, format_type, expected_content):
    """INVARIANT: Different formats should output data in the correct structure."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        p1 = create_project(name="test-project")

        mock_client.list_projects.return_value = iter([p1])

        result = runner.invoke(cli, ["projects", "list", "--format", format_type])

        assert result.exit_code == 0
        assert expected_content in result.output
        # CSV should have headers with name and id fields
        if format_type == "csv":
            assert "name" in result.output and "id" in result.output
            # Verify it's actually CSV format (has commas)
            assert "," in result.output


def test_projects_list_with_empty_results(runner):
    """INVARIANT: Empty results should show appropriate message."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        mock_client.list_projects.return_value = iter([])

        result = runner.invoke(cli, ["projects", "list"])

        assert result.exit_code == 0
        assert "No projects found" in result.output


def test_projects_list_with_invalid_regex(runner):
    """INVARIANT: Invalid regex should raise error."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        p1 = create_project(name="test")
        mock_client.list_projects.return_value = iter([p1])

        # Invalid regex pattern
        result = runner.invoke(cli, ["projects", "list", "--name-regex", "[invalid("])
        assert result.exit_code != 0
        assert "Invalid regex pattern" in result.output


def test_projects_create_already_exists(runner):
    """INVARIANT: Creating existing project should handle gracefully."""
    from langsmith.utils import LangSmithConflictError

    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        mock_client.create_project.side_effect = LangSmithConflictError(
            "Project already exists"
        )

        result = runner.invoke(cli, ["projects", "create", "existing-proj"])

        assert result.exit_code == 0
        assert "already exists" in result.output


def test_projects_list_name_regex_with_limit_optimizes_api_call(runner):
    """
    INVARIANT: When using --name-regex with --limit, the CLI should extract a search term
    from the regex and pass it to the API to optimize results.

    This test verifies that ".*moments.*" extracts "moments" and passes it to
    client.list_projects(name="moments", limit=3) rather than client.list_projects(limit=3).
    """
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        # Simulate API behavior: If name filter is provided, return matching projects
        # If no name filter, return first N projects (which might not match regex)
        def list_projects_side_effect(**kwargs):
            limit = kwargs.get("limit", 100)
            name_filter = kwargs.get("name_contains")

            if name_filter == "moments":
                # API returns projects matching "moments"
                p1 = create_project(name="dev/moments")
                p2 = create_project(name="local/moments")
                return iter([p1, p2])
            else:
                # API returns first N projects (none match "moments")
                projects = []
                for i in range(min(limit, 3)):
                    p = create_project(name=f"unrelated-project-{i}")
                    projects.append(p)
                return iter(projects)

        mock_client.list_projects.side_effect = list_projects_side_effect

        # Execute command with regex that should extract "moments"
        result = runner.invoke(
            cli, ["projects", "list", "--limit", "3", "--name-regex", ".*moments.*"]
        )

        assert result.exit_code == 0

        # INVARIANT: Should find the "moments" projects, not return empty
        # This will FAIL before the fix because API is called without name filter
        assert "dev/moments" in result.output or "local/moments" in result.output

        # Verify API was called with extracted search term
        mock_client.list_projects.assert_called_once()
        call_kwargs = mock_client.list_projects.call_args[1]
        assert call_kwargs.get("name_contains") == "moments", (
            f"Expected API to be called with name_contains='moments', "
            f"but got name_contains={call_kwargs.get('name_contains')}"
        )


def test_projects_list_name_pattern_with_limit_optimizes_api_call(runner):
    """
    INVARIANT: When using --name-pattern with --limit, the CLI should extract a search term
    from the wildcard pattern and pass it to the API.

    This verifies existing behavior for patterns like "*moments*".
    """
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        def list_projects_side_effect(**kwargs):
            name_filter = kwargs.get("name_contains")

            if name_filter == "moments":
                p1 = create_project(name="dev/moments")
                return iter([p1])
            else:
                return iter([])

        mock_client.list_projects.side_effect = list_projects_side_effect

        result = runner.invoke(
            cli, ["projects", "list", "--limit", "3", "--name-pattern", "*moments*"]
        )

        assert result.exit_code == 0
        assert "dev/moments" in result.output

        # Verify API was called with extracted search term
        call_kwargs = mock_client.list_projects.call_args[1]
        assert call_kwargs.get("name_contains") == "moments"


def test_projects_list_anchored_pattern_no_api_optimization(runner):
    """
    INVARIANT: Anchored wildcard patterns (*moments or moments*) should NOT use API optimization.

    Anchored patterns need client-side filtering for correctness. Only unanchored patterns
    (*moments*) can safely use API substring search optimization.
    """
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        # Create projects - some end with "moments", some don't
        p1 = create_project(name="dev/moments")
        p2 = create_project(name="dev/moments/runs")
        p3 = create_project(name="moments")

        mock_client.list_projects.return_value = iter([p1, p2, p3])

        # Test anchored pattern *moments (ends with)
        result = runner.invoke(
            cli, ["projects", "list", "--limit", "10", "--name-pattern", "*moments"]
        )

        assert result.exit_code == 0
        # Should match only projects ending with "moments"
        assert "dev/moments" in result.output
        assert "moments" in result.output
        assert "dev/moments/runs" not in result.output

        # Verify API was called WITHOUT name filter (no optimization)
        call_kwargs = mock_client.list_projects.call_args[1]
        assert call_kwargs.get("name_contains") is None


def test_projects_list_anchored_pattern_applies_limit_after_filtering(runner):
    """
    INVARIANT: When using anchored patterns, limit should be applied AFTER client-side filtering.

    This ensures that `--limit 3 --name-pattern "*moments"` returns 3 projects ending
    with "moments", not 0-2 projects (which would happen if limit was applied before filtering).
    """
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        # Create 10 projects: 5 end with "moments", 5 don't
        projects = []
        for i in range(5):
            p = create_project(name=f"project{i}/moments")
            projects.append(p)
        for i in range(5, 10):
            p = create_project(name=f"project{i}/other")
            projects.append(p)

        mock_client.list_projects.return_value = iter(projects)

        # Request limit=3 with anchored pattern
        result = runner.invoke(
            cli, ["projects", "list", "--limit", "3", "--name-pattern", "*moments"]
        )

        assert result.exit_code == 0
        # Should return exactly 3 projects (not 0-2)
        output_lines = [
            line for line in result.output.split("\n") if "/moments" in line
        ]
        assert len(output_lines) == 3, (
            f"Expected 3 matches, got {len(output_lines)}: {output_lines}"
        )

        # Verify API was called without limit (to allow client-side filtering)
        call_kwargs = mock_client.list_projects.call_args[1]
        assert call_kwargs.get("limit") is None, (
            "API should be called without limit when client-side filtering is needed"
        )


def test_projects_list_has_runs_filter_applies_limit_after_filtering(runner):
    """
    INVARIANT: --has-runs filter should apply limit AFTER filtering.

    This ensures that `--limit 3 --has-runs` returns 3 projects with runs,
    not fewer (which would happen if limit was applied before filtering).
    """
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        # Create 10 projects: 5 with runs, 5 without
        projects = []
        for i in range(5):
            p = create_project(name=f"active-project-{i}", run_count=100 + i)
            projects.append(p)
        for i in range(5, 10):
            p = create_project(name=f"empty-project-{i}", run_count=0)
            projects.append(p)

        mock_client.list_projects.return_value = iter(projects)

        # Request limit=3 with has-runs filter
        result = runner.invoke(cli, ["projects", "list", "--limit", "3", "--has-runs"])

        assert result.exit_code == 0
        # Should return exactly 3 projects with runs
        output_lines = [
            line for line in result.output.split("\n") if "active-project" in line
        ]
        assert len(output_lines) == 3, (
            f"Expected 3 active projects, got {len(output_lines)}"
        )

        # Verify API was called without limit
        call_kwargs = mock_client.list_projects.call_args[1]
        assert call_kwargs.get("limit") is None, (
            "API should be called without limit when --has-runs filter is used"
        )


def test_projects_list_complex_regex_extracts_best_search_term(runner):
    """
    INVARIANT: Complex regex patterns should extract the longest/best literal substring
    to optimize API filtering.

    Example: "^(dev|local)/.*moments.*-v[0-9]+" should extract "moments".
    """
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        def list_projects_side_effect(**kwargs):
            name_filter = kwargs.get("name_contains")

            # Accept any filter that contains "moments" substring
            if name_filter and "moments" in name_filter:
                p1 = create_project(name="dev/special-moments-v1")
                return iter([p1])
            else:
                return iter([])

        mock_client.list_projects.side_effect = list_projects_side_effect

        result = runner.invoke(
            cli,
            [
                "projects",
                "list",
                "--limit",
                "3",
                "--name-regex",
                "^(dev|local)/.*moments.*-v[0-9]+",
            ],
        )

        assert result.exit_code == 0
        assert "dev/special-moments-v1" in result.output

        # Verify API optimization occurred
        call_kwargs = mock_client.list_projects.call_args[1]
        assert call_kwargs.get("name_contains") is not None, (
            "API should be called with extracted search term for optimization"
        )
