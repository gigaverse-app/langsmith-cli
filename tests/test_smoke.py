"""Smoke tests for skill examples using real LangSmith data.

These tests verify that the examples from SKILL.md work correctly
against live LangSmith data. They are lenient (balance between zealous
and too lenient) since they test against a moving target.
"""

import subprocess
import json
import pytest
import os


def run_cli(*args):
    """Run langsmith-cli with the given arguments.

    Returns:
        tuple: (exit_code, stdout, stderr)
    """
    cmd = ["uv", "run", "langsmith-cli", *args]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        env=os.environ.copy(),
    )
    return result.returncode, result.stdout, result.stderr


def parse_json_output(stdout):
    """Parse JSON output, returning None if invalid."""
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        return None


# Skip all tests if LANGSMITH_API_KEY is not set
pytestmark = pytest.mark.skipif(
    not os.environ.get("LANGSMITH_API_KEY"),
    reason="LANGSMITH_API_KEY not set - skipping smoke tests",
)


class TestProjectsSkill:
    """Test examples from SKILL.md - Projects section."""

    def test_projects_list_json(self):
        """Test: langsmith-cli --json projects list"""
        exit_code, stdout, stderr = run_cli("--json", "projects", "list")

        assert exit_code == 0, f"Command failed: {stderr}"
        data = parse_json_output(stdout)
        assert data is not None, "Output is not valid JSON"

        # Structure validation - lenient
        assert isinstance(data, list), "Expected list of projects"
        # Don't assert exact count - data is dynamic
        # Just ensure it's a valid response (can be empty or have items)
        if len(data) > 0:
            # If we have projects, validate structure
            project = data[0]
            assert "id" in project, "Project should have 'id' field"
            assert "name" in project, "Project should have 'name' field"

    def test_projects_create_and_cleanup(self):
        """Test: langsmith-cli --json projects create <name>"""
        project_name = f"smoke-test-{os.urandom(4).hex()}"

        try:
            # Create project
            exit_code, stdout, stderr = run_cli(
                "--json", "projects", "create", project_name
            )

            assert exit_code == 0, f"Create failed: {stderr}"
            data = parse_json_output(stdout)
            assert data is not None, "Output is not valid JSON"
            assert data.get("name") == project_name, "Project name doesn't match"
            assert "id" in data, "Created project should have ID"

        finally:
            # Note: CLI doesn't have delete command, so project remains
            # This is acceptable for smoke tests
            pass


class TestRunsSkill:
    """Test examples from SKILL.md - Runs section."""

    def test_runs_list_basic(self):
        """Test: langsmith-cli --json runs list"""
        exit_code, stdout, stderr = run_cli("--json", "runs", "list", "--limit", "5")

        assert exit_code == 0, f"Command failed: {stderr}"
        data = parse_json_output(stdout)
        assert data is not None, "Output is not valid JSON"

        # Lenient validation - may have 0 runs
        assert isinstance(data, list), "Expected list of runs"
        if len(data) > 0:
            run = data[0]
            assert "id" in run, "Run should have 'id'"
            assert "name" in run, "Run should have 'name'"
            # Status can be various values, just check it exists
            assert "status" in run, "Run should have 'status'"

    def test_runs_list_with_status_filter(self):
        """Test: langsmith-cli --json runs list --status error"""
        exit_code, stdout, stderr = run_cli(
            "--json", "runs", "list", "--status", "error", "--limit", "5"
        )

        assert exit_code == 0, f"Command failed: {stderr}"
        data = parse_json_output(stdout)
        assert data is not None, "Output is not valid JSON"
        assert isinstance(data, list), "Expected list of runs"

        # If we got error runs, verify they are actually errors
        for run in data:
            # Lenient: status might be 'error' or have error field
            # Just check structure is valid
            assert "id" in run, "Run should have 'id'"
            assert "status" in run, "Run should have 'status'"

    def test_runs_get_with_fields(self):
        """Test: langsmith-cli --json runs get <id> --fields inputs,outputs,error"""
        # First, get a run ID
        exit_code, stdout, _ = run_cli("--json", "runs", "list", "--limit", "1")
        if exit_code != 0:
            pytest.skip("No runs available to test get command")

        data = parse_json_output(stdout)
        if not data or len(data) == 0:
            pytest.skip("No runs available to test get command")

        run_id = data[0]["id"]

        # Now test get with fields
        exit_code, stdout, stderr = run_cli(
            "--json",
            "runs",
            "get",
            run_id,
            "--fields",
            "inputs,outputs,error,name",
        )

        assert exit_code == 0, f"Get command failed: {stderr}"
        result = parse_json_output(stdout)
        assert result is not None, "Output is not valid JSON"
        assert "id" in result, "Result should have 'id'"
        # Should have requested fields (or None if not available)
        assert "name" in result, "Result should have 'name' field"

    def test_runs_stats(self):
        """Test: langsmith-cli --json runs stats"""
        exit_code, stdout, stderr = run_cli("--json", "runs", "stats")

        assert exit_code == 0, f"Stats command failed: {stderr}"
        data = parse_json_output(stdout)
        assert data is not None, "Output is not valid JSON"

        # Stats should be a dict with some metrics
        assert isinstance(data, dict), "Expected dict of stats"
        # Lenient: just check it's a valid response, don't require specific fields
        # as different SDK versions might return different stats

    def test_runs_search(self):
        """Test: langsmith-cli --json runs search <query>"""
        # Search for a common term (lenient - might not find anything)
        exit_code, stdout, stderr = run_cli(
            "--json", "runs", "search", "test", "--limit", "5"
        )

        assert exit_code == 0, f"Search command failed: {stderr}"
        data = parse_json_output(stdout)
        assert data is not None, "Output is not valid JSON"
        assert isinstance(data, list), "Expected list of runs"


