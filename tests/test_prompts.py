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
from conftest import (
    create_prompt,
    create_prompt_commit,
    create_listed_prompt_commit,
    strip_ansi,
)
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
    """INVARIANT: prompts get --json returns a single dict (not a list) with prompt data."""
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
        assert isinstance(data, dict), "prompts get should return a dict, not a list"
        assert data["template"] == "Hello, {name}!"
        assert data["input_variables"] == ["name"]


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


def test_prompts_get_with_fields(runner):
    """INVARIANT: --fields should limit returned fields in prompts get output."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        class MockPromptObj:
            def to_json(self):
                return {
                    "template": "Hello, {name}!",
                    "input_variables": ["name"],
                    "metadata": {"version": "1.0"},
                }

        mock_client.pull_prompt.return_value = MockPromptObj()

        result = runner.invoke(
            cli,
            ["--json", "prompts", "get", "my-prompt", "--fields", "template"],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, dict)
        assert "template" in data
        assert data["template"] == "Hello, {name}!"
        # Fields not requested should be absent
        assert "input_variables" not in data
        assert "metadata" not in data


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


def test_prompts_get_with_output(runner, tmp_path):
    """INVARIANT: prompts get --output writes a single JSON object to file."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        class MockPromptObj:
            def to_json(self):
                return {"template": "Hello, {name}!", "input_variables": ["name"]}

        mock_client.pull_prompt.return_value = MockPromptObj()

        output_file = str(tmp_path / "prompt.json")
        result = runner.invoke(
            cli,
            ["--json", "prompts", "get", "my-prompt", "--output", output_file],
        )
        assert result.exit_code == 0
        with open(output_file) as f:
            data = json.load(f)
        assert isinstance(data, dict)
        assert data["template"] == "Hello, {name}!"


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


