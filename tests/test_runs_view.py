"""Tests for runs view-file command and Unicode/Hebrew preservation."""

import json

import pytest

from conftest import create_run, strip_ansi
from langsmith_cli.main import cli


class TestRunsViewFile:
    """Tests for runs view-file command."""

    def test_view_file_basic(self, runner, sample_runs_file):
        """Basic view-file command with single JSONL file."""
        result = runner.invoke(cli, ["runs", "view-file", str(sample_runs_file)])

        assert result.exit_code == 0
        assert "Test Run 1" in result.output
        assert "Test Run 2" in result.output
        assert "success" in result.output
        assert "error" in result.output
        assert "Loaded 2 runs from 1 file(s)" in strip_ansi(result.output)

    def test_view_file_glob_pattern(self, runner, tmp_path):
        """View-file with glob pattern matching multiple files."""
        for i in range(3):
            test_file = tmp_path / f"runs_{i}.jsonl"
            run = create_run(
                name=f"Run {i}", id_str=f"1234567{i}-1234-5678-1234-567812345678"
            )
            with open(test_file, "w") as f:
                f.write(json.dumps(run.model_dump(mode="json")) + "\n")

        result = runner.invoke(
            cli, ["runs", "view-file", str(tmp_path / "runs_*.jsonl")]
        )

        assert result.exit_code == 0
        assert "Run 0" in result.output
        assert "Run 1" in result.output
        assert "Run 2" in result.output
        assert "Loaded 3 runs from 3 file(s)" in strip_ansi(result.output)

    @pytest.mark.parametrize(
        "extra_args,expected_fields",
        [
            ([], None),  # All fields
            (["--fields", "id,name"], {"id", "name"}),
        ],
    )
    def test_view_file_output_modes(
        self, runner, sample_runs_file, extra_args, expected_fields
    ):
        """View-file with different output modes and field filtering."""
        result = runner.invoke(
            cli, ["--json", "runs", "view-file", str(sample_runs_file)] + extra_args
        )

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, list)
        assert len(data) == 2
        assert data[0]["name"] == "Test Run 1"
        if expected_fields:
            assert set(data[0].keys()) == expected_fields

    def test_view_file_no_truncate(self, runner, tmp_path):
        """View-file with --no-truncate flag."""
        test_file = tmp_path / "test_runs.jsonl"
        run = create_run(
            name="Very Long Run Name That Would Normally Be Truncated",
            run_type="llm",
            extra={
                "invocation_params": {
                    "model_name": "very-long-model-name-that-would-be-truncated-normally"
                }
            },
        )
        with open(test_file, "w") as f:
            f.write(json.dumps(run.model_dump(mode="json")) + "\n")

        result = runner.invoke(
            cli, ["runs", "view-file", str(test_file), "--no-truncate"]
        )

        assert result.exit_code == 0
        assert "very-long-model-name-that-would-be-trunca" in result.output

    @pytest.mark.parametrize(
        "file_content,expected_msg",
        [
            ("nonexistent.jsonl", "No files match pattern"),
            ('{"invalid json\n', "Invalid JSON"),
            ("", "No valid runs found"),
        ],
    )
    def test_view_file_error_handling(
        self, runner, tmp_path, file_content, expected_msg
    ):
        """View-file handles various error cases gracefully."""
        if file_content == "nonexistent.jsonl":
            result = runner.invoke(cli, ["runs", "view-file", file_content])
            assert result.exit_code != 0
        else:
            test_file = tmp_path / "test.jsonl"
            test_file.write_text(file_content)
            result = runner.invoke(cli, ["runs", "view-file", str(test_file)])
            assert result.exit_code == 0

        assert expected_msg in result.output