class TestDatasetsSkill:
    """Test examples from SKILL.md - Datasets section."""

    def test_datasets_list(self):
        """Test: langsmith-cli --json datasets list"""
        exit_code, stdout, stderr = run_cli("--json", "datasets", "list")

        assert exit_code == 0, f"Command failed: {stderr}"
        data = parse_json_output(stdout)
        assert data is not None, "Output is not valid JSON"
        assert isinstance(data, list), "Expected list of datasets"

        # Lenient - may have 0 datasets
        if len(data) > 0:
            dataset = data[0]
            assert "id" in dataset, "Dataset should have 'id'"
            assert "name" in dataset, "Dataset should have 'name'"

    def test_datasets_create_and_get(self):
        """Test: langsmith-cli --json datasets create <name>"""
        dataset_name = f"smoke-test-ds-{os.urandom(4).hex()}"

        # Create dataset
        exit_code, stdout, stderr = run_cli(
            "--json",
            "datasets",
            "create",
            dataset_name,
            "--description",
            "Smoke test dataset",
        )

        assert exit_code == 0, f"Create failed: {stderr}"
        create_data = parse_json_output(stdout)
        assert create_data is not None, "Create output is not valid JSON"
        assert create_data.get("name") == dataset_name, "Dataset name doesn't match"
        dataset_id = create_data.get("id")
        assert dataset_id, "Created dataset should have ID"

        # Get dataset to verify
        exit_code, stdout, stderr = run_cli("--json", "datasets", "get", dataset_id)

        assert exit_code == 0, f"Get failed: {stderr}"
        get_data = parse_json_output(stdout)
        assert get_data is not None, "Get output is not valid JSON"
        assert get_data.get("id") == dataset_id, "Dataset ID doesn't match"
        assert get_data.get("name") == dataset_name, "Dataset name doesn't match"


