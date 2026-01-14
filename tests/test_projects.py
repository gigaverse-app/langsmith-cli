from langsmith_cli.main import cli
from unittest.mock import patch, MagicMock


def test_projects_list(runner):
    """Test the projects list command."""
    with patch("langsmith.Client") as MockClient:
        # Setup mock return value
        mock_client = MockClient.return_value
        mock_project = MagicMock()
        mock_project.name = "default"
        mock_project.id = "123-abc"
        mock_project.run_count = 42
        mock_project.active = True
        mock_client.list_projects.return_value = [mock_project]

        result = runner.invoke(cli, ["projects", "list"])
        assert result.exit_code == 0
        assert "default" in result.output
        assert "123-abc" in result.output


def test_projects_create(runner):
    """Test the projects create command."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        mock_project = MagicMock()
        mock_project.name = "new-project"
        mock_project.id = "456-def"
        mock_client.create_project.return_value = mock_project

        result = runner.invoke(cli, ["projects", "create", "new-project"])
        assert result.exit_code == 0
        assert "Created project new-project" in result.output