class TestUnicodePreservation:
    """Tests for Hebrew/Unicode preservation in JSON output."""

    def test_list_json_preserves_hebrew(self, runner, mock_client):
        """Runs list --json preserves Hebrew characters."""
        mock_client.list_runs.return_value = [
            create_run(
                name="תופים זה החיים",
                inputs={"stream_title": "תופים זה החיים", "language": "he-IL"},
                outputs={"message": "היי, מה קורה?"},
            )
        ]

        result = runner.invoke(cli, ["--json", "runs", "list", "--limit", "1"])

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data[0]["name"] == "תופים זה החיים"
        assert data[0]["inputs"]["stream_title"] == "תופים זה החיים"
        assert data[0]["outputs"]["message"] == "היי, מה קורה?"
        assert "\\u05" not in result.output  # Not escaped

    def test_get_json_preserves_hebrew(self, runner, mock_client):
        """Runs get --json preserves Hebrew characters."""
        mock_client.read_run.return_value = create_run(
            name="task-simulate-chat-chain",
            id_str="019bc1d8-ba71-7120-babf-1b41dfaeaa40",
            inputs={
                "stream_title": "תופים זה החיים",
                "transcripts": '- aviad: "היי, מה קורה?"',
            },
            outputs={
                "messages": [
                    {"message": "מה הקיק שאתה משתמש?", "user_name": "persona_alice"},
                    {"message": "אני מת לנסות גרוב", "user_name": "persona_bob"},
                ]
            },
        )

        result = runner.invoke(
            cli, ["--json", "runs", "get", "019bc1d8-ba71-7120-babf-1b41dfaeaa40"]
        )

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["inputs"]["stream_title"] == "תופים זה החיים"
        assert "היי, מה קורה?" in data["inputs"]["transcripts"]
        assert "\\u05" not in result.output

    def test_sample_file_output_preserves_hebrew(self, runner, mock_client, tmp_path):
        """Runs sample --output preserves Hebrew when writing to file."""
        mock_client.list_runs.return_value = iter(
            [
                create_run(
                    name="תופים זה החיים",
                    id_str="auto",
                    tags=["length:short"],
                    inputs={"text": "היי, מה קורה?"},
                    outputs={"response": "שלום!"},
                ),
                create_run(
                    name="רוק אנד רול",
                    id_str="auto",
                    tags=["length:short"],
                    inputs={"text": "אני אוהב מוזיקה"},
                    outputs={"response": "גם אני!"},
                ),
            ]
        )

        output_file = tmp_path / "hebrew_samples.jsonl"
        result = runner.invoke(
            cli,
            [
                "runs",
                "sample",
                "--stratify-by",
                "tag:length",
                "--values",
                "short",
                "--samples-per-stratum",
                "2",
                "--output",
                str(output_file),
            ],
        )

        assert result.exit_code == 0
        with open(output_file, "r", encoding="utf-8") as f:
            lines = f.readlines()
            assert len(lines) == 2
            data1 = json.loads(lines[0])
            assert data1["name"] == "תופים זה החיים"
            assert data1["inputs"]["text"] == "היי, מה קורה?"

        file_content = output_file.read_text(encoding="utf-8")
        assert "\\u05" not in file_content
        assert "תופים" in file_content

    def test_view_file_preserves_hebrew_round_trip(self, runner, tmp_path):
        """Complete round-trip: write file with Hebrew -> read with view-file."""
        test_file = tmp_path / "hebrew_runs.jsonl"
        run1 = create_run(
            name="תופים זה החיים",
            inputs={"text": "היי, מה קורה?"},
            outputs={"response": "שלום מישראל!"},
        )
        run2 = create_run(
            name="רוק אנד רול",
            id_str="auto",
            error="Test error",
            inputs={"text": "אני אוהב מוזיקה"},
            outputs={"response": "גם אני אוהב!"},
        )

        with open(test_file, "w", encoding="utf-8") as f:
            f.write(
                json.dumps(
                    run1.model_dump(mode="json"), ensure_ascii=False, default=str
                )
                + "\n"
            )
            f.write(
                json.dumps(
                    run2.model_dump(mode="json"), ensure_ascii=False, default=str
                )
                + "\n"
            )

        # Table output
        result = runner.invoke(cli, ["runs", "view-file", str(test_file)])
        assert result.exit_code == 0
        assert "תופים זה החיים" in result.output
        assert "רוק אנד רול" in result.output

        # JSON output
        result_json = runner.invoke(
            cli, ["--json", "runs", "view-file", str(test_file)]
        )
        assert result_json.exit_code == 0
        data = json.loads(result_json.output)
        assert data[0]["name"] == "תופים זה החיים"
        assert data[0]["inputs"]["text"] == "היי, מה קורה?"
        assert "\\u05" not in result_json.output

    def test_view_file_with_fields_preserves_hebrew(self, runner, tmp_path):
        """--fields selection still preserves Hebrew."""
        test_file = tmp_path / "hebrew_runs.jsonl"
        run1 = create_run(
            name="תופים זה החיים",
            inputs={"stream_title": "תוכנית תופים", "language": "he-IL"},
        )

        with open(test_file, "w", encoding="utf-8") as f:
            f.write(
                json.dumps(
                    run1.model_dump(mode="json"), ensure_ascii=False, default=str
                )
                + "\n"
            )

        result = runner.invoke(
            cli,
            [
                "--json",
                "runs",
                "view-file",
                str(test_file),
                "--fields",
                "id,name,inputs",
            ],
        )

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data[0]["name"] == "תופים זה החיים"
        assert data[0]["inputs"]["stream_title"] == "תוכנית תופים"
        assert "\\u05" not in result.output
