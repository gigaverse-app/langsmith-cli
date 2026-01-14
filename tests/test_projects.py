from langsmith_cli.main import cli
from unittest.mock import patch, MagicMock


def test_projects_list(runner):
    """Test the projects list command with multiple items."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        # Mock two projects
        p1 = MagicMock()
        p1.name = "proj-1"
        p1.id = "id-1"
        p1.run_count = 10
        p1.project_type = "tracer"

        p2 = MagicMock()
        p2.name = "proj-2"
        p2.id = "id-2"
        p2.run_count = None  # Test null handling
        p2.project_type = "eval"

        mock_client.list_projects.return_value = iter([p1, p2])

        result = runner.invoke(cli, ["projects", "list"])
        assert result.exit_code == 0
        assert "proj-1" in result.output
        assert "proj-2" in result.output
        assert "id-1" in result.output


def test_projects_list_json(runner):
    """Test projects list in JSON mode."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        p1 = MagicMock()
        p1.name = "proj-json"
        p1.id = "id-json"
        p1.model_dump.return_value = {"name": "proj-json", "id": "id-json"}
        mock_client.list_projects.return_value = iter([p1])

        result = runner.invoke(cli, ["--json", "projects", "list"])
        assert result.exit_code == 0
        import json

        data = json.loads(result.output)
        assert data[0]["name"] == "proj-json"


def test_projects_create(runner):
    """Test the projects create command."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        mock_project = MagicMock()
        mock_project.name = "created-proj"
        mock_project.id = "created-id"
        mock_client.create_project.return_value = mock_project

        result = runner.invoke(cli, ["projects", "create", "created-proj"])
        assert result.exit_code == 0
        assert "Created project created-proj" in result.output


def test_projects_list_with_name_pattern(runner):
    """Test projects list with --name-pattern filter."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        p1 = MagicMock()
        p1.name = "prod-api-v1"
        p1.id = "1"

        p2 = MagicMock()
        p2.name = "prod-web-v1"
        p2.id = "2"

        p3 = MagicMock()
        p3.name = "staging-api"
        p3.id = "3"

        mock_client.list_projects.return_value = iter([p1, p2, p3])

        # Filter with pattern "*prod*"
        result = runner.invoke(cli, ["projects", "list", "--name-pattern", "*prod*"])

        assert result.exit_code == 0
        # Should match p1 and p2, but not p3
        assert "prod-api-v1" in result.output
        assert "prod-web-v1" in result.output
        assert "staging-api" not in result.output


def test_projects_list_with_name_regex(runner):
    """Test projects list with --name-regex filter."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        p1 = MagicMock()
        p1.name = "prod-api-v1"
        p1.id = "1"

        p2 = MagicMock()
        p2.name = "prod-api-v2"
        p2.id = "2"

        p3 = MagicMock()
        p3.name = "staging-api"
        p3.id = "3"

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
    """Test projects list with --has-runs filter."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        p1 = MagicMock()
        p1.name = "active-project"
        p1.id = "1"
        p1.run_count = 100

        p2 = MagicMock()
        p2.name = "empty-project"
        p2.id = "2"
        p2.run_count = 0

        p3 = MagicMock()
        p3.name = "another-active"
        p3.id = "3"
        p3.run_count = 50

        mock_client.list_projects.return_value = iter([p1, p2, p3])

        # Filter with --has-runs
        result = runner.invoke(cli, ["projects", "list", "--has-runs"])

        assert result.exit_code == 0
        # Should match p1 and p3, but not p2
        assert "active-project" in result.output
        assert "another-active" in result.output
        assert "empty-project" not in result.output


def test_projects_list_with_sort_by_name(runner):
    """Test projects list with --sort-by name."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        p1 = MagicMock()
        p1.name = "zebra-project"
        p1.id = "1"

        p2 = MagicMock()
        p2.name = "alpha-project"
        p2.id = "2"

        p3 = MagicMock()
        p3.name = "beta-project"
        p3.id = "3"

        mock_client.list_projects.return_value = iter([p1, p2, p3])

        # Sort by name ascending
        result = runner.invoke(cli, ["projects", "list", "--sort-by", "name"])

        assert result.exit_code == 0
        # Check order in output (alpha should appear before zebra)
        alpha_pos = result.output.find("alpha-project")
        zebra_pos = result.output.find("zebra-project")
        assert alpha_pos < zebra_pos


def test_projects_list_with_sort_by_run_count_desc(runner):
    """Test projects list with --sort-by run_count descending."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        p1 = MagicMock()
        p1.name = "low-activity"
        p1.id = "1"
        p1.run_count = 10

        p2 = MagicMock()
        p2.name = "high-activity"
        p2.id = "2"
        p2.run_count = 1000

        mock_client.list_projects.return_value = iter([p1, p2])

        # Sort by run_count descending
        result = runner.invoke(cli, ["projects", "list", "--sort-by", "-run_count"])

        assert result.exit_code == 0
        # High activity should appear before low activity
        high_pos = result.output.find("high-activity")
        low_pos = result.output.find("low-activity")
        assert high_pos < low_pos


def test_projects_list_with_csv_format(runner):
    """Test projects list with CSV export."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        p1 = MagicMock()
        p1.name = "test-project"
        p1.id = "123"
        p1.model_dump.return_value = {"name": "test-project", "id": "123"}

        mock_client.list_projects.return_value = iter([p1])

        result = runner.invoke(cli, ["projects", "list", "--format", "csv"])

        assert result.exit_code == 0
        # CSV should have header and data
        assert "name,id" in result.output
        assert "test-project,123" in result.output


def test_projects_list_with_yaml_format(runner):
    """Test projects list with YAML export."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        p1 = MagicMock()
        p1.name = "test-project"
        p1.id = "123"
        p1.model_dump.return_value = {"name": "test-project", "id": "123"}

        mock_client.list_projects.return_value = iter([p1])

        result = runner.invoke(cli, ["projects", "list", "--format", "yaml"])

        assert result.exit_code == 0
        # YAML should contain the data
        assert "name: test-project" in result.output
        assert "id:" in result.output


def test_projects_list_with_empty_results(runner):
    """Test projects list with no results."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        mock_client.list_projects.return_value = iter([])

        result = runner.invoke(cli, ["projects", "list"])

        assert result.exit_code == 0
        assert "No projects found" in result.output


def test_projects_list_with_invalid_regex(runner):
    """Test that invalid regex raises error."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        p1 = MagicMock()
        p1.name = "test"
        p1.id = "1"
        mock_client.list_projects.return_value = iter([p1])

        # Invalid regex pattern
        result = runner.invoke(cli, ["projects", "list", "--name-regex", "[invalid("])
        assert result.exit_code != 0
        assert "Invalid regex pattern" in result.output


def test_projects_create_already_exists(runner):
    """Test creating a project that already exists."""
    from langsmith.utils import LangSmithConflictError

    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        mock_client.create_project.side_effect = LangSmithConflictError(
            "Project already exists"
        )

        result = runner.invoke(cli, ["projects", "create", "existing-proj"])

        assert result.exit_code == 0
        assert "already exists" in result.output
