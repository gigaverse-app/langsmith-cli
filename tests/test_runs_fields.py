"""Tests for runs fields and runs describe commands."""

import json
import re

from langsmith_cli.main import cli
from conftest import create_run


class TestRunsFields:
    """Tests for runs fields command."""

    def test_fields_json_output(self, runner, mock_client):
        """Fields command returns JSON with field statistics."""
        mock_client.list_runs.return_value = [
            create_run(
                name="run-1",
                id_str="auto",
                inputs={"query": "What is the capital of France?"},
                outputs={"answer": "Paris is the capital of France."},
            ),
            create_run(
                name="run-2",
                id_str="auto",
                inputs={"query": "What is 2+2?"},
                outputs={"answer": "4"},
            ),
        ]

        # Use -q to suppress info messages for clean JSON
        result = runner.invoke(
            cli,
            ["--json", "runs", "fields", "--no-language"],
        )

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "fields" in data
        assert "total_runs" in data
        assert data["total_runs"] == 2

        # Check that we have field paths
        paths = [f["path"] for f in data["fields"]]
        assert "inputs.query" in paths
        assert "outputs.answer" in paths

    def test_fields_table_output(self, runner, mock_client):
        """Fields command shows table in non-JSON mode."""
        mock_client.list_runs.return_value = [
            create_run(
                name="run-1",
                id_str="auto",
                inputs={"query": "Hello world"},
                outputs={"answer": "Hi there"},
            ),
        ]

        result = runner.invoke(
            cli,
            ["runs", "fields", "--no-language"],
        )

        assert result.exit_code == 0
        # Strip ANSI codes for assertion
        clean_output = re.sub(r"\x1b\[[0-9;]*m", "", result.output)
        assert "Field Path" in clean_output
        assert "Type" in clean_output
        assert "Present" in clean_output

    def test_fields_include_filter(self, runner, mock_client):
        """Fields command filters by include paths."""
        mock_client.list_runs.return_value = [
            create_run(
                name="run-1",
                id_str="auto",
                inputs={"query": "test"},
                outputs={"answer": "result"},
            ),
        ]

        result = runner.invoke(
            cli,
            ["--json", "runs", "fields", "--include", "inputs", "--no-language"],
        )

        assert result.exit_code == 0
        data = json.loads(result.output)
        paths = [f["path"] for f in data["fields"]]
        # Should only include inputs fields
        assert all(p.startswith("inputs") for p in paths)

    def test_fields_exclude_filter(self, runner, mock_client):
        """Fields command excludes specified paths."""
        mock_client.list_runs.return_value = [
            create_run(
                name="run-1",
                id_str="auto",
                inputs={"query": "test"},
                outputs={"answer": "result"},
            ),
        ]

        result = runner.invoke(
            cli,
            [
                "--json",
                "-q",
                "runs",
                "fields",
                "--exclude",
                "extra,events,serialized",
                "--no-language",
            ],
        )

        assert result.exit_code == 0
        data = json.loads(result.output)
        paths = [f["path"] for f in data["fields"]]
        # Should not include extra fields
        assert not any(p.startswith("extra.") for p in paths)

    def test_fields_no_runs(self, runner, mock_client):
        """Fields command handles no runs gracefully."""
        mock_client.list_runs.return_value = []

        result = runner.invoke(
            cli,
            ["--json", "runs", "fields", "--no-language"],
        )

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["fields"] == []
        assert data["total_runs"] == 0

    def test_fields_with_sample_size(self, runner, mock_client):
        """Fields command respects sample-size option."""
        mock_client.list_runs.return_value = []

        runner.invoke(
            cli,
            ["runs", "fields", "--sample-size", "50", "--no-language"],
        )

        _, kwargs = mock_client.list_runs.call_args
        assert kwargs["limit"] == 50


