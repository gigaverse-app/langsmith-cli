from langsmith_cli.main import cli
from unittest.mock import patch
from conftest import create_project, strip_ansi
import json
import pytest
from langsmith.schemas import TracerSession


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


@pytest.mark.parametrize(
    "filter_type,filter_value,projects_data,should_match,should_not_match",
    [
        (
            "--name-pattern",
            "*prod*",
            ["prod-api-v1", "prod-web-v1", "staging-api"],
            ["prod-api-v1", "prod-web-v1"],
            ["staging-api"],
        ),
        (
            "--name-regex",
            "^prod-.*-v[0-9]+",
            ["prod-api-v1", "prod-api-v2", "staging-api"],
            ["prod-api-v1", "prod-api-v2"],
            ["staging-api"],
        ),
    ],
)
def test_projects_list_with_name_filter(
    runner, filter_type, filter_value, projects_data, should_match, should_not_match
):
    """INVARIANT: Name filters should correctly match/exclude projects."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        # Create projects from test data
        projects = [create_project(name=name) for name in projects_data]
        mock_client.list_projects.return_value = iter(projects)

        # Apply filter
        result = runner.invoke(cli, ["projects", "list", filter_type, filter_value])

        assert result.exit_code == 0

        # Verify matches
        for name in should_match:
            assert name in result.output, f"Expected '{name}' to match filter"

        # Verify exclusions
        for name in should_not_match:
            assert name not in result.output, f"Expected '{name}' to NOT match filter"


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


@pytest.mark.parametrize(
    "sort_field,projects_data,first_expected,last_expected",
    [
        (
            "name",
            [
                ("zebra-project", 0),
                ("alpha-project", 0),
                ("beta-project", 0),
            ],
            "alpha-project",
            "zebra-project",
        ),
        (
            "-run_count",
            [
                ("low-activity", 10),
                ("high-activity", 1000),
            ],
            "high-activity",
            "low-activity",
        ),
    ],
)
def test_projects_list_with_sort_by(
    runner, sort_field, projects_data, first_expected, last_expected
):
    """INVARIANT: --sort-by should sort projects correctly."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        # Create projects from test data
        projects = [
            create_project(name=name, run_count=count) for name, count in projects_data
        ]
        mock_client.list_projects.return_value = iter(projects)

        result = runner.invoke(cli, ["projects", "list", "--sort-by", sort_field])

        assert result.exit_code == 0
        # Check order in output
        first_pos = result.output.find(first_expected)
        last_pos = result.output.find(last_expected)
        assert first_pos < last_pos, (
            f"Expected {first_expected} to appear before {last_expected}"
        )


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


def test_projects_list_limit_zero_returns_all(runner):
    """INVARIANT: --limit 0 should return all projects without limit."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        # Create many projects to test pagination
        projects = [create_project(name=f"proj-{i}") for i in range(250)]
        mock_client.list_projects.return_value = iter(projects)

        result = runner.invoke(cli, ["--json", "projects", "list", "--limit", "0"])
        assert result.exit_code == 0

        data = json.loads(result.output)
        assert len(data) == 250, "limit=0 should return all 250 projects"

        # Verify all project names are present
        names = {p["name"] for p in data}
        assert all(f"proj-{i}" in names for i in range(250))

        # Verify API was called with limit=None (fetch all)
        call_kwargs = mock_client.list_projects.call_args[1]
        assert call_kwargs.get("limit") is None, (
            "API should be called with limit=None for pagination"
        )


def test_projects_list_displays_metadata_columns(runner):
    """INVARIANT: Projects table should display run count, last run, error rate, and cost."""
    from datetime import datetime, timezone, timedelta

    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        # Create project with rich metadata
        recent_time = datetime.now(timezone.utc) - timedelta(hours=2)
        p1 = create_project(
            name="prod-api",
            run_count=150,
            last_run_start_time=recent_time,
            error_rate=0.05,  # 5% error rate
            total_cost=0.1234,
        )

        mock_client.list_projects.return_value = iter([p1])

        result = runner.invoke(cli, ["projects", "list"])
        assert result.exit_code == 0

        # Verify metadata columns are present
        assert "Runs" in result.output
        assert "Last Run" in result.output
        assert "Error Rate" in result.output
        assert "Cost" in result.output

        # Verify data is displayed
        assert "150" in result.output  # Run count
        assert "5.0%" in result.output  # Error rate
        assert "$0.1234" in result.output  # Cost
        assert "2h ago" in result.output  # Last run (relative time)


@pytest.mark.parametrize(
    "total_projects,limit,json_mode,should_show_message",
    [
        (50, 10, False, True),  # Limit hit, table mode -> show message
        (5, 10, False, False),  # Limit not hit -> no message
        (50, 10, True, False),  # Limit hit, JSON mode -> no message
    ],
)
def test_projects_list_limit_message(
    runner, total_projects, limit, json_mode, should_show_message
):
    """INVARIANT: Limit message should appear only when limit is hit and not in JSON mode."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        # Create projects
        projects = [create_project(name=f"proj-{i}") for i in range(total_projects)]
        mock_client.list_projects.return_value = iter(projects)

        # Build command
        cmd = ["projects", "list", "--limit", str(limit)]
        if json_mode:
            cmd = ["--json"] + cmd

        result = runner.invoke(cli, cmd)
        assert result.exit_code == 0

        output = strip_ansi(result.output)
        if should_show_message:
            # Verify the limit message is shown with exact count
            shown = min(limit, total_projects)
            assert f"Showing {shown} of {total_projects} projects" in output
            assert f"Use --limit 0 to see all {total_projects} projects" in output
        else:
            # Verify the limit message is NOT shown
            assert "Showing" not in output or "Projects" in output  # Allow table title
            assert "Use --limit 0" not in output


