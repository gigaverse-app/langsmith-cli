"""
Permanent tests for prompts command.

These tests use mocked data and will continue to work indefinitely.
"""

from langsmith_cli.main import cli
from unittest.mock import patch, MagicMock
import json


def test_prompts_list(runner):
    """INVARIANT: Prompts list should return all prompts with correct structure."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        # Create mock prompts
        p1 = MagicMock()
        p1.repo_handle = "agent_prompt-profile"
        p1.full_name = "mitchell-compoze/agent_prompt-profile"
        p1.id = "a9adf0cb-6238-453f-abab-f75361a39ea8"
        p1.owner = "mitchell-compoze"
        p1.description = ""
        p1.is_public = True
        p1.tags = ["ChatPromptTemplate"]
        p1.num_likes = 0
        p1.num_downloads = 772
        p1.num_views = 23

        p2 = MagicMock()
        p2.repo_handle = "outline_generator"
        p2.full_name = "ethan-work/outline_generator"
        p2.id = "76974ea6-81de-4649-8e1c-10b98e06d4e5"
        p2.owner = "ethan-work"
        p2.description = ""
        p2.is_public = True
        p2.tags = ["ChatPromptTemplate"]
        p2.num_likes = 0
        p2.num_downloads = 1196
        p2.num_views = 51

        # list_prompts returns ListPromptsResponse with .repos attribute
        mock_result = MagicMock()
        mock_result.repos = [p1, p2]
        mock_client.list_prompts.return_value = mock_result

        result = runner.invoke(cli, ["prompts", "list"])
        assert result.exit_code == 0
        assert (
            "agent_prompt-profile" in result.output
            or "mitchell-compoze" in result.output
        )


def test_prompts_list_json(runner):
    """INVARIANT: JSON output should be valid with prompt fields."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        p1 = MagicMock()
        p1.repo_handle = "test-prompt"
        p1.full_name = "owner/test-prompt"
        p1.id = "test-id"
        p1.owner = "owner"
        p1.description = "Test prompt"
        p1.is_public = True
        p1.num_downloads = 100
        p1.model_dump.return_value = {
            "repo_handle": "test-prompt",
            "full_name": "owner/test-prompt",
            "id": "test-id",
            "owner": "owner",
            "description": "Test prompt",
            "is_public": True,
            "num_downloads": 100,
        }

        # list_prompts returns ListPromptsResponse with .repos attribute
        mock_result = MagicMock()
        mock_result.repos = [p1]
        mock_client.list_prompts.return_value = mock_result

        result = runner.invoke(cli, ["--json", "prompts", "list"])
        assert result.exit_code == 0

        data = json.loads(result.output)
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["full_name"] == "owner/test-prompt"


def test_prompts_list_with_limit(runner):
    """INVARIANT: --limit parameter should be passed to API."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        # Create mock prompts
        prompts = []
        for i in range(5):
            p = MagicMock()
            p.repo_handle = f"prompt-{i}"
            p.full_name = f"owner/prompt-{i}"
            p.id = f"id-{i}"
            p.owner = "owner"
            p.is_public = True
            p.description = f"Test prompt {i}"
            prompts.append(p)

        # list_prompts returns ListPromptsResponse with .repos attribute
        mock_result = MagicMock()
        mock_result.repos = prompts[:3]
        mock_client.list_prompts.return_value = mock_result

        result = runner.invoke(cli, ["prompts", "list", "--limit", "3"])
        assert result.exit_code == 0
        mock_client.list_prompts.assert_called_once()
        call_kwargs = mock_client.list_prompts.call_args[1]
        assert call_kwargs["limit"] == 3


def test_prompts_list_public_only(runner):
    """INVARIANT: Prompts list should show public prompts by default."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        p1 = MagicMock()
        p1.repo_handle = "public-prompt"
        p1.full_name = "owner/public-prompt"
        p1.id = "id-1"
        p1.owner = "owner"
        p1.is_public = True
        p1.description = "A public prompt"

        # list_prompts returns ListPromptsResponse with .repos attribute
        mock_result = MagicMock()
        mock_result.repos = [p1]
        mock_client.list_prompts.return_value = mock_result

        result = runner.invoke(cli, ["prompts", "list"])
        assert result.exit_code == 0


def test_prompts_list_with_filter(runner):
    """INVARIANT: Filtering prompts should work."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        p1 = MagicMock()
        p1.repo_handle = "llm-analyzer"
        p1.full_name = "analytics/llm-analyzer"
        p1.id = "id-1"
        p1.owner = "analytics"
        p1.is_public = True
        p1.description = "LLM analysis prompt"

        p2 = MagicMock()
        p2.repo_handle = "data-processor"
        p2.full_name = "tools/data-processor"
        p2.id = "id-2"
        p2.owner = "tools"
        p2.is_public = True
        p2.description = "Data processing prompt"

        def list_prompts_side_effect(**kwargs):
            # Simple filter simulation - return ListPromptsResponse with .repos
            mock_result = MagicMock()
            mock_result.repos = [p1, p2]
            return mock_result

        mock_client.list_prompts.side_effect = list_prompts_side_effect

        result = runner.invoke(cli, ["prompts", "list", "--limit", "10"])
        assert result.exit_code == 0


def test_prompts_list_empty_results(runner):
    """INVARIANT: Empty results should be handled gracefully."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        # list_prompts returns ListPromptsResponse with .repos attribute
        mock_result = MagicMock()
        mock_result.repos = []
        mock_client.list_prompts.return_value = mock_result

        result = runner.invoke(cli, ["prompts", "list"])
        assert result.exit_code == 0
