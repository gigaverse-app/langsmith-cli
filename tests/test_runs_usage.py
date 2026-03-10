"""Tests for runs usage command."""

import json
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

import pytest
from langsmith.schemas import Run

from conftest import make_run_id, strip_ansi
from langsmith_cli.main import cli


def _extract_json(output: str) -> dict:
    """Extract JSON from CliRunner output that may contain mixed stderr/stdout."""
    for line in output.strip().split("\n"):
        line = line.strip()
        if line.startswith("{"):
            return json.loads(line)
    raise ValueError(f"No JSON found in output: {output!r}")


def _create_llm_run(
    n: int,
    hour: int = 16,
    minute: int = 0,
    model: str = "gpt-4",
    total_tokens: int = 1000,
    prompt_tokens: int = 700,
    completion_tokens: int = 300,
    cost: str = "0.001",
    channel_id: str | None = None,
    tag_env: str | None = None,
) -> Run:
    """Create an LLM run with model metadata for usage tests."""
    metadata: dict = {"ls_model_name": model}
    if channel_id:
        metadata["channel_id"] = channel_id

    tags = []
    if tag_env:
        tags.append(f"env:{tag_env}")

    return Run(
        id=UUID(make_run_id(n)),
        name="ChatModel",
        run_type="llm",
        start_time=datetime(2026, 3, 9, hour, minute, 0, tzinfo=timezone.utc),
        total_tokens=total_tokens,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_cost=Decimal(cost),
        extra={"metadata": metadata},
        tags=tags,
    )


def _create_chain_run(n: int, hour: int = 16, total_tokens: int = 1000) -> Run:
    """Create a chain run (no model name) - should be filtered out by usage."""
    return Run(
        id=UUID(make_run_id(n)),
        name="chain-wrapper",
        run_type="llm",
        start_time=datetime(2026, 3, 9, hour, 0, 0, tzinfo=timezone.utc),
        total_tokens=total_tokens,
        extra={"metadata": {}},
    )


class TestUsageHelpers:
    """Tests for _get_model_name and _truncate_hour helpers."""

    def test_get_model_name_from_ls_model_name(self):
        from langsmith_cli.commands.runs import _get_model_name

        run = _create_llm_run(1, model="gemini-2.5-flash-lite")
        assert _get_model_name(run) == "gemini-2.5-flash-lite"

    def test_get_model_name_from_invocation_params(self):
        from langsmith_cli.commands.runs import _get_model_name

        run = Run(
            id=UUID(make_run_id(1)),
            name="test",
            run_type="llm",
            start_time=datetime.now(timezone.utc),
            extra={"invocation_params": {"model": "claude-3-opus"}},
        )
        assert _get_model_name(run) == "claude-3-opus"

    def test_get_model_name_unknown_when_no_model(self):
        from langsmith_cli.commands.runs import _get_model_name

        run = _create_chain_run(1)
        assert _get_model_name(run) == "unknown"

    def test_truncate_hour(self):
        from langsmith_cli.commands.runs import _truncate_hour

        dt = datetime(2026, 3, 9, 16, 45, 30, tzinfo=timezone.utc)
        assert _truncate_hour(dt) == "2026-03-09T16:00Z"

    def test_truncate_hour_naive_datetime(self):
        from langsmith_cli.commands.runs import _truncate_hour

        dt = datetime(2026, 3, 9, 10, 30, 0)
        assert _truncate_hour(dt) == "2026-03-09T10:00Z"