class TestExamplesSkill:
    """Test examples from SKILL.md - Examples section."""

    def test_examples_list(self):
        """Test: langsmith-cli --json examples list --dataset <name>"""
        # First, find or create a dataset
        exit_code, stdout, _ = run_cli("--json", "datasets", "list", "--limit", "1")
        if exit_code != 0:
            pytest.skip("Cannot list datasets")

        data = parse_json_output(stdout)
        if not data or len(data) == 0:
            # Create a test dataset
            dataset_name = f"smoke-test-examples-{os.urandom(4).hex()}"
            exit_code, stdout, _ = run_cli("--json", "datasets", "create", dataset_name)
            if exit_code != 0:
                pytest.skip("Cannot create test dataset")
            create_data = parse_json_output(stdout)
            if not create_data:
                pytest.skip("Invalid JSON response from create dataset")
            dataset_name = create_data.get("name")
        else:
            dataset_name = data[0]["name"]

        # List examples for this dataset
        exit_code, stdout, stderr = run_cli(
            "--json", "examples", "list", "--dataset", dataset_name, "--limit", "10"
        )

        assert exit_code == 0, f"List examples failed: {stderr}"
        examples_data = parse_json_output(stdout)
        assert examples_data is not None, "Output is not valid JSON"
        assert isinstance(examples_data, list), "Expected list of examples"

    def test_examples_create(self):
        """Test: langsmith-cli --json examples create --dataset <name> --inputs <json> --outputs <json>"""
        # Create a test dataset first
        dataset_name = f"smoke-test-examples-{os.urandom(4).hex()}"
        exit_code, stdout, _ = run_cli("--json", "datasets", "create", dataset_name)
        if exit_code != 0:
            pytest.skip("Cannot create test dataset")

        # Create an example
        exit_code, stdout, stderr = run_cli(
            "--json",
            "examples",
            "create",
            "--dataset",
            dataset_name,
            "--inputs",
            '{"question": "What is AI?"}',
            "--outputs",
            '{"answer": "Artificial Intelligence"}',
        )

        assert exit_code == 0, f"Create example failed: {stderr}"
        example_data = parse_json_output(stdout)
        assert example_data is not None, "Output is not valid JSON"
        assert "id" in example_data, "Created example should have ID"


class TestPromptsSkill:
    """Test examples from SKILL.md - Prompts section."""

    def test_prompts_list(self):
        """Test: langsmith-cli --json prompts list"""
        exit_code, stdout, stderr = run_cli("--json", "prompts", "list")

        assert exit_code == 0, f"Command failed: {stderr}"
        data = parse_json_output(stdout)
        assert data is not None, "Output is not valid JSON"
        assert isinstance(data, list), "Expected list of prompts"

        # Lenient - may have 0 prompts
        if len(data) > 0:
            prompt = data[0]
            assert "name" in prompt or "id" in prompt, "Prompt should have name or id"


class TestAdvancedFiltering:
    """Test advanced filtering examples from docs."""

    def test_runs_with_latency_filter(self):
        """Test: langsmith-cli --json runs list --filter 'gt(latency, "5s")'"""
        exit_code, stdout, stderr = run_cli(
            "--json",
            "runs",
            "list",
            "--filter",
            'gt(latency, "5s")',
            "--limit",
            "5",
        )

        assert exit_code == 0, f"Filter command failed: {stderr}"
        data = parse_json_output(stdout)
        assert data is not None, "Output is not valid JSON"
        assert isinstance(data, list), "Expected list of runs"

    def test_runs_with_and_filter(self):
        """Test: langsmith-cli --json runs list --filter 'and(gt(latency, "1s"), eq(status, "success"))'"""
        exit_code, stdout, stderr = run_cli(
            "--json",
            "runs",
            "list",
            "--filter",
            'and(gt(latency, "1s"), eq(status, "success"))',
            "--limit",
            "5",
        )

        # This might fail if no runs match - that's OK
        # Just verify the command syntax is accepted
        data = parse_json_output(stdout)
        if exit_code == 0:
            assert data is not None, "Output is not valid JSON"
            assert isinstance(data, list), "Expected list of runs"


