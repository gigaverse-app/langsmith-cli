"""Tests for runs get and get-latest commands."""

import json
import re
from unittest.mock import patch, MagicMock

import pytest

from conftest import create_run, create_project, strip_ansi
from langsmith_cli.main import cli


class TestRunsGet:
    """Tests for runs get command."""

    def test_get_json_output(self, runner, mock_client):
        """INVARIANT: runs get --json returns a single dict (not a list) with all run fields."""
        mock_client.read_run.return_value = create_run(
            name="Detailed Run",
            id_str="12345678-0000-0000-0000-000000000456",
            inputs={"q": "hello"},
            outputs={"a": "world"},
        )

        result = runner.invoke(
            cli, ["--json", "runs", "get", "12345678-0000-0000-0000-000000000456"]
        )

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, dict), "runs get should return a dict, not a list"
        assert data["id"] == "12345678-0000-0000-0000-000000000456"
        assert data["name"] == "Detailed Run"
        assert data["inputs"] == {"q": "hello"}
        assert data["outputs"] == {"a": "world"}

    def test_get_with_fields_pruning(self, runner, mock_client):
        """INVARIANT: --fields prunes output to only selected fields, result is still a dict."""
        mock_client.read_run.return_value = create_run(
            name="Full Run",
            id_str="12345678-0000-0000-0000-000000000789",
            inputs={"input": "foo"},
            outputs={"output": "bar"},
            extra={"heavy_field": "huge_data"},
        )

        result = runner.invoke(
            cli,
            [
                "--json",
                "runs",
                "get",
                "12345678-0000-0000-0000-000000000789",
                "--fields",
                "inputs",
            ],
        )

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, dict), (
            "runs get --fields should return a dict, not a list"
        )
        assert "inputs" in data
        assert data["inputs"] == {"input": "foo"}
        # Fields not requested should be absent
        assert "outputs" not in data
        assert "extra" not in data
        assert "name" not in data

    def test_get_rich_output(self, runner, mock_client):
        """Get command displays rich formatted output without --json."""
        mock_client.read_run.return_value = create_run(
            name="Rich Output Test",
            id_str="12345678-0000-0000-0000-000000000123",
            inputs={"query": "test"},
            outputs={"result": "success"},
        )

        result = runner.invoke(
            cli, ["runs", "get", "12345678-0000-0000-0000-000000000123"]
        )

        assert result.exit_code == 0
        clean_output = re.sub(r"\x1b\[[0-9;]*m", "", result.output)
        assert "12345678-0000-0000-0000-000000000123" in clean_output
        assert "Rich Output Test" in clean_output

    def test_get_with_complex_data_types(self, runner, mock_client):
        """Get command handles dict and list data types."""
        mock_client.read_run.return_value = create_run(
            name="Complex Data",
            metadata={"key": "value", "nested": {"deep": "data"}},
            tags=["tag1", "tag2"],
            extra={"simple_field": "simple_value"},
        )

        result = runner.invoke(
            cli, ["runs", "get", "12345678-1234-5678-1234-567812345678"]
        )

        assert result.exit_code == 0
        assert "tag1" in result.output or "tags" in result.output
        assert "simple_value" in result.output

    def test_get_with_fields_outputs_nested_null(self, runner, mock_client):
        """INVARIANT: --fields outputs with complex nested data (including null values) produces parseable JSON.

        This is the 'runs get <id> --fields outputs' use case where the outputs
        field contains nested dicts and null values. The stdout must contain ONLY
        valid JSON (no diagnostic text mixed in) so it can be piped to other tools.
        """
        mock_client.read_run.return_value = create_run(
            name="Entity Extraction Run",
            id_str="12345678-0000-0000-0000-000000000abc",
            outputs={
                "extracted_entities": [
                    {
                        "canonical_full_name": "Jia",
                        "details_for_llm_recognized_entities": None,
                        "entity_type": "Person",
                        "llm_recognition": False,
                    },
                    {
                        "canonical_full_name": "OpenAI",
                        "details_for_llm_recognized_entities": {
                            "one_sentence_relevant_additional_information": "AI company"
                        },
                        "entity_type": "Organization",
                        "llm_recognition": True,
                    },
                ]
            },
        )

        result = runner.invoke(
            cli,
            [
                "--json",
                "runs",
                "get",
                "12345678-0000-0000-0000-000000000abc",
                "--fields",
                "outputs",
            ],
        )

        assert result.exit_code == 0
        # INVARIANT: stdout must be parseable JSON with no diagnostic text mixed in
        data = json.loads(result.output)
        assert isinstance(data, dict)
        assert "outputs" in data
        assert "inputs" not in data
        assert "name" not in data

        entities = data["outputs"]["extracted_entities"]
        assert len(entities) == 2

        # Null nested dict is preserved as null (not coerced)
        jia = entities[0]
        assert jia["canonical_full_name"] == "Jia"
        assert jia["details_for_llm_recognized_entities"] is None

        # Non-null nested dict is preserved intact
        openai = entities[1]
        details = openai["details_for_llm_recognized_entities"]
        assert details is not None
        assert details["one_sentence_relevant_additional_information"] == "AI company"

    def test_get_with_output_writes_file(self, runner, mock_client, tmp_path):
        """INVARIANT: --output writes a single JSON object to file."""
        mock_client.read_run.return_value = create_run(
            name="File Output Run",
            id_str="12345678-0000-0000-0000-000000000456",
            inputs={"q": "hello"},
        )
        output_file = str(tmp_path / "run.json")

        result = runner.invoke(
            cli,
            [
                "--json",
                "runs",
                "get",
                "12345678-0000-0000-0000-000000000456",
                "--output",
                output_file,
            ],
        )

        assert result.exit_code == 0
        with open(output_file) as f:
            data = json.load(f)
        assert isinstance(data, dict)
        assert data["name"] == "File Output Run"
        assert data["inputs"] == {"q": "hello"}

    def test_get_with_output_and_fields(self, runner, mock_client, tmp_path):
        """INVARIANT: --output combined with --fields writes only selected fields."""
        mock_client.read_run.return_value = create_run(
            name="Pruned File Run",
            id_str="12345678-0000-0000-0000-000000000789",
            inputs={"input": "foo"},
            outputs={"output": "bar"},
        )
        output_file = str(tmp_path / "run_pruned.json")

        result = runner.invoke(
            cli,
            [
                "--json",
                "runs",
                "get",
                "12345678-0000-0000-0000-000000000789",
                "--fields",
                "inputs",
                "--output",
                output_file,
            ],
        )

        assert result.exit_code == 0
        with open(output_file) as f:
            data = json.load(f)
        assert isinstance(data, dict)
        assert "inputs" in data
        assert "outputs" not in data
        assert "name" not in data