def test_prompts_push_json_mode_outputs_json_confirmation(runner, tmp_path):
    """Invariant: --json mode push outputs JSON confirmation, not empty stdout."""
    with patch("langsmith.Client") as MockClient:
        MockClient.return_value  # Client is needed but return value isn't used directly

        prompt_file = tmp_path / "my_prompt.txt"
        prompt_file.write_text("Hello, {name}!")

        result = runner.invoke(
            cli,
            ["--json", "prompts", "push", "my-prompt", str(prompt_file)],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["status"] == "success"
        assert data["name"] == "my-prompt"


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


# ===== prompts pull tests =====


def test_prompts_pull_json(runner):
    """INVARIANT: prompts pull --json returns PromptCommit data with manifest."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        commit = create_prompt_commit(
            owner="my-org",
            repo="greeting",
            commit_hash="abc123",
            manifest={"type": "prompt", "template": "Hello, {name}!"},
        )
        mock_client.pull_prompt_commit.return_value = commit

        result = runner.invoke(cli, ["--json", "prompts", "pull", "greeting"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["owner"] == "my-org"
        assert data["repo"] == "greeting"
        assert data["commit_hash"] == "abc123"
        assert data["manifest"]["template"] == "Hello, {name}!"


def test_prompts_pull_with_commit_version(runner):
    """INVARIANT: --commit should be appended to the prompt identifier."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        commit = create_prompt_commit()
        mock_client.pull_prompt_commit.return_value = commit

        result = runner.invoke(
            cli, ["--json", "prompts", "pull", "my-prompt", "--commit", "v1.0"]
        )
        assert result.exit_code == 0
        mock_client.pull_prompt_commit.assert_called_once_with(
            "my-prompt:v1.0", include_model=False
        )


def test_prompts_pull_with_include_model(runner):
    """INVARIANT: --include-model should be passed to SDK."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        commit = create_prompt_commit()
        mock_client.pull_prompt_commit.return_value = commit

        result = runner.invoke(
            cli, ["--json", "prompts", "pull", "my-prompt", "--include-model"]
        )
        assert result.exit_code == 0
        mock_client.pull_prompt_commit.assert_called_once_with(
            "my-prompt", include_model=True
        )


def test_prompts_pull_with_fields(runner):
    """INVARIANT: --fields should filter output to only requested fields."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        commit = create_prompt_commit()
        mock_client.pull_prompt_commit.return_value = commit

        result = runner.invoke(
            cli,
            ["--json", "prompts", "pull", "my-prompt", "--fields", "commit_hash,manifest"],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "commit_hash" in data
        assert "manifest" in data
        assert "owner" not in data


def test_prompts_pull_table_output(runner):
    """INVARIANT: prompts pull without --json should show formatted output."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        commit = create_prompt_commit(owner="my-org", repo="greeting", commit_hash="abc123")
        mock_client.pull_prompt_commit.return_value = commit

        result = runner.invoke(cli, ["prompts", "pull", "greeting"])
        assert result.exit_code == 0
        output = strip_ansi(result.output)
        assert "my-org" in output
        assert "greeting" in output
        assert "abc123" in output


def test_prompts_pull_with_output_file(runner, tmp_path):
    """INVARIANT: --output should write commit data to file."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        commit = create_prompt_commit()
        mock_client.pull_prompt_commit.return_value = commit

        output_file = str(tmp_path / "commit.json")
        result = runner.invoke(
            cli,
            ["--json", "prompts", "pull", "my-prompt", "--output", output_file],
        )
        assert result.exit_code == 0
        with open(output_file) as f:
            data = json.load(f)
        assert data["commit_hash"] == "abc123def456"


# ===== prompts delete tests =====


def test_prompts_delete_json(runner):
    """INVARIANT: prompts delete with --confirm should delete and return success."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        result = runner.invoke(
            cli, ["--json", "prompts", "delete", "my-prompt", "--confirm"]
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["status"] == "success"
        assert data["name"] == "my-prompt"
        mock_client.delete_prompt.assert_called_once_with("my-prompt")


def test_prompts_delete_table_output(runner):
    """INVARIANT: prompts delete should show success message."""
    with patch("langsmith.Client") as MockClient:
        MockClient.return_value

        result = runner.invoke(
            cli, ["prompts", "delete", "my-prompt", "--confirm"]
        )
        assert result.exit_code == 0
        output = strip_ansi(result.output)
        assert "Deleted" in output
        assert "my-prompt" in output


def test_prompts_delete_not_found(runner):
    """INVARIANT: Deleting non-existent prompt should handle gracefully."""
    from langsmith.utils import LangSmithNotFoundError

    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        mock_client.delete_prompt.side_effect = LangSmithNotFoundError("Not found")

        result = runner.invoke(
            cli, ["--json", "prompts", "delete", "missing", "--confirm"]
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["status"] == "error"
        assert "not found" in data["message"]


def test_prompts_delete_requires_confirmation(runner):
    """INVARIANT: Without --confirm, delete should prompt for confirmation."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        # Simulate user saying 'n' to confirmation
        result = runner.invoke(
            cli, ["prompts", "delete", "my-prompt"], input="n\n"
        )
        # Should abort
        assert result.exit_code != 0
        mock_client.delete_prompt.assert_not_called()


# ===== prompts create tests =====


def test_prompts_create_json(runner):
    """INVARIANT: prompts create --json should return prompt data."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        prompt = create_prompt(
            repo_handle="new-prompt",
            full_name="owner/new-prompt",
            owner="owner",
        )
        mock_client.create_prompt.return_value = prompt

        result = runner.invoke(cli, ["--json", "prompts", "create", "new-prompt"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["full_name"] == "owner/new-prompt"
        mock_client.create_prompt.assert_called_once()


def test_prompts_create_with_options(runner):
    """INVARIANT: --description, --tags, --is-public should be passed to SDK."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        prompt = create_prompt(
            repo_handle="new-prompt",
            full_name="owner/new-prompt",
            owner="owner",
            description="A test prompt",
        )
        mock_client.create_prompt.return_value = prompt

        result = runner.invoke(
            cli,
            [
                "prompts",
                "create",
                "new-prompt",
                "--description",
                "A test prompt",
                "--tags",
                "prod,v2",
                "--is-public",
                "true",
            ],
        )
        assert result.exit_code == 0
        call_kwargs = mock_client.create_prompt.call_args[1]
        assert call_kwargs["description"] == "A test prompt"
        assert call_kwargs["tags"] == ["prod", "v2"]
        assert call_kwargs["is_public"] is True


def test_prompts_create_already_exists(runner):
    """INVARIANT: Creating existing prompt should handle gracefully."""
    from langsmith.utils import LangSmithConflictError

    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        mock_client.create_prompt.side_effect = LangSmithConflictError(
            "Prompt already exists"
        )

        result = runner.invoke(
            cli, ["--json", "prompts", "create", "existing"]
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["status"] == "error"
        assert "already exists" in data["message"]


# ===== prompts commits tests =====


def test_prompts_commits_json(runner):
    """INVARIANT: prompts commits --json should return list of commits."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        c1 = create_listed_prompt_commit(
            id_str="a9adf0cb-6238-453f-abab-f75361a39ea8",
            commit_hash="abc123",
        )
        c2 = create_listed_prompt_commit(
            id_str="b9bdf0cb-7348-563f-bcbc-a86471b50fb9",
            commit_hash="def456",
            parent_commit_hash="abc123",
        )

        mock_client.list_prompt_commits.return_value = iter([c1, c2])

        result = runner.invoke(cli, ["--json", "prompts", "commits", "my-prompt"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, list)
        assert len(data) == 2
        assert data[0]["commit_hash"] == "abc123"
        assert data[1]["commit_hash"] == "def456"


def test_prompts_commits_table_output(runner):
    """INVARIANT: prompts commits without --json should show formatted table."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        c1 = create_listed_prompt_commit(commit_hash="abc123")
        mock_client.list_prompt_commits.return_value = iter([c1])

        result = runner.invoke(cli, ["prompts", "commits", "my-prompt"])
        assert result.exit_code == 0
        output = strip_ansi(result.output)
        assert "abc123" in output
        assert "Commits" in output


def test_prompts_commits_with_limit(runner):
    """INVARIANT: --limit should be passed to SDK."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        mock_client.list_prompt_commits.return_value = iter([])

        result = runner.invoke(
            cli, ["prompts", "commits", "my-prompt", "--limit", "5"]
        )
        assert result.exit_code == 0
        call_kwargs = mock_client.list_prompt_commits.call_args[1]
        assert call_kwargs["limit"] == 5


def test_prompts_commits_with_count(runner):
    """INVARIANT: --count should output only the count."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        commits = [
            create_listed_prompt_commit(
                id_str=f"a9adf0cb-6238-453f-abab-f75361a39e{i:02d}",
                commit_hash=f"hash{i}",
            )
            for i in range(3)
        ]
        mock_client.list_prompt_commits.return_value = iter(commits)

        result = runner.invoke(
            cli, ["--json", "prompts", "commits", "my-prompt", "--count"]
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data == 3


def test_prompts_commits_with_output_file(runner, tmp_path):
    """INVARIANT: --output should write commits to file."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        c1 = create_listed_prompt_commit(commit_hash="abc123")
        mock_client.list_prompt_commits.return_value = iter([c1])

        output_file = tmp_path / "commits.jsonl"
        result = runner.invoke(
            cli,
            ["prompts", "commits", "my-prompt", "--output", str(output_file)],
        )
        assert result.exit_code == 0
        assert output_file.exists()
        data = json.loads(output_file.read_text().strip())
        assert data["commit_hash"] == "abc123"


def test_prompts_commits_empty(runner):
    """INVARIANT: Empty commits list should be handled gracefully."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        mock_client.list_prompt_commits.return_value = iter([])

        result = runner.invoke(cli, ["prompts", "commits", "my-prompt"])
        assert result.exit_code == 0