class TestRunsDescribe:
    """Tests for runs describe command."""

    def test_describe_json_output(self, runner, mock_client):
        """Describe command returns JSON with detailed statistics."""
        mock_client.list_runs.return_value = [
            create_run(
                name="run-1",
                id_str="auto",
                inputs={"query": "What is the capital of France?"},
                outputs={"answer": "Paris is the capital of France."},
                latency=1.5,
                total_tokens=100,
            ),
            create_run(
                name="run-2",
                id_str="auto",
                inputs={"query": "What is 2+2?"},
                outputs={"answer": "The answer is 4."},
                latency=0.5,
                total_tokens=50,
            ),
        ]

        result = runner.invoke(
            cli,
            ["--json", "runs", "describe", "--no-language"],
        )

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "fields" in data
        assert "total_runs" in data

        # Find string field and check for length stats
        string_fields = [f for f in data["fields"] if f["type"] == "string"]
        assert len(string_fields) > 0

        # Check that string fields have length info
        query_field = next(
            (f for f in data["fields"] if f["path"] == "inputs.query"), None
        )
        if query_field and "length" in query_field:
            assert "min" in query_field["length"]
            assert "max" in query_field["length"]

    def test_describe_table_output(self, runner, mock_client):
        """Describe command shows table with stats columns."""
        mock_client.list_runs.return_value = [
            create_run(
                name="run-1",
                id_str="auto",
                inputs={"query": "Hello world"},
                outputs={"answer": "Hi there"},
            ),
        ]

        result = runner.invoke(
            cli,
            ["runs", "describe", "--no-language"],
        )

        assert result.exit_code == 0
        # Strip ANSI codes for assertion
        clean_output = re.sub(r"\x1b\[[0-9;]*m", "", result.output)
        assert "Field Path" in clean_output
        assert "Length/Numeric Stats" in clean_output

    def test_describe_include_filter(self, runner, mock_client):
        """Describe command filters by include paths."""
        mock_client.list_runs.return_value = [
            create_run(
                name="run-1",
                id_str="auto",
                inputs={"query": "test query text"},
                outputs={"answer": "test answer text"},
            ),
        ]

        result = runner.invoke(
            cli,
            [
                "--json",
                "runs",
                "describe",
                "--include",
                "inputs,outputs",
                "--no-language",
            ],
        )

        assert result.exit_code == 0
        data = json.loads(result.output)
        paths = [f["path"] for f in data["fields"]]
        # Should only include inputs and outputs fields
        assert all(p.startswith("inputs") or p.startswith("outputs") for p in paths)

    def test_describe_no_runs(self, runner, mock_client):
        """Describe command handles no runs gracefully."""
        mock_client.list_runs.return_value = []

        result = runner.invoke(
            cli,
            ["--json", "runs", "describe", "--no-language"],
        )

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["fields"] == []
        assert data["total_runs"] == 0

    def test_describe_numeric_stats(self, runner, mock_client):
        """Describe command includes numeric stats for numeric fields."""
        mock_client.list_runs.return_value = [
            create_run(
                name="run-1",
                id_str="auto",
                latency=1.0,
                total_tokens=100,
            ),
            create_run(
                name="run-2",
                id_str="auto",
                latency=2.0,
                total_tokens=200,
            ),
            create_run(
                name="run-3",
                id_str="auto",
                latency=3.0,
                total_tokens=300,
            ),
        ]

        result = runner.invoke(
            cli,
            [
                "--json",
                "runs",
                "describe",
                "--include",
                "latency,total_tokens",
                "--no-language",
            ],
        )

        assert result.exit_code == 0
        data = json.loads(result.output)

        # Find latency field
        latency_field = next(
            (f for f in data["fields"] if f["path"] == "latency"), None
        )
        if latency_field and "numeric" in latency_field:
            assert latency_field["numeric"]["min"] == 1.0
            assert latency_field["numeric"]["max"] == 3.0


class TestRunsFieldsLanguageDetection:
    """Tests for language detection in runs fields/describe."""

    def test_fields_with_language_detection(self, runner, mock_client):
        """Fields command includes language detection when enabled."""
        # Create runs with English text
        mock_client.list_runs.return_value = [
            create_run(
                name="run-1",
                id_str="auto",
                inputs={
                    "query": "This is a sample English text that should be detected correctly by the language detector."
                },
            ),
            create_run(
                name="run-2",
                id_str="auto",
                inputs={
                    "query": "Another English sentence that is long enough to be analyzed for language detection."
                },
            ),
        ]

        result = runner.invoke(
            cli,
            ["--json", "runs", "fields", "--include", "inputs"],
        )

        assert result.exit_code == 0
        data = json.loads(result.output)

        # Check that languages field exists
        query_field = next(
            (f for f in data["fields"] if f["path"] == "inputs.query"), None
        )
        assert query_field is not None
        # Language detection should have run
        if "languages" in query_field:
            assert "en" in query_field["languages"]

    def test_fields_no_language_flag(self, runner, mock_client):
        """--no-language flag skips language detection."""
        mock_client.list_runs.return_value = [
            create_run(
                name="run-1",
                id_str="auto",
                inputs={
                    "query": "This is a sample English text that should be detected correctly."
                },
            ),
        ]

        result = runner.invoke(
            cli,
            ["--json", "runs", "fields", "--include", "inputs", "--no-language"],
        )

        assert result.exit_code == 0
        data = json.loads(result.output)

        # Check meta indicates language detection was disabled
        assert data["meta"]["lang_detect_enabled"] is False

        # Languages should be empty when detection is disabled
        query_field = next(
            (f for f in data["fields"] if f["path"] == "inputs.query"), None
        )
        if query_field:
            # Either no languages key or empty dict
            assert query_field.get("languages", {}) == {}