@pytest.mark.parametrize(
    "total_projects,explicit_limit,expected_count",
    [
        (150, None, 150),  # No explicit limit -> count all
        (150, 50, 50),  # Explicit limit -> respect it
    ],
)
def test_projects_list_count(runner, total_projects, explicit_limit, expected_count):
    """INVARIANT: --count should default to unlimited, but respect explicit --limit."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        # Create projects (more than default limit of 100)
        projects = [create_project(name=f"proj-{i}") for i in range(total_projects)]
        mock_client.list_projects.return_value = iter(projects)

        # Build command
        cmd = ["projects", "list", "--count"]
        if explicit_limit is not None:
            cmd.extend(["--limit", str(explicit_limit)])

        result = runner.invoke(cli, cmd)
        assert result.exit_code == 0

        # Verify count
        assert result.output.strip() == str(expected_count)


# ===== projects get tests =====


def test_projects_get_by_name_json(runner):
    """INVARIANT: projects get by name should return project details."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        project = create_project(name="my-project", run_count=42)
        mock_client.read_project.return_value = project

        result = runner.invoke(cli, ["--json", "projects", "get", "my-project"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["name"] == "my-project"
        assert data["run_count"] == 42


def test_projects_get_by_name_table(runner):
    """INVARIANT: projects get without --json should show formatted details."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        project = create_project(name="my-project", run_count=42, error_rate=0.05)
        mock_client.read_project.return_value = project

        result = runner.invoke(cli, ["projects", "get", "my-project"])
        assert result.exit_code == 0
        output = strip_ansi(result.output)
        assert "my-project" in output
        assert "42" in output
        assert "5.0%" in output


def test_projects_get_falls_back_to_id(runner):
    """INVARIANT: projects get with UUID should resolve by ID directly."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        project = create_project(name="found-by-id")
        # UUID auto-detected → read_project(project_id=...) called directly
        mock_client.read_project.return_value = project

        result = runner.invoke(
            cli,
            ["--json", "projects", "get", "f47ac10b-58cc-4372-a567-0e02b2c3d479"],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["name"] == "found-by-id"

        # UUID detected → only one call with project_id
        mock_client.read_project.assert_called_once_with(
            project_id="f47ac10b-58cc-4372-a567-0e02b2c3d479", include_stats=True
        )


def test_projects_get_not_found(runner):
    """INVARIANT: Non-existent project should raise error."""
    from langsmith.utils import LangSmithNotFoundError

    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        mock_client.read_project.side_effect = LangSmithNotFoundError("Not found")

        result = runner.invoke(cli, ["projects", "get", "nonexistent"])
        assert result.exit_code != 0
        assert "not found" in result.output.lower()


def test_projects_get_not_found_langsmith_error(runner):
    """INVARIANT: LangSmithError (e.g. LangSmithUserError for invalid UUID) should produce friendly error."""
    from langsmith.utils import LangSmithError

    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        # SDK raises LangSmithUserError (subclass of LangSmithError) when
        # project_id is not a valid UUID. The first call (by name) raises
        # LangSmithNotFoundError, the fallback (by ID) raises LangSmithError.
        from langsmith.utils import LangSmithNotFoundError

        mock_client.read_project.side_effect = [
            LangSmithNotFoundError("Not found"),
            LangSmithError("project_id must be a valid UUID"),
        ]

        result = runner.invoke(cli, ["projects", "get", "nonexistent"])
        assert result.exit_code != 0
        assert "not found" in result.output.lower()


def test_projects_get_with_fields(runner):
    """INVARIANT: --fields should limit returned fields."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        project = create_project(name="my-project", run_count=42)
        mock_client.read_project.return_value = project

        result = runner.invoke(
            cli,
            ["--json", "projects", "get", "my-project", "--fields", "name,id"],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "name" in data
        assert "id" in data
        assert "run_count" not in data


def test_projects_get_with_output_file(runner, tmp_path):
    """INVARIANT: --output should write project data to file."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        project = create_project(name="my-project")
        mock_client.read_project.return_value = project

        output_file = str(tmp_path / "project.json")
        result = runner.invoke(
            cli,
            ["--json", "projects", "get", "my-project", "--output", output_file],
        )
        assert result.exit_code == 0
        with open(output_file) as f:
            data = json.load(f)
        assert data["name"] == "my-project"


# ===== projects update tests =====


def _create_tracer_session(name="updated-project"):
    """Create a TracerSession for update_project return value."""
    from uuid import UUID
    from datetime import datetime, timezone

    return TracerSession(
        id=UUID("f47ac10b-58cc-4372-a567-0e02b2c3d479"),
        name=name,
        tenant_id=UUID("00000000-0000-0000-0000-000000000000"),
        reference_dataset_id=None,
        start_time=datetime(2024, 7, 3, 9, 27, 16, tzinfo=timezone.utc),
    )


def test_projects_update_name_json(runner):
    """INVARIANT: projects update --name should rename and return JSON."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        project = create_project(name="old-name")
        mock_client.read_project.return_value = project

        updated = _create_tracer_session(name="new-name")
        mock_client.update_project.return_value = updated

        result = runner.invoke(
            cli,
            ["--json", "projects", "update", "old-name", "--name", "new-name"],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["name"] == "new-name"
        mock_client.update_project.assert_called_once()


def test_projects_update_description(runner):
    """INVARIANT: projects update --description should update description."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        project = create_project(name="my-project")
        mock_client.read_project.return_value = project

        updated = _create_tracer_session(name="my-project")
        mock_client.update_project.return_value = updated

        result = runner.invoke(
            cli,
            [
                "projects",
                "update",
                "my-project",
                "--description",
                "New description",
            ],
        )
        assert result.exit_code == 0
        call_kwargs = mock_client.update_project.call_args[1]
        assert call_kwargs["description"] == "New description"


def test_projects_update_requires_at_least_one_option(runner):
    """INVARIANT: projects update without --name or --description should error."""
    with patch("langsmith.Client"):
        result = runner.invoke(cli, ["projects", "update", "my-project"])
        assert result.exit_code != 0
        assert "required" in result.output.lower() or "at least" in result.output.lower()


def test_projects_update_not_found(runner):
    """INVARIANT: Updating non-existent project should error."""
    from langsmith.utils import LangSmithNotFoundError

    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        mock_client.read_project.side_effect = LangSmithNotFoundError("Not found")

        result = runner.invoke(
            cli, ["projects", "update", "nonexistent", "--name", "new"]
        )
        assert result.exit_code != 0
        assert "not found" in result.output.lower()


def test_projects_update_table_output(runner):
    """INVARIANT: projects update without --json should show success message."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        project = create_project(name="my-project")
        mock_client.read_project.return_value = project

        updated = _create_tracer_session(name="renamed")
        mock_client.update_project.return_value = updated

        result = runner.invoke(
            cli, ["projects", "update", "my-project", "--name", "renamed"]
        )
        assert result.exit_code == 0
        output = strip_ansi(result.output)
        assert "Updated" in output
        assert "renamed" in output


# ===== projects delete tests =====


def test_projects_delete_with_confirm_json(runner):
    """INVARIANT: projects delete --confirm should resolve then delete by ID."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        project = create_project(name="my-project")
        mock_client.read_project.return_value = project

        result = runner.invoke(
            cli, ["--json", "projects", "delete", "my-project", "--confirm"]
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["status"] == "success"
        mock_client.delete_project.assert_called_once_with(project_id=str(project.id))


def test_projects_delete_table_output(runner):
    """INVARIANT: projects delete should show success message."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        project = create_project(name="my-project")
        mock_client.read_project.return_value = project

        result = runner.invoke(
            cli, ["projects", "delete", "my-project", "--confirm"]
        )
        assert result.exit_code == 0
        output = strip_ansi(result.output)
        assert "Deleted" in output
        assert "my-project" in output


def test_projects_delete_not_found(runner):
    """INVARIANT: Deleting non-existent project should raise error."""
    from langsmith.utils import LangSmithNotFoundError

    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        # resolve_project fails: name lookup fails, ID fallback also fails
        mock_client.read_project.side_effect = LangSmithNotFoundError("Not found")

        result = runner.invoke(
            cli, ["projects", "delete", "missing", "--confirm"]
        )
        assert result.exit_code != 0
        assert "not found" in result.output.lower()


def test_projects_delete_requires_confirmation(runner):
    """INVARIANT: Without --confirm, delete should prompt for confirmation."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        project = create_project(name="my-project")
        mock_client.read_project.return_value = project

        result = runner.invoke(
            cli, ["projects", "delete", "my-project"], input="n\n"
        )
        assert result.exit_code != 0
        mock_client.delete_project.assert_not_called()


def test_projects_delete_falls_back_to_id(runner):
    """INVARIANT: Delete with UUID should resolve by ID directly."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        project = create_project(name="my-project")
        # UUID auto-detected → read_project(project_id=...) succeeds directly
        mock_client.read_project.return_value = project

        result = runner.invoke(
            cli,
            [
                "--json",
                "projects",
                "delete",
                "f47ac10b-58cc-4372-a567-0e02b2c3d479",
                "--confirm",
            ],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["status"] == "success"
        # resolve_project detects UUID, reads by ID, then deletes by ID
        mock_client.read_project.assert_called_once_with(
            project_id="f47ac10b-58cc-4372-a567-0e02b2c3d479", include_stats=False
        )
        mock_client.delete_project.assert_called_once_with(project_id=str(project.id))