class TestRunsGetLatest:
    """Tests for runs get-latest command."""

    def test_get_latest_basic(self, runner, mock_client):
        """Get-latest returns most recent run."""
        mock_client.list_runs.return_value = iter([create_run(name="Latest Run")])

        result = runner.invoke(cli, ["runs", "get-latest", "--project", "test"])

        assert result.exit_code == 0
        assert "Latest Run" in result.output

    def test_get_latest_with_project_id(self, runner, mock_client):
        """INVARIANT: --project-id passes project_id directly to SDK."""
        mock_client.list_runs.return_value = iter([create_run(name="ID Run")])

        result = runner.invoke(
            cli,
            [
                "--json",
                "runs",
                "get-latest",
                "--project-id",
                "8dc9fb82-ee48-4815-a0b0-c0fbabaa1887",
            ],
        )

        assert result.exit_code == 0
        call_kwargs = mock_client.list_runs.call_args[1]
        assert call_kwargs["project_id"] == "8dc9fb82-ee48-4815-a0b0-c0fbabaa1887"
        assert "project_name" not in call_kwargs

    def test_get_latest_json_output(self, runner, mock_client):
        """INVARIANT: runs get-latest --json returns a single dict (not a list)."""
        mock_client.list_runs.return_value = iter([create_run(name="Latest Run")])

        result = runner.invoke(
            cli, ["--json", "runs", "get-latest", "--project", "test"]
        )

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, dict), (
            "runs get-latest should return a dict, not a list"
        )
        assert data["name"] == "Latest Run"
        assert "id" in data

    def test_order_by_not_passed_to_api(self, runner, mock_client):
        """INVARIANT: order_by must NOT be passed to client.list_runs() — API rejects it with 400."""
        mock_client.list_runs.return_value = iter([create_run(name="Latest Run")])

        runner.invoke(cli, ["runs", "get-latest", "--project", "test"])

        call_kwargs = mock_client.list_runs.call_args[1]
        assert "order_by" not in call_kwargs, (
            "order_by should not be passed to list_runs — LangSmith API rejects it with 400 Bad Request"
        )

    def test_get_latest_with_fields(self, runner, mock_client):
        """Get-latest with --fields returns only selected fields."""
        mock_client.list_runs.return_value = iter(
            [
                create_run(
                    name="Latest Run",
                    inputs={"text": "test input"},
                    outputs={"response": "test output"},
                )
            ]
        )

        result = runner.invoke(
            cli,
            [
                "--json",
                "runs",
                "get-latest",
                "--project",
                "test",
                "--fields",
                "inputs,outputs",
            ],
        )

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "inputs" in data
        assert "outputs" in data
        assert "id" not in data
        assert "name" not in data

    @pytest.mark.parametrize(
        "flag,expected_error",
        [
            ("--failed", True),
            ("--succeeded", False),
        ],
    )
    def test_get_latest_status_flags(self, runner, mock_client, flag, expected_error):
        """--failed and --succeeded flags filter by error status."""
        mock_client.list_runs.return_value = iter([create_run(name="Run")])

        runner.invoke(cli, ["--json", "runs", "get-latest", "--project", "test", flag])

        call_kwargs = mock_client.list_runs.call_args[1]
        assert call_kwargs["error"] is expected_error

    def test_get_latest_with_roots_flag(self, runner, mock_client):
        """--roots flag filters to root runs."""
        mock_client.list_runs.return_value = iter([create_run(name="Root Run")])

        runner.invoke(
            cli, ["--json", "runs", "get-latest", "--project", "test", "--roots"]
        )

        call_kwargs = mock_client.list_runs.call_args[1]
        assert call_kwargs["is_root"] is True

    def test_get_latest_no_runs_found(self, runner, mock_client):
        """Get-latest returns error when no runs match."""
        mock_client.list_runs.return_value = iter([])

        result = runner.invoke(
            cli, ["runs", "get-latest", "--project", "test", "--failed"]
        )

        assert result.exit_code == 1
        assert "No runs found" in result.output

    def test_get_latest_with_multiple_projects(self, runner, mock_client):
        """Get-latest searches multiple projects with pattern."""
        mock_client.list_projects.return_value = [
            create_project(name="prd/project1"),
            create_project(name="prd/project2"),
        ]

        def list_runs_side_effect(**kwargs):
            if kwargs["project_name"] == "prd/project1":
                return iter([])
            return iter([create_run(name="Run from project2")])

        mock_client.list_runs.side_effect = list_runs_side_effect

        result = runner.invoke(
            cli, ["--json", "runs", "get-latest", "--project-name-pattern", "prd/*"]
        )

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["name"] == "Run from project2"

    def test_get_latest_with_output_writes_file(self, runner, mock_client, tmp_path):
        """INVARIANT: get-latest --output writes a single JSON object to file."""
        mock_client.list_runs.return_value = iter(
            [create_run(name="Latest For File", inputs={"q": "test"})]
        )
        output_file = str(tmp_path / "latest.json")

        result = runner.invoke(
            cli,
            [
                "--json",
                "runs",
                "get-latest",
                "--project",
                "test",
                "--output",
                output_file,
            ],
        )

        assert result.exit_code == 0
        with open(output_file) as f:
            data = json.load(f)
        assert isinstance(data, dict)
        assert data["name"] == "Latest For File"

    def test_get_latest_with_tag_filter(self, runner, mock_client):
        """--tag filter builds correct FQL."""
        mock_client.list_runs.return_value = iter(
            [create_run(name="Tagged Run", tags=["prod", "critical"])]
        )

        result = runner.invoke(
            cli,
            [
                "--json",
                "runs",
                "get-latest",
                "--project",
                "test",
                "--tag",
                "prod",
                "--tag",
                "critical",
            ],
        )

        assert result.exit_code == 0
        call_kwargs = mock_client.list_runs.call_args[1]
        assert 'has(tags, "prod")' in call_kwargs["filter"]
        assert 'has(tags, "critical")' in call_kwargs["filter"]