class TestUsageCommand:
    """Tests for runs usage command end-to-end."""

    def test_usage_basic_json(self, runner, mock_client):
        """Basic usage command returns JSON with summary and buckets."""
        runs = [
            _create_llm_run(1, hour=16, minute=10),
            _create_llm_run(2, hour=16, minute=30),
            _create_llm_run(3, hour=17, minute=15),
        ]
        mock_client.list_runs.return_value = runs

        result = runner.invoke(cli, ["--json", "runs", "usage", "--last", "24h"])

        assert result.exit_code == 0
        data = _extract_json(result.output)
        assert "summary" in data
        assert "buckets" in data
        assert data["summary"]["total_tokens"] == 3000
        assert data["summary"]["run_count"] == 3
        assert data["summary"]["active_buckets"] == 2

    def test_usage_filters_out_unknown_models(self, runner, mock_client):
        """Runs without model info (chain wrappers) are excluded."""
        runs = [
            _create_llm_run(1, total_tokens=500),
            _create_chain_run(2, total_tokens=500),  # No model - filtered out
        ]
        mock_client.list_runs.return_value = runs

        result = runner.invoke(cli, ["--json", "runs", "usage", "--last", "24h"])

        assert result.exit_code == 0
        data = _extract_json(result.output)
        assert data["summary"]["total_tokens"] == 500
        assert data["summary"]["run_count"] == 1

    def test_usage_group_by_metadata(self, runner, mock_client):
        """Group by metadata field creates per-group buckets."""
        runs = [
            _create_llm_run(1, hour=16, channel_id="room-A", total_tokens=100),
            _create_llm_run(2, hour=16, channel_id="room-A", total_tokens=200),
            _create_llm_run(3, hour=16, channel_id="room-B", total_tokens=300),
        ]
        mock_client.list_runs.return_value = runs

        result = runner.invoke(
            cli,
            [
                "--json",
                "runs",
                "usage",
                "--group-by",
                "metadata:channel_id",
                "--last",
                "24h",
            ],
        )

        assert result.exit_code == 0
        data = _extract_json(result.output)
        assert data["summary"]["unique_groups"] == 2

        buckets = data["buckets"]
        room_a = [b for b in buckets if b["group"] == "room-A"]
        room_b = [b for b in buckets if b["group"] == "room-B"]
        assert room_a[0]["total_tokens"] == 300
        assert room_b[0]["total_tokens"] == 300

    def test_usage_group_by_tag(self, runner, mock_client):
        """Group by tag field works correctly."""
        runs = [
            _create_llm_run(1, tag_env="prod", total_tokens=100),
            _create_llm_run(2, tag_env="staging", total_tokens=200),
        ]
        mock_client.list_runs.return_value = runs

        result = runner.invoke(
            cli,
            [
                "--json",
                "runs",
                "usage",
                "--group-by",
                "tag:env",
                "--last",
                "24h",
            ],
        )

        assert result.exit_code == 0
        data = _extract_json(result.output)
        assert data["summary"]["unique_groups"] == 2

    def test_usage_breakdown_by_model(self, runner, mock_client):
        """Breakdown by model adds model column to buckets."""
        runs = [
            _create_llm_run(1, hour=16, model="gpt-4", total_tokens=100),
            _create_llm_run(2, hour=16, model="claude-3", total_tokens=200),
        ]
        mock_client.list_runs.return_value = runs

        result = runner.invoke(
            cli,
            [
                "--json",
                "runs",
                "usage",
                "--breakdown",
                "model",
                "--last",
                "24h",
            ],
        )

        assert result.exit_code == 0
        data = _extract_json(result.output)
        buckets = data["buckets"]
        assert len(buckets) == 2
        models = {b["model"] for b in buckets}
        assert models == {"gpt-4", "claude-3"}

    def test_usage_breakdown_by_project(self, runner, mock_client):
        """Breakdown by project uses the source project name from fetch."""
        runs = [
            _create_llm_run(1, total_tokens=100),
            _create_llm_run(2, total_tokens=200),
        ]
        mock_client.list_runs.return_value = runs

        result = runner.invoke(
            cli,
            [
                "--json",
                "runs",
                "usage",
                "--breakdown",
                "project",
                "--last",
                "24h",
            ],
        )

        assert result.exit_code == 0
        data = _extract_json(result.output)
        buckets = data["buckets"]
        assert all("project" in b for b in buckets)

    def test_usage_active_only(self, runner, mock_client):
        """Active-only flag filters out zero-token buckets."""
        runs = [
            _create_llm_run(1, hour=16, total_tokens=500),
            _create_llm_run(2, hour=16, total_tokens=0),
        ]
        mock_client.list_runs.return_value = runs

        result = runner.invoke(
            cli,
            ["--json", "runs", "usage", "--active-only", "--last", "24h"],
        )

        assert result.exit_code == 0
        data = _extract_json(result.output)
        for bucket in data["buckets"]:
            assert bucket["total_tokens"] > 0

    def test_usage_interval_day(self, runner, mock_client):
        """Day interval groups by date instead of hour."""
        runs = [
            _create_llm_run(1, hour=10),
            _create_llm_run(2, hour=14),
            _create_llm_run(3, hour=22),
        ]
        mock_client.list_runs.return_value = runs

        result = runner.invoke(
            cli,
            ["--json", "runs", "usage", "--interval", "day", "--last", "24h"],
        )

        assert result.exit_code == 0
        data = _extract_json(result.output)
        assert data["summary"]["active_buckets"] == 1
        assert data["summary"]["run_count"] == 3
        assert data["buckets"][0]["time"] == "2026-03-09"

    def test_usage_concurrent_groups(self, runner, mock_client):
        """Summary reports max and avg concurrent groups per time bucket."""
        runs = [
            _create_llm_run(1, hour=16, channel_id="room-A"),
            _create_llm_run(2, hour=16, channel_id="room-B"),
            _create_llm_run(3, hour=16, channel_id="room-C"),
            _create_llm_run(4, hour=17, channel_id="room-A"),
        ]
        mock_client.list_runs.return_value = runs

        result = runner.invoke(
            cli,
            [
                "--json",
                "runs",
                "usage",
                "--group-by",
                "metadata:channel_id",
                "--last",
                "24h",
            ],
        )

        assert result.exit_code == 0
        data = _extract_json(result.output)
        assert data["summary"]["max_concurrent_groups"] == 3
        assert data["summary"]["avg_concurrent_groups"] == 2.0

    def test_usage_metadata_filter(self, runner, mock_client):
        """Metadata filter adds server-side FQL filter."""
        mock_client.list_runs.return_value = [_create_llm_run(1, channel_id="room-X")]

        result = runner.invoke(
            cli,
            [
                "--json",
                "runs",
                "usage",
                "--metadata",
                "channel_id=room-X",
                "--last",
                "24h",
            ],
        )

        assert result.exit_code == 0
        _, kwargs = mock_client.list_runs.call_args
        fql_filter = kwargs.get("filter", "")
        assert 'in(metadata_key, ["channel_id"])' in fql_filter
        assert 'eq(metadata_value, "room-X")' in fql_filter

    def test_usage_metadata_filter_invalid_format(self, runner, mock_client):
        """Metadata filter without = raises error."""
        result = runner.invoke(
            cli,
            [
                "--json",
                "runs",
                "usage",
                "--metadata",
                "channel_id_missing_equals",
                "--last",
                "24h",
            ],
        )

        assert result.exit_code != 0
        assert "key=value" in result.output

    def test_usage_multiple_metadata_filters(self, runner, mock_client):
        """Multiple metadata filters are combined in FQL."""
        mock_client.list_runs.return_value = [_create_llm_run(1)]

        result = runner.invoke(
            cli,
            [
                "--json",
                "runs",
                "usage",
                "--metadata",
                "channel_id=room-X",
                "--metadata",
                "user_id=user-123",
                "--last",
                "24h",
            ],
        )

        assert result.exit_code == 0
        _, kwargs = mock_client.list_runs.call_args
        fql_filter = kwargs.get("filter", "")
        assert "channel_id" in fql_filter
        assert "user_id" in fql_filter

    def test_usage_llm_only_filter(self, runner, mock_client):
        """Usage command always filters to run_type=llm to avoid double-counting."""
        mock_client.list_runs.return_value = []

        runner.invoke(cli, ["--json", "runs", "usage", "--last", "24h"])

        _, kwargs = mock_client.list_runs.call_args
        fql_filter = kwargs.get("filter", "")
        assert 'eq(run_type, "llm")' in fql_filter

    def test_usage_select_fields(self, runner, mock_client):
        """Usage command uses select for performance."""
        mock_client.list_runs.return_value = []

        runner.invoke(cli, ["--json", "runs", "usage", "--last", "24h"])

        _, kwargs = mock_client.list_runs.call_args
        select = kwargs.get("select", [])
        assert "total_tokens" in select
        assert "start_time" in select
        assert "extra" in select
        assert "prompt_tokens" in select
        assert "completion_tokens" in select
        assert "total_cost" in select

    def test_usage_token_cost_aggregation(self, runner, mock_client):
        """Token counts and costs aggregate correctly across runs."""
        runs = [
            _create_llm_run(
                1,
                hour=16,
                total_tokens=1000,
                prompt_tokens=600,
                completion_tokens=400,
                cost="0.010",
            ),
            _create_llm_run(
                2,
                hour=16,
                total_tokens=2000,
                prompt_tokens=1200,
                completion_tokens=800,
                cost="0.020",
            ),
        ]
        mock_client.list_runs.return_value = runs

        result = runner.invoke(cli, ["--json", "runs", "usage", "--last", "24h"])

        assert result.exit_code == 0
        data = _extract_json(result.output)
        bucket = data["buckets"][0]
        assert bucket["total_tokens"] == 3000
        assert bucket["prompt_tokens"] == 1800
        assert bucket["completion_tokens"] == 1200
        assert bucket["total_cost"] == pytest.approx(0.030, rel=0.001)
        assert bucket["run_count"] == 2

    def test_usage_table_output(self, runner, mock_client):
        """Table output shows summary and table with token data."""
        runs = [_create_llm_run(1, total_tokens=5000)]
        mock_client.list_runs.return_value = runs

        result = runner.invoke(cli, ["runs", "usage", "--last", "24h"])

        assert result.exit_code == 0
        output = strip_ansi(result.output)
        assert "Token Usage Summary" in output
        assert "5,000" in output
        assert "Token Usage by Hour" in output

    def test_usage_table_with_group_and_breakdown(self, runner, mock_client):
        """Table output includes group and breakdown columns."""
        runs = [
            _create_llm_run(1, model="gpt-4", channel_id="room-A"),
        ]
        mock_client.list_runs.return_value = runs

        result = runner.invoke(
            cli,
            [
                "runs",
                "usage",
                "--group-by",
                "metadata:channel_id",
                "--breakdown",
                "model",
                "--last",
                "24h",
            ],
        )

        assert result.exit_code == 0
        output = strip_ansi(result.output)
        assert "Channel_Id" in output or "channel_id" in output.lower()
        assert "room-A" in output
        assert "gpt-4" in output

    def test_usage_no_runs_found(self, runner, mock_client):
        """Empty result set shows warning."""
        mock_client.list_runs.return_value = []

        result = runner.invoke(cli, ["--json", "runs", "usage", "--last", "24h"])

        # Should exit 0 but print a warning
        assert result.exit_code == 0

    def test_usage_sample_size(self, runner, mock_client):
        """Sample size limits the number of runs fetched."""
        runs = [_create_llm_run(i) for i in range(100)]
        mock_client.list_runs.return_value = iter(runs)

        result = runner.invoke(
            cli,
            ["--json", "runs", "usage", "--sample-size", "10", "--last", "24h"],
        )

        assert result.exit_code == 0
        data = _extract_json(result.output)
        assert data["summary"]["run_count"] == 10

    def test_usage_sorted_by_time(self, runner, mock_client):
        """Results are sorted by time bucket."""
        runs = [
            _create_llm_run(1, hour=18),
            _create_llm_run(2, hour=14),
            _create_llm_run(3, hour=16),
        ]
        mock_client.list_runs.return_value = runs

        result = runner.invoke(cli, ["--json", "runs", "usage", "--last", "24h"])

        assert result.exit_code == 0
        data = _extract_json(result.output)
        times = [b["time"] for b in data["buckets"]]
        assert times == sorted(times)

    def test_usage_ungrouped_runs(self, runner, mock_client):
        """Runs without the group field get placed in 'ungrouped'."""
        runs = [
            _create_llm_run(1, channel_id="room-A"),
            _create_llm_run(2),  # No channel_id
        ]
        mock_client.list_runs.return_value = runs

        result = runner.invoke(
            cli,
            [
                "--json",
                "runs",
                "usage",
                "--group-by",
                "metadata:channel_id",
                "--last",
                "24h",
            ],
        )

        assert result.exit_code == 0
        data = _extract_json(result.output)
        groups = {b["group"] for b in data["buckets"]}
        assert "room-A" in groups
        assert "ungrouped" in groups

    def test_usage_multi_dimensional_group_rejected(self, runner, mock_client):
        """Multi-dimensional grouping is not supported for usage."""
        mock_client.list_runs.return_value = [_create_llm_run(1)]

        result = runner.invoke(
            cli,
            [
                "runs",
                "usage",
                "--group-by",
                "metadata:channel_id,metadata:user_id",
                "--last",
                "24h",
            ],
        )

        assert result.exit_code != 0
        assert "not supported" in result.output

    def test_usage_decimal_cost_handling(self, runner, mock_client):
        """Decimal costs from SDK are handled without TypeError."""
        runs = [
            Run(
                id=UUID(make_run_id(1)),
                name="ChatModel",
                run_type="llm",
                start_time=datetime(2026, 3, 9, 16, 0, 0, tzinfo=timezone.utc),
                total_tokens=1000,
                prompt_tokens=700,
                completion_tokens=300,
                total_cost=Decimal("0.0058"),
                extra={"metadata": {"ls_model_name": "gpt-4"}},
            ),
        ]
        mock_client.list_runs.return_value = runs

        result = runner.invoke(cli, ["--json", "runs", "usage", "--last", "24h"])

        assert result.exit_code == 0
        data = _extract_json(result.output)
        assert data["buckets"][0]["total_cost"] == pytest.approx(0.0058, rel=0.001)

    def test_usage_both_breakdowns(self, runner, mock_client):
        """Both model and project breakdowns can be used together."""
        runs = [
            _create_llm_run(1, model="gpt-4"),
            _create_llm_run(2, model="claude-3"),
        ]
        mock_client.list_runs.return_value = runs

        result = runner.invoke(
            cli,
            [
                "--json",
                "runs",
                "usage",
                "--breakdown",
                "model",
                "--breakdown",
                "project",
                "--last",
                "24h",
            ],
        )

        assert result.exit_code == 0
        data = _extract_json(result.output)
        buckets = data["buckets"]
        assert all("model" in b for b in buckets)
        assert all("project" in b for b in buckets)

    def test_order_by_not_passed_to_api(self, runner, mock_client):
        """INVARIANT: order_by must NOT be passed to client.list_runs()."""
        mock_client.list_runs.return_value = [_create_llm_run(1)]

        runner.invoke(cli, ["runs", "usage", "--last", "24h"])

        for call in mock_client.list_runs.call_args_list:
            call_kwargs = call[1]
            assert "order_by" not in call_kwargs


