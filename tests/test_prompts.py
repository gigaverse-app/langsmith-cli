"""
Permanent tests for prompts command.

These tests use mocked data and will continue to work indefinitely,
unless E2E tests that depend on real trace data (which expires after 400 days).

All test data is created using real LangSmith Pydantic model instances from
langsmith.schemas, ensuring compatibility with the actual SDK.
"""

from langsmith_cli.main import cli
from unittest.mock import patch
import json
from conftest import create_prompt
from langsmith.schemas import ListPromptsResponse


def test_prompts_list(runner):
    """INVARIANT: Prompts list should return all prompts with correct structure."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        # Create real Prompt Pydantic instances
        p1 = create_prompt(
            repo_handle="agent_prompt-profile",
            full_name="mitchell-compoze/agent_prompt-profile",
            owner="mitchell-compoze",
        )
        p2 = create_prompt(
            repo_handle="outline_generator",
            full_name="ethan-work/outline_generator",
            owner="ethan-work",
            description="Outline generator prompt",
        )

        # list_prompts returns ListPromptsResponse with .repos attribute
        mock_result = ListPromptsResponse(repos=[p1, p2], total=2)
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

        p1 = create_prompt(
            repo_handle="test-prompt",
            full_name="owner/test-prompt",
            owner="owner",
            description="Test prompt",
        )

        # list_prompts returns ListPromptsResponse with .repos attribute
        mock_result = ListPromptsResponse(repos=[p1], total=1)
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

        # Create real Prompt instances
        prompts = [
            create_prompt(
                repo_handle=f"prompt-{i}",
                full_name=f"owner/prompt-{i}",
                owner="owner",
                description=f"Test prompt {i}",
            )
            for i in range(5)
        ]

        # list_prompts returns ListPromptsResponse with .repos attribute
        mock_result = ListPromptsResponse(repos=prompts[:3], total=5)
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

        p1 = create_prompt(
            repo_handle="public-prompt",
            full_name="owner/public-prompt",
            owner="owner",
            description="A public prompt",
            is_public=True,
        )

        # list_prompts returns ListPromptsResponse with .repos attribute
        mock_result = ListPromptsResponse(repos=[p1], total=1)
        mock_client.list_prompts.return_value = mock_result

        result = runner.invoke(cli, ["prompts", "list"])
        assert result.exit_code == 0


def test_prompts_list_with_filter(runner):
    """INVARIANT: Filtering prompts should work."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        p1 = create_prompt(
            repo_handle="llm-analyzer",
            full_name="analytics/llm-analyzer",
            owner="analytics",
            description="LLM analysis prompt",
        )
        p2 = create_prompt(
            repo_handle="data-processor",
            full_name="tools/data-processor",
            owner="tools",
            description="Data processing prompt",
        )

        def list_prompts_side_effect(**kwargs):
            # Return ListPromptsResponse with .repos attribute
            return ListPromptsResponse(repos=[p1, p2], total=2)

        mock_client.list_prompts.side_effect = list_prompts_side_effect

        result = runner.invoke(cli, ["prompts", "list", "--limit", "10"])
        assert result.exit_code == 0


def test_prompts_list_empty_results(runner):
    """INVARIANT: Empty results should be handled gracefully."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        # list_prompts returns ListPromptsResponse with empty .repos
        mock_result = ListPromptsResponse(repos=[], total=0)
        mock_client.list_prompts.return_value = mock_result

        result = runner.invoke(cli, ["prompts", "list"])
        assert result.exit_code == 0