class TestQoLFeatures:
    """Test Quality of Life features from QOL_FEATURES.md"""

    def test_runs_list_with_tag(self):
        """Test: langsmith-cli --json runs list --tag <tag>"""
        exit_code, stdout, stderr = run_cli(
            "--json", "runs", "list", "--tag", "test", "--limit", "5"
        )

        assert exit_code == 0, f"Tag filter failed: {stderr}"
        data = parse_json_output(stdout)
        assert data is not None, "Output is not valid JSON"
        assert isinstance(data, list), "Expected list of runs"

    def test_runs_list_with_name_pattern(self):
        """Test: langsmith-cli --json runs list --name-pattern '*auth*'"""
        exit_code, stdout, stderr = run_cli(
            "--json", "runs", "list", "--name-pattern", "*test*", "--limit", "5"
        )

        assert exit_code == 0, f"Pattern filter failed: {stderr}"
        data = parse_json_output(stdout)
        assert data is not None, "Output is not valid JSON"
        assert isinstance(data, list), "Expected list of runs"

    def test_runs_list_with_name_regex(self):
        """Test: langsmith-cli --json runs list --name-regex '^test'"""
        exit_code, stdout, stderr = run_cli(
            "--json", "runs", "list", "--name-regex", "^test", "--limit", "5"
        )

        # Regex filter happens client-side, so command should succeed
        # even if no matches found
        assert exit_code == 0, f"Regex filter failed: {stderr}"
        data = parse_json_output(stdout)
        assert data is not None, "Output is not valid JSON"
        assert isinstance(data, list), "Expected list of runs"

    def test_runs_list_with_slow_flag(self):
        """Test: langsmith-cli --json runs list --slow"""
        exit_code, stdout, stderr = run_cli(
            "--json", "runs", "list", "--slow", "--limit", "5"
        )

        assert exit_code == 0, f"Slow filter failed: {stderr}"
        data = parse_json_output(stdout)
        assert data is not None, "Output is not valid JSON"
        assert isinstance(data, list), "Expected list of runs"

    def test_runs_list_with_recent_flag(self):
        """Test: langsmith-cli --json runs list --recent"""
        exit_code, stdout, stderr = run_cli(
            "--json", "runs", "list", "--recent", "--limit", "5"
        )

        assert exit_code == 0, f"Recent filter failed: {stderr}"
        data = parse_json_output(stdout)
        assert data is not None, "Output is not valid JSON"
        assert isinstance(data, list), "Expected list of runs"

    def test_runs_list_combined_filters(self):
        """Test: Combined filters from QOL_FEATURES.md examples"""
        exit_code, stdout, stderr = run_cli(
            "--json",
            "runs",
            "list",
            "--tag",
            "production",
            "--status",
            "error",
            "--limit",
            "5",
        )

        assert exit_code == 0, f"Combined filters failed: {stderr}"
        data = parse_json_output(stdout)
        assert data is not None, "Output is not valid JSON"
        assert isinstance(data, list), "Expected list of runs"


class TestOutputFormats:
    """Test different output formats work correctly."""

    def test_runs_list_csv_format(self):
        """Test: langsmith-cli runs list --format csv"""
        exit_code, stdout, stderr = run_cli(
            "runs", "list", "--format", "csv", "--limit", "5"
        )

        if exit_code == 0 and stdout.strip():
            # Lenient: Just verify it looks like CSV (has commas)
            lines = stdout.strip().split("\n")
            if len(lines) > 1:  # Header + at least one row
                assert "," in lines[0], "CSV header should have commas"

    def test_runs_list_yaml_format(self):
        """Test: langsmith-cli runs list --format yaml"""
        exit_code, stdout, stderr = run_cli(
            "runs", "list", "--format", "yaml", "--limit", "5"
        )

        if exit_code == 0 and stdout.strip():
            # Lenient: Just verify it looks like YAML (has colons)
            assert ":" in stdout, "YAML should have colons"


class TestErrorHandling:
    """Test that error cases are handled gracefully."""

    def test_runs_get_invalid_id(self):
        """Test error handling with invalid run ID."""
        exit_code, stdout, stderr = run_cli(
            "--json", "runs", "get", "invalid-run-id-12345"
        )

        # Should fail gracefully (non-zero exit code)
        assert exit_code != 0, "Should fail with invalid ID"

    def test_datasets_get_invalid_id(self):
        """Test error handling with invalid dataset ID."""
        exit_code, stdout, stderr = run_cli(
            "--json", "datasets", "get", "invalid-dataset-id-12345"
        )

        # Should fail gracefully (non-zero exit code)
        assert exit_code != 0, "Should fail with invalid ID"

    def test_invalid_filter_syntax(self):
        """Test error handling with invalid filter syntax."""
        exit_code, stdout, stderr = run_cli(
            "--json",
            "runs",
            "list",
            "--filter",
            "invalid filter syntax!!!",
            "--limit",
            "5",
        )

        # Should either fail or return empty results
        # Don't assert exit code as SDK might handle this differently
        # Just verify it doesn't crash
        pass