class TestUsageTagFiltering:
    """Tests for --tag filtering on runs usage command."""

    def test_tag_filter_adds_fql_for_api(self, runner, mock_client):
        """INVARIANT: --tag adds has(tags, ...) FQL filter for API calls."""
        mock_client.list_runs.return_value = [_create_llm_run(1)]

        runner.invoke(
            cli,
            ["--json", "runs", "usage", "--last", "24h", "--tag", "env:prod"],
        )

        call_kwargs = mock_client.list_runs.call_args[1]
        assert 'has(tags, "env:prod")' in call_kwargs["filter"]

    def test_multiple_tags_and_logic_api(self, runner, mock_client):
        """INVARIANT: Multiple --tag flags produce AND logic in FQL."""
        mock_client.list_runs.return_value = [_create_llm_run(1)]

        runner.invoke(
            cli,
            [
                "--json",
                "runs",
                "usage",
                "--last",
                "24h",
                "--tag",
                "env:prod",
                "--tag",
                "team:ml",
            ],
        )

        call_kwargs = mock_client.list_runs.call_args[1]
        fql = call_kwargs["filter"]
        assert 'has(tags, "env:prod")' in fql
        assert 'has(tags, "team:ml")' in fql

    def test_tag_filter_client_side_from_cache(
        self, runner, mock_client, tmp_path, monkeypatch
    ):
        """INVARIANT: --tag filters cached runs client-side."""
        monkeypatch.setattr("langsmith_cli.cache.get_cache_dir", lambda: tmp_path)
        from langsmith_cli.cache import append_runs_to_cache

        runs = [
            _create_llm_run(1, tag_env="prod"),
            _create_llm_run(2, tag_env="staging"),
            _create_llm_run(3, tag_env="prod"),
        ]
        append_runs_to_cache("test-proj", runs)

        result = runner.invoke(
            cli,
            [
                "--json",
                "runs",
                "usage",
                "--project",
                "test-proj",
                "--from-cache",
                "--tag",
                "env:prod",
            ],
        )
        assert result.exit_code == 0
        data = _extract_json(result.output)
        # Should only count 2 prod runs, not all 3
        assert data["summary"]["run_count"] == 2

    def test_cache_project_mapping_uses_source_map(self, tmp_path, monkeypatch):
        """INVARIANT: load_runs_from_cache populates item_source_map correctly."""
        monkeypatch.setattr("langsmith_cli.cache.get_cache_dir", lambda: tmp_path)
        from langsmith_cli.cache import append_runs_to_cache, load_runs_from_cache

        # Two projects with different runs
        proj_a_runs = [_create_llm_run(1, hour=14, model="gpt-4")]
        proj_b_runs = [_create_llm_run(2, hour=15, model="claude-3")]
        append_runs_to_cache("proj-a", proj_a_runs)
        append_runs_to_cache("proj-b", proj_b_runs)

        result = load_runs_from_cache(["proj-a", "proj-b"])

        assert len(result.items) == 2
        # Each run should map to its source project
        run1_id = str(proj_a_runs[0].id)
        run2_id = str(proj_b_runs[0].id)
        assert result.item_source_map[run1_id] == "proj-a"
        assert result.item_source_map[run2_id] == "proj-b"