class TestRunsStats:
    """Tests for runs stats command."""

    def test_stats_basic(self, runner, mock_client):
        """Stats command displays statistics."""
        mock_client.get_run_stats.return_value = {"error_rate": 0.1, "latency_p50": 0.2}

        result = runner.invoke(cli, ["runs", "stats"])

        assert result.exit_code == 0
        assert "Error Rate" in result.output
        assert "0.1" in result.output

    def test_stats_table_output(self, runner, mock_client):
        """Stats with table output shows metrics."""
        mock_project = MagicMock()
        mock_project.id = "project-123"
        mock_client.read_project.return_value = mock_project
        mock_client.get_run_stats.return_value = {
            "run_count": 100,
            "error_count": 5,
            "avg_latency": 1.5,
        }

        result = runner.invoke(cli, ["runs", "stats", "--project", "test-project"])

        assert result.exit_code == 0
        assert "100" in result.output
        assert "5" in result.output

    def test_stats_json_output(self, runner, mock_client):
        """Stats with --json returns JSON."""
        mock_project = MagicMock()
        mock_project.id = "project-456"
        mock_client.read_project.return_value = mock_project
        mock_client.get_run_stats.return_value = {"run_count": 50, "error_count": 2}

        result = runner.invoke(
            cli, ["--json", "runs", "stats", "--project", "my-project"]
        )

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["run_count"] == 50
        assert data["error_count"] == 2

    def test_stats_fallback_to_project_id(self, runner, mock_client):
        """Stats falls back to using project name as ID on error."""
        mock_client.read_project.side_effect = Exception("Not found")
        mock_client.get_run_stats.return_value = {"run_count": 10}

        result = runner.invoke(
            cli, ["--json", "runs", "stats", "--project", "fallback-id"]
        )

        assert result.exit_code == 0
        mock_client.get_run_stats.assert_called_once()

    def test_stats_no_matching_projects_json(self, runner, mock_client):
        """INVARIANT: stats returns JSON error when no projects match."""
        from langsmith_cli.commands.runs import stats_cmd
        from langsmith_cli.project_resolution import ProjectQuery

        empty_pq = ProjectQuery(names=[], project_id=None)
        with patch.object(stats_cmd, "resolve_project_filters", return_value=empty_pq):
            result = runner.invoke(
                cli,
                ["--json", "runs", "stats", "--project-name-pattern", "nonexistent/*"],
            )

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "error" in data or "message" in data

    def test_stats_no_matching_projects_table(self, runner, mock_client):
        """INVARIANT: stats prints warning when no projects match in table mode."""
        from langsmith_cli.commands.runs import stats_cmd
        from langsmith_cli.project_resolution import ProjectQuery

        empty_pq = ProjectQuery(names=[], project_id=None)
        with patch.object(stats_cmd, "resolve_project_filters", return_value=empty_pq):
            result = runner.invoke(
                cli,
                ["runs", "stats", "--project-name-pattern", "nonexistent/*"],
            )

        assert result.exit_code == 0
        assert "No matching projects" in result.output

    def test_stats_multi_project_title(self, runner, mock_client):
        """INVARIANT: stats table title shows count when multiple projects match."""
        from conftest import create_project

        proj1 = create_project(name="proj-a")
        proj2 = create_project(name="proj-b")
        mock_client.list_projects.return_value = [proj1, proj2]
        mock_client.read_project.side_effect = lambda project_name=None: (
            proj1 if project_name == "proj-a" else proj2
        )
        mock_client.get_run_stats.return_value = {"run_count": 5}

        result = runner.invoke(
            cli,
            ["runs", "stats", "--project-name", "proj"],
        )

        assert result.exit_code == 0
        output = strip_ansi(result.output)
        assert "2 projects" in output

    def test_stats_project_id_flag(self, runner, mock_client):
        """INVARIANT: --project-id uses pq.use_id branch, title shows 'id:'."""
        mock_client.get_run_stats.return_value = {"run_count": 42}

        result = runner.invoke(
            cli,
            [
                "runs",
                "stats",
                "--project-id",
                "00000000-0000-0000-0000-000000000001",
            ],
        )

        assert result.exit_code == 0
        output = strip_ansi(result.output)
        assert "id:" in output


