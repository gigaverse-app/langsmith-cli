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
from conftest import create_prompt, strip_ansi
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


def test_prompts_list_with_exclude(runner):
    """INVARIANT: --exclude should filter out prompts by name substring."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        p1 = create_prompt(
            repo_handle="keep-prompt",
            full_name="owner/keep-prompt",
            owner="owner",
        )
        p2 = create_prompt(
            repo_handle="exclude-prompt",
            full_name="test/exclude-prompt",
            owner="test",
        )

        mock_result = ListPromptsResponse(repos=[p1, p2], total=2)
        mock_client.list_prompts.return_value = mock_result

        # Exclude uses substring matching on full_name
        result = runner.invoke(cli, ["--json", "prompts", "list", "--exclude", "test/"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data) == 1
        assert data[0]["full_name"] == "owner/keep-prompt"


def test_prompts_list_with_count(runner):
    """INVARIANT: --count should output only the count of prompts."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        prompts = [
            create_prompt(
                repo_handle=f"prompt-{i}",
                full_name=f"owner/prompt-{i}",
                owner="owner",
            )
            for i in range(5)
        ]

        mock_result = ListPromptsResponse(repos=prompts, total=5)
        mock_client.list_prompts.return_value = mock_result

        result = runner.invoke(cli, ["--json", "prompts", "list", "--count"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data == 5


def test_prompts_list_with_output_file(runner, tmp_path):
    """INVARIANT: --output should write prompts to a JSONL file."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        p1 = create_prompt(
            repo_handle="prompt-1",
            full_name="owner/prompt-1",
            owner="owner",
        )
        p2 = create_prompt(
            repo_handle="prompt-2",
            full_name="owner/prompt-2",
            owner="owner",
        )

        mock_result = ListPromptsResponse(repos=[p1, p2], total=2)
        mock_client.list_prompts.return_value = mock_result

        output_file = tmp_path / "prompts.jsonl"
        result = runner.invoke(cli, ["prompts", "list", "--output", str(output_file)])
        assert result.exit_code == 0

        # Verify file was written
        assert output_file.exists()
        lines = output_file.read_text().strip().split("\n")
        assert len(lines) == 2
        data1 = json.loads(lines[0])
        assert data1["full_name"] == "owner/prompt-1"


def test_prompts_get_json(runner):
    """INVARIANT: prompts get with --json should return valid JSON."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        # Mock pull_prompt to return a prompt object with to_json method
        class MockPromptObj:
            def to_json(self):
                return {"template": "Hello, {name}!", "input_variables": ["name"]}

        mock_client.pull_prompt.return_value = MockPromptObj()

        result = runner.invoke(cli, ["--json", "prompts", "get", "my-prompt"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["template"] == "Hello, {name}!"


def test_prompts_get_table_output(runner):
    """INVARIANT: prompts get without --json should show formatted output."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        # Mock pull_prompt to return a prompt object
        class MockPromptObj:
            def __str__(self):
                return "Hello, {name}!"

        mock_client.pull_prompt.return_value = MockPromptObj()

        result = runner.invoke(cli, ["prompts", "get", "my-prompt"])
        assert result.exit_code == 0
        output = strip_ansi(result.output)
        assert "my-prompt" in output
        assert "Hello, {name}!" in output


def test_prompts_get_with_commit(runner):
    """INVARIANT: --commit should append to prompt name for versioning."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        class MockPromptObj:
            def to_json(self):
                return {"template": "Hello, {name}!"}

        mock_client.pull_prompt.return_value = MockPromptObj()

        result = runner.invoke(
            cli, ["--json", "prompts", "get", "my-prompt", "--commit", "v1.0"]
        )
        assert result.exit_code == 0
        mock_client.pull_prompt.assert_called_once_with("my-prompt:v1.0")


def test_prompts_get_fallback_to_string(runner):
    """INVARIANT: prompts get should fallback to string if no to_json method."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        # Mock pull_prompt to return a prompt object without to_json
        class MockPromptObj:
            def __str__(self):
                return "Hello, world!"

        mock_client.pull_prompt.return_value = MockPromptObj()

        result = runner.invoke(cli, ["--json", "prompts", "get", "my-prompt"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["prompt"] == "Hello, world!"


def test_prompts_push(runner, tmp_path):
    """INVARIANT: prompts push should upload a prompt file."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        # Create a prompt file
        prompt_file = tmp_path / "my_prompt.txt"
        prompt_file.write_text("Hello, {name}! Welcome to {place}.")

        result = runner.invoke(
            cli,
            [
                "prompts",
                "push",
                "my-prompt",
                str(prompt_file),
                "--description",
                "A greeting prompt",
            ],
        )
        assert result.exit_code == 0
        mock_client.push_prompt.assert_called_once()
        call_kwargs = mock_client.push_prompt.call_args[1]
        assert call_kwargs["prompt_identifier"] == "my-prompt"
        assert call_kwargs["object"] == "Hello, {name}! Welcome to {place}."
        assert call_kwargs["description"] == "A greeting prompt"


def test_prompts_push_with_tags(runner, tmp_path):
    """INVARIANT: --tags should be parsed and passed to SDK."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        prompt_file = tmp_path / "my_prompt.txt"
        prompt_file.write_text("Test prompt")

        result = runner.invoke(
            cli,
            [
                "prompts",
                "push",
                "my-prompt",
                str(prompt_file),
                "--tags",
                "production,greeting",
            ],
        )
        assert result.exit_code == 0
        mock_client.push_prompt.assert_called_once()
        call_kwargs = mock_client.push_prompt.call_args[1]
        assert call_kwargs["tags"] == ["production", "greeting"]


def test_prompts_push_public(runner, tmp_path):
    """INVARIANT: --is-public should set prompt visibility."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        prompt_file = tmp_path / "my_prompt.txt"
        prompt_file.write_text("Test prompt")

        result = runner.invoke(
            cli,
            [
                "prompts",
                "push",
                "my-prompt",
                str(prompt_file),
                "--is-public",
                "true",
            ],
        )
        assert result.exit_code == 0
        mock_client.push_prompt.assert_called_once()
        call_kwargs = mock_client.push_prompt.call_args[1]
        assert call_kwargs["is_public"] is True
