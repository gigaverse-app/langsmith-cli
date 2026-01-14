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