RUN_ID = "019cdd82-6584-74c0-82f5-3dc7bf5582d6"
TRACE_ID = "019cdd82-5b79-74e3-8852-09f00ea5f8aa"
PROJECT_ID = "730acc6c-ec97-4f08-915e-7d3f7f775300"
ORG_ID = "b658ea18-0431-42c0-8d03-337d43fed8cf"

# URL generated by client.get_run_url (SDK-authoritative format):
#   /o/{tenant_id}/projects/p/{session_id}/r/{run_id}?poll=true
SDK_URL = (
    f"https://smith.langchain.com/o/{ORG_ID}/projects/p/{PROJECT_ID}"
    f"/r/{RUN_ID}?poll=true"
)


class TestRunsOpen:
    """Tests for runs open command."""

    def test_open_url_contains_org_and_project_ids(self, runner, mock_client):
        """INVARIANT: the URL must contain both org_id (tenant) and project_id (session).

        The broken format /r/{run_id} only contains the run_id — it lacks the
        org and project context needed to locate the run without an active browser
        session. A correct URL is self-contained and shareable.
        """
        mock_client.read_run.return_value = create_run(
            id_str=RUN_ID, trace_id=TRACE_ID, session_id=PROJECT_ID
        )
        mock_client.get_run_url.return_value = SDK_URL

        with patch("webbrowser.open"):
            result = runner.invoke(cli, ["--json", "runs", "open", RUN_ID])

        assert result.exit_code == 0
        url = json.loads(result.output)["url"]

        assert ORG_ID in url, f"URL must contain org_id ({ORG_ID}): {url}"
        assert PROJECT_ID in url, f"URL must contain project_id ({PROJECT_ID}): {url}"
        assert RUN_ID in url, f"URL must contain run_id ({RUN_ID}): {url}"
        assert url != f"https://smith.langchain.com/r/{RUN_ID}", (
            "URL must not be the broken /r/{run_id} format that lacks org and project context"
        )

    def test_open_delegates_to_sdk_get_run_url(self, runner, mock_client):
        """INVARIANT: URL comes from client.get_run_url, not hardcoded logic.

        Using the SDK as the single source of truth ensures the URL format
        stays correct as LangSmith evolves, without needing to update the CLI.
        """
        run = create_run(id_str=RUN_ID, trace_id=TRACE_ID, session_id=PROJECT_ID)
        mock_client.read_run.return_value = run
        sentinel_url = (
            "https://smith.langchain.com/o/SENTINEL/projects/p/SENTINEL/r/SENTINEL"
        )
        mock_client.get_run_url.return_value = sentinel_url

        with patch("webbrowser.open") as mock_browser:
            result = runner.invoke(cli, ["--json", "runs", "open", RUN_ID])

        assert result.exit_code == 0
        mock_client.get_run_url.assert_called_once_with(run=run)
        data = json.loads(result.output)
        assert data["run_id"] == RUN_ID
        assert data["url"] == sentinel_url
        mock_browser.assert_called_once_with(sentinel_url)

    def test_open_table_mode_shows_url_with_context(self, runner, mock_client):
        """INVARIANT: table mode output contains the URL with org and project context."""
        mock_client.read_run.return_value = create_run(
            id_str=RUN_ID, trace_id=TRACE_ID, session_id=PROJECT_ID
        )
        mock_client.get_run_url.return_value = SDK_URL

        with patch("webbrowser.open"):
            result = runner.invoke(cli, ["runs", "open", RUN_ID])

        assert result.exit_code == 0
        assert ORG_ID in result.output
        assert PROJECT_ID in result.output


class TestRunsWatch:
    """Tests for runs watch command."""

    def test_watch_keyboard_interrupt(self, runner, mock_client):
        """Watch handles keyboard interrupt gracefully."""
        from uuid import UUID

        mock_client.list_projects.return_value = []
        test_run = create_run(name="Watched Run", total_tokens=100)
        # Override session_id to be a UUID
        test_run_dict = test_run.model_dump()
        test_run_dict["session_id"] = UUID("00000000-0000-0000-0000-000000000092")

        from langsmith.schemas import Run

        test_run_with_session = Run(**test_run_dict)

        mock_client.list_runs.side_effect = [
            [test_run_with_session],
            KeyboardInterrupt(),
        ]

        with patch("time.sleep") as mock_sleep:
            mock_sleep.side_effect = KeyboardInterrupt()
            result = runner.invoke(cli, ["runs", "watch", "--project", "test"])

        assert result.exit_code == 0

    def test_watch_project_id_branch(self, runner, mock_client):
        """INVARIANT: --project-id uses pq.use_id=True branch in generate_table."""
        mock_client.list_runs.return_value = [create_run(name="id-branch-run")]

        with patch("time.sleep") as mock_sleep:
            mock_sleep.side_effect = KeyboardInterrupt()
            result = runner.invoke(
                cli,
                [
                    "runs",
                    "watch",
                    "--project-id",
                    "00000000-0000-0000-0000-000000000001",
                ],
            )

        assert result.exit_code == 0
        output = strip_ansi(result.output)
        assert "id:" in output

    def test_watch_project_name_pattern_title(self, runner, mock_client):
        """INVARIANT: --project-name-pattern shows pattern in title."""
        from conftest import create_project

        mock_client.list_projects.return_value = [
            create_project(name="prd/svc-a"),
            create_project(name="prd/svc-b"),
        ]
        mock_client.list_runs.return_value = []

        with patch("time.sleep") as mock_sleep:
            mock_sleep.side_effect = KeyboardInterrupt()
            result = runner.invoke(
                cli,
                ["runs", "watch", "--project-name-pattern", "prd/*"],
            )

        assert result.exit_code == 0
        output = strip_ansi(result.output)
        assert "prd/*" in output

    def test_watch_project_name_regex_title(self, runner, mock_client):
        """INVARIANT: --project-name-regex shows regex in title."""
        from conftest import create_project

        mock_client.list_projects.return_value = [create_project(name="dev-api-v2")]
        mock_client.list_runs.return_value = []

        with patch("time.sleep") as mock_sleep:
            mock_sleep.side_effect = KeyboardInterrupt()
            result = runner.invoke(
                cli,
                ["runs", "watch", "--project-name-regex", "^dev-.*-v[0-9]+$"],
            )

        assert result.exit_code == 0
        output = strip_ansi(result.output)
        assert "regex(" in output

    def test_watch_project_name_fuzzy_title(self, runner, mock_client):
        """INVARIANT: --project-name shows fuzzy match title with * wildcards."""
        from conftest import create_project

        mock_client.list_projects.return_value = [
            create_project(name="my-service"),
            create_project(name="my-other-service"),
        ]
        mock_client.list_runs.return_value = []

        with patch("time.sleep") as mock_sleep:
            mock_sleep.side_effect = KeyboardInterrupt()
            result = runner.invoke(
                cli,
                ["runs", "watch", "--project-name", "my"],
            )

        assert result.exit_code == 0
        output = strip_ansi(result.output)
        assert "*my*" in output

    def test_watch_failed_project_shows_count(self, runner, mock_client):
        """INVARIANT: When projects fail to fetch runs, failed count appears in title."""
        from conftest import create_project

        mock_client.list_projects.return_value = [
            create_project(name="svc-a"),
            create_project(name="svc-b"),
        ]
        mock_client.list_runs.side_effect = Exception("API error")

        with patch("time.sleep") as mock_sleep:
            mock_sleep.side_effect = KeyboardInterrupt()
            result = runner.invoke(
                cli,
                ["runs", "watch", "--project-name", "svc"],
            )

        assert result.exit_code == 0
        output = strip_ansi(result.output)
        assert "failed" in output.lower()
