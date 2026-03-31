"""Tests for runs usage command."""

import json
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

import pytest
from langsmith.schemas import Run

from conftest import make_run_id, strip_ansi
from langsmith_cli.commands.runs import _get_service_tier
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
    prompt_cost: str | None = None,
    completion_cost: str | None = None,
    channel_id: str | None = None,
    tag_env: str | None = None,
    ls_provider: str | None = None,
    service_tier: str | None = None,
) -> Run:
    """Create an LLM run with model metadata for usage tests."""
    metadata: dict = {"ls_model_name": model}
    if channel_id:
        metadata["channel_id"] = channel_id
    if ls_provider:
        metadata["ls_provider"] = ls_provider

    tags = []
    if tag_env:
        tags.append(f"env:{tag_env}")

    extra: dict = {"metadata": metadata}
    if service_tier:
        extra["invocation_params"] = {"service_tier": service_tier}

    return Run(
        id=UUID(make_run_id(n)),
        name="ChatModel",
        run_type="llm",
        start_time=datetime(2026, 3, 9, hour, minute, 0, tzinfo=timezone.utc),
        total_tokens=total_tokens,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_cost=Decimal(cost),
        prompt_cost=Decimal(prompt_cost) if prompt_cost else None,
        completion_cost=Decimal(completion_cost) if completion_cost else None,
        extra=extra,
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


class TestUsageGrepFiltering:
    """Tests for --grep filtering on runs usage command."""

    def test_grep_filters_runs_by_content(self, runner, mock_client):
        """INVARIANT: --grep filters runs by content before aggregation."""
        # Create runs with different inputs
        run_match = Run(
            id=UUID(make_run_id(1)),
            name="ChatModel",
            run_type="llm",
            start_time=datetime(2026, 3, 9, 16, 0, 0, tzinfo=timezone.utc),
            total_tokens=1000,
            prompt_tokens=700,
            completion_tokens=300,
            total_cost=Decimal("0.001"),
            extra={"metadata": {"ls_model_name": "gpt-4"}},
            inputs={"messages": [{"content": "Tell me about Python"}]},
        )
        run_no_match = Run(
            id=UUID(make_run_id(2)),
            name="ChatModel",
            run_type="llm",
            start_time=datetime(2026, 3, 9, 16, 30, 0, tzinfo=timezone.utc),
            total_tokens=2000,
            prompt_tokens=1400,
            completion_tokens=600,
            total_cost=Decimal("0.002"),
            extra={"metadata": {"ls_model_name": "gpt-4"}},
            inputs={"messages": [{"content": "Tell me about Java"}]},
        )
        mock_client.list_runs.return_value = [run_match, run_no_match]

        result = runner.invoke(
            cli,
            ["--json", "runs", "usage", "--last", "24h", "--grep", "Python"],
        )
        assert result.exit_code == 0
        data = _extract_json(result.output)
        # Only the Python run should be counted
        assert data["summary"]["run_count"] == 1
        assert data["summary"]["total_tokens"] == 1000

    def test_grep_with_api_adds_content_fields_to_select(self, runner, mock_client):
        """INVARIANT: --grep adds inputs/outputs/error to select fields for API."""
        mock_client.list_runs.return_value = [_create_llm_run(1)]

        runner.invoke(
            cli,
            ["--json", "runs", "usage", "--last", "24h", "--grep", "test"],
        )

        call_kwargs = mock_client.list_runs.call_args[1]
        select = call_kwargs["select"]
        assert "inputs" in select
        assert "outputs" in select
        assert "error" in select

    def test_grep_without_flag_does_not_add_content_fields(self, runner, mock_client):
        """INVARIANT: Without --grep, select fields stay minimal for performance."""
        mock_client.list_runs.return_value = [_create_llm_run(1)]

        runner.invoke(cli, ["--json", "runs", "usage", "--last", "24h"])

        call_kwargs = mock_client.list_runs.call_args[1]
        select = call_kwargs["select"]
        assert "inputs" not in select
        assert "outputs" not in select


class TestUsageMetadataTagFallback:
    """Tests for metadata filter falling back to tag checking."""

    def test_metadata_filter_matches_tag_directly(
        self, runner, mock_client, tmp_path, monkeypatch
    ):
        """INVARIANT: --metadata key=value matches runs where value appears in tags."""
        monkeypatch.setattr("langsmith_cli.cache.get_cache_dir", lambda: tmp_path)
        from langsmith_cli.cache import append_runs_to_cache

        # Run has tag "chat:Foo" but no metadata field "channel_id"
        run_with_tag = Run(
            id=UUID(make_run_id(1)),
            name="ChatModel",
            run_type="llm",
            start_time=datetime(2026, 3, 9, 16, 0, 0, tzinfo=timezone.utc),
            total_tokens=1000,
            prompt_tokens=700,
            completion_tokens=300,
            total_cost=Decimal("0.001"),
            extra={"metadata": {"ls_model_name": "gpt-4"}},
            tags=["chat:Foo"],
        )
        run_without_tag = _create_llm_run(2)
        append_runs_to_cache("test-proj", [run_with_tag, run_without_tag])

        result = runner.invoke(
            cli,
            [
                "--json",
                "runs",
                "usage",
                "--project",
                "test-proj",
                "--from-cache",
                "--metadata",
                "channel_id=chat:Foo",
            ],
        )
        assert result.exit_code == 0
        data = _extract_json(result.output)
        # Should match the run with tag "chat:Foo" via direct tag check
        assert data["summary"]["run_count"] == 1

    def test_metadata_filter_matches_key_value_tag(
        self, runner, mock_client, tmp_path, monkeypatch
    ):
        """INVARIANT: --metadata key=value matches runs where 'key:value' appears in tags."""
        monkeypatch.setattr("langsmith_cli.cache.get_cache_dir", lambda: tmp_path)
        from langsmith_cli.cache import append_runs_to_cache

        # Run has tag "channel_id:room-A" (key:value format)
        run_with_kv_tag = Run(
            id=UUID(make_run_id(1)),
            name="ChatModel",
            run_type="llm",
            start_time=datetime(2026, 3, 9, 16, 0, 0, tzinfo=timezone.utc),
            total_tokens=1000,
            prompt_tokens=700,
            completion_tokens=300,
            total_cost=Decimal("0.001"),
            extra={"metadata": {"ls_model_name": "gpt-4"}},
            tags=["channel_id:room-A"],
        )
        run_no_match = _create_llm_run(2)
        append_runs_to_cache("test-proj", [run_with_kv_tag, run_no_match])

        result = runner.invoke(
            cli,
            [
                "--json",
                "runs",
                "usage",
                "--project",
                "test-proj",
                "--from-cache",
                "--metadata",
                "channel_id=room-A",
            ],
        )
        assert result.exit_code == 0
        data = _extract_json(result.output)
        # Should match via "channel_id:room-A" tag pattern
        assert data["summary"]["run_count"] == 1

    def test_metadata_filter_prefers_direct_metadata_over_tag(
        self, runner, mock_client, tmp_path, monkeypatch
    ):
        """INVARIANT: Direct metadata match takes priority; tag fallback only when metadata missing."""
        monkeypatch.setattr("langsmith_cli.cache.get_cache_dir", lambda: tmp_path)
        from langsmith_cli.cache import append_runs_to_cache

        # Run has both metadata AND a conflicting tag — metadata should win
        run_with_metadata = _create_llm_run(1, channel_id="room-X")
        run_with_tag_only = Run(
            id=UUID(make_run_id(2)),
            name="ChatModel",
            run_type="llm",
            start_time=datetime(2026, 3, 9, 16, 0, 0, tzinfo=timezone.utc),
            total_tokens=500,
            prompt_tokens=300,
            completion_tokens=200,
            total_cost=Decimal("0.001"),
            extra={"metadata": {"ls_model_name": "gpt-4"}},
            tags=["channel_id:room-X"],
        )
        run_no_match = _create_llm_run(3)
        append_runs_to_cache(
            "test-proj", [run_with_metadata, run_with_tag_only, run_no_match]
        )

        result = runner.invoke(
            cli,
            [
                "--json",
                "runs",
                "usage",
                "--project",
                "test-proj",
                "--from-cache",
                "--metadata",
                "channel_id=room-X",
            ],
        )
        assert result.exit_code == 0
        data = _extract_json(result.output)
        # Both run_with_metadata (direct match) and run_with_tag_only (tag fallback) should match
        assert data["summary"]["run_count"] == 2


class TestMetadataValueMatches:
    """Tests for _metadata_value_matches helper."""

    def test_exact_match(self):
        from langsmith_cli.commands.runs import _metadata_value_matches

        assert _metadata_value_matches("room-A", "room-A") is True
        assert _metadata_value_matches("room-B", "room-A") is False

    def test_none_candidate(self):
        from langsmith_cli.commands.runs import _metadata_value_matches

        assert _metadata_value_matches(None, "room-A") is False

    def test_wildcard_star(self):
        from langsmith_cli.commands.runs import _metadata_value_matches

        assert _metadata_value_matches("room-A", "room-*") is True
        assert _metadata_value_matches("room-B", "room-*") is True
        assert _metadata_value_matches("lobby-A", "room-*") is False

    def test_wildcard_question(self):
        from langsmith_cli.commands.runs import _metadata_value_matches

        assert _metadata_value_matches("room-A", "room-?") is True
        assert _metadata_value_matches("room-AB", "room-?") is False

    def test_wildcard_unanchored(self):
        from langsmith_cli.commands.runs import _metadata_value_matches

        assert _metadata_value_matches("my-room-A", "*room*") is True
        assert _metadata_value_matches("lobby", "*room*") is False

    def test_regex_match(self):
        from langsmith_cli.commands.runs import _metadata_value_matches

        assert _metadata_value_matches("room-123", "/^room-[0-9]+$/") is True
        assert _metadata_value_matches("room-abc", "/^room-[0-9]+$/") is False

    def test_regex_search_not_fullmatch(self):
        from langsmith_cli.commands.runs import _metadata_value_matches

        # Regex uses search, not fullmatch
        assert _metadata_value_matches("prefix-room-suffix", "/room/") is True

    def test_invalid_regex_falls_back_to_exact(self):
        from langsmith_cli.commands.runs import _metadata_value_matches

        # Invalid regex should fall back to exact match
        assert _metadata_value_matches("/[invalid/", "/[invalid/") is True
        assert _metadata_value_matches("other", "/[invalid/") is False


class TestUsageMetadataWildcard:
    """Tests for wildcard/regex metadata filtering in usage from-cache mode."""

    def test_wildcard_metadata_filter(self, runner, mock_client, tmp_path, monkeypatch):
        """INVARIANT: --metadata key=pattern* matches runs with wildcard."""
        monkeypatch.setattr("langsmith_cli.cache.get_cache_dir", lambda: tmp_path)
        from langsmith_cli.cache import append_runs_to_cache

        runs = [
            _create_llm_run(1, channel_id="room-A"),
            _create_llm_run(2, channel_id="room-B"),
            _create_llm_run(3, channel_id="lobby-C"),
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
                "--metadata",
                "channel_id=room-*",
            ],
        )
        assert result.exit_code == 0
        data = _extract_json(result.output)
        assert data["summary"]["run_count"] == 2

    def test_regex_metadata_filter(self, runner, mock_client, tmp_path, monkeypatch):
        """INVARIANT: --metadata key=/regex/ matches runs with regex."""
        monkeypatch.setattr("langsmith_cli.cache.get_cache_dir", lambda: tmp_path)
        from langsmith_cli.cache import append_runs_to_cache

        runs = [
            _create_llm_run(1, channel_id="room-123"),
            _create_llm_run(2, channel_id="room-456"),
            _create_llm_run(3, channel_id="room-abc"),
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
                "--metadata",
                "channel_id=/^room-[0-9]+$/",
            ],
        )
        assert result.exit_code == 0
        data = _extract_json(result.output)
        assert data["summary"]["run_count"] == 2


class TestUsageCsvFormat:
    """Tests for --format csv output on runs usage command."""

    def test_csv_format_produces_valid_output(self, runner, mock_client):
        """INVARIANT: --format csv produces valid CSV output."""
        import csv
        import io

        mock_client.list_runs.return_value = [
            _create_llm_run(1, hour=16, model="gpt-4"),
            _create_llm_run(2, hour=17, model="claude-3"),
        ]

        result = runner.invoke(
            cli,
            ["-qq", "runs", "usage", "--last", "24h", "--format", "csv"],
        )
        assert result.exit_code == 0
        # Parse as CSV — should not raise
        reader = csv.DictReader(io.StringIO(result.output.strip()))
        rows = list(reader)
        assert len(rows) >= 1
        # Verify expected columns exist
        assert "time" in rows[0]
        assert "run_count" in rows[0]


class TestUsageProviderGatewayBreakdown:
    """Tests for --breakdown provider and --breakdown gateway dimensions."""

    def test_breakdown_by_provider_from_model_name(self, runner, mock_client):
        """INVARIANT: Provider is inferred from model name prefix."""
        runs = [
            _create_llm_run(1, model="gpt-4", total_tokens=100),
            _create_llm_run(2, model="gemini-2.5-flash", total_tokens=200),
            _create_llm_run(3, model="llama-3.3-70b", total_tokens=300),
        ]
        mock_client.list_runs.return_value = runs

        result = runner.invoke(
            cli,
            ["--json", "runs", "usage", "--breakdown", "provider", "--last", "24h"],
        )

        assert result.exit_code == 0
        data = _extract_json(result.output)
        buckets = data["buckets"]
        providers = {b["provider"] for b in buckets}
        assert providers == {"OpenAI", "Google", "Meta"}

    def test_breakdown_by_provider_gpt_oss_is_openai(self, runner, mock_client):
        """INVARIANT: gpt-oss is an OpenAI open-source model (served via Cerebras gateway)."""
        runs = [
            _create_llm_run(1, model="gpt-oss-120b", ls_provider="cerebras"),
        ]
        mock_client.list_runs.return_value = runs

        result = runner.invoke(
            cli,
            [
                "--json",
                "runs",
                "usage",
                "--breakdown",
                "provider",
                "--breakdown",
                "gateway",
                "--last",
                "24h",
            ],
        )

        assert result.exit_code == 0
        data = _extract_json(result.output)
        assert data["buckets"][0]["provider"] == "OpenAI"
        assert data["buckets"][0]["gateway"] == "cerebras"

    def test_breakdown_by_provider_falls_back_to_gateway(self, runner, mock_client):
        """INVARIANT: Unknown model names fall back to gateway as provider."""
        runs = [
            _create_llm_run(1, model="custom-proprietary-v2", ls_provider="cerebras"),
        ]
        mock_client.list_runs.return_value = runs

        result = runner.invoke(
            cli,
            ["--json", "runs", "usage", "--breakdown", "provider", "--last", "24h"],
        )

        assert result.exit_code == 0
        data = _extract_json(result.output)
        buckets = data["buckets"]
        assert buckets[0]["provider"] == "Cerebras"

    def test_breakdown_by_gateway(self, runner, mock_client):
        """INVARIANT: Gateway is extracted from ls_provider metadata."""
        runs = [
            _create_llm_run(1, model="llama-3.3-70b", ls_provider="groq"),
            _create_llm_run(2, model="llama-3.3-70b", ls_provider="cerebras"),
            _create_llm_run(3, model="gpt-4", ls_provider="openai"),
        ]
        mock_client.list_runs.return_value = runs

        result = runner.invoke(
            cli,
            ["--json", "runs", "usage", "--breakdown", "gateway", "--last", "24h"],
        )

        assert result.exit_code == 0
        data = _extract_json(result.output)
        buckets = data["buckets"]
        gateways = {b["gateway"] for b in buckets}
        assert gateways == {"groq", "cerebras", "openai"}

    def test_breakdown_by_gateway_unknown_when_missing(self, runner, mock_client):
        """INVARIANT: Missing ls_provider yields 'unknown' gateway."""
        runs = [_create_llm_run(1, model="gpt-4")]
        mock_client.list_runs.return_value = runs

        result = runner.invoke(
            cli,
            ["--json", "runs", "usage", "--breakdown", "gateway", "--last", "24h"],
        )

        assert result.exit_code == 0
        data = _extract_json(result.output)
        assert data["buckets"][0]["gateway"] == "unknown"

    def test_breakdown_provider_and_gateway_together(self, runner, mock_client):
        """INVARIANT: Provider and gateway can be used as simultaneous breakdowns."""
        runs = [
            _create_llm_run(1, model="llama-3.3-70b", ls_provider="groq"),
            _create_llm_run(2, model="llama-3.3-70b", ls_provider="cerebras"),
        ]
        mock_client.list_runs.return_value = runs

        result = runner.invoke(
            cli,
            [
                "--json",
                "runs",
                "usage",
                "--breakdown",
                "provider",
                "--breakdown",
                "gateway",
                "--last",
                "24h",
            ],
        )

        assert result.exit_code == 0
        data = _extract_json(result.output)
        buckets = data["buckets"]
        assert len(buckets) == 2
        assert all("provider" in b for b in buckets)
        assert all("gateway" in b for b in buckets)
        # Both are Meta but different gateways
        assert all(b["provider"] == "Meta" for b in buckets)
        gateways = {b["gateway"] for b in buckets}
        assert gateways == {"groq", "cerebras"}

    def test_provider_prefix_matching(self, runner, mock_client):
        """INVARIANT: Provider detection handles slash-separated model names."""
        runs = [
            _create_llm_run(
                1, model="meta-llama/llama-3.3-70b-instruct", ls_provider="openrouter"
            ),
        ]
        mock_client.list_runs.return_value = runs

        result = runner.invoke(
            cli,
            ["--json", "runs", "usage", "--breakdown", "provider", "--last", "24h"],
        )

        assert result.exit_code == 0
        data = _extract_json(result.output)
        assert data["buckets"][0]["provider"] == "Meta"


class TestUsagePromptCompletionCost:
    """Tests for prompt_cost and completion_cost in usage output."""

    def test_prompt_completion_cost_aggregated(self, runner, mock_client):
        """INVARIANT: prompt_cost and completion_cost are summed in buckets."""
        runs = [
            _create_llm_run(
                1,
                cost="0.05",
                prompt_cost="0.02",
                completion_cost="0.03",
            ),
            _create_llm_run(
                2,
                cost="0.10",
                prompt_cost="0.04",
                completion_cost="0.06",
            ),
        ]
        mock_client.list_runs.return_value = runs

        result = runner.invoke(
            cli,
            ["--json", "runs", "usage", "--last", "24h"],
        )

        assert result.exit_code == 0
        data = _extract_json(result.output)
        bucket = data["buckets"][0]
        assert bucket["prompt_cost"] == pytest.approx(0.06, rel=0.001)
        assert bucket["completion_cost"] == pytest.approx(0.09, rel=0.001)

    def test_prompt_completion_cost_none_treated_as_zero(self, runner, mock_client):
        """INVARIANT: None prompt_cost/completion_cost are treated as zero."""
        runs = [
            _create_llm_run(1, cost="0.05"),  # No prompt_cost/completion_cost
        ]
        mock_client.list_runs.return_value = runs

        result = runner.invoke(
            cli,
            ["--json", "runs", "usage", "--last", "24h"],
        )

        assert result.exit_code == 0
        data = _extract_json(result.output)
        bucket = data["buckets"][0]
        assert bucket["prompt_cost"] == pytest.approx(0.0)
        assert bucket["completion_cost"] == pytest.approx(0.0)

    def test_prompt_completion_cost_in_summary(self, runner, mock_client):
        """INVARIANT: Summary includes prompt_cost and completion_cost."""
        runs = [
            _create_llm_run(1, cost="0.10", prompt_cost="0.04", completion_cost="0.06"),
        ]
        mock_client.list_runs.return_value = runs

        result = runner.invoke(
            cli,
            ["--json", "runs", "usage", "--last", "24h"],
        )

        assert result.exit_code == 0
        data = _extract_json(result.output)
        summary = data["summary"]
        assert "prompt_cost" in summary
        assert "completion_cost" in summary
        assert summary["prompt_cost"] == pytest.approx(0.04, rel=0.001)
        assert summary["completion_cost"] == pytest.approx(0.06, rel=0.001)


class TestUsageEmptyResults:
    """Tests for usage command when no data matches filters."""

    def test_json_output_has_summary_when_no_runs(self, runner, mock_client):
        """INVARIANT: --json always outputs parseable JSON with summary, even when no runs match."""
        mock_client.list_runs.return_value = []

        result = runner.invoke(cli, ["--json", "runs", "usage", "--last", "24h"])
        assert result.exit_code == 0
        data = _extract_json(result.output)
        assert "summary" in data
        assert data["summary"]["run_count"] == 0
        assert data["summary"]["total_tokens"] == 0
        assert data["summary"]["total_cost"] == 0

    def test_json_output_has_summary_when_no_model_runs(self, runner, mock_client):
        """INVARIANT: --json outputs empty summary when runs exist but none have model info."""
        # Chain run without model metadata — will be filtered out
        run_no_model = Run(
            id=UUID(make_run_id(1)),
            name="chain-wrapper",
            run_type="llm",
            start_time=datetime(2026, 3, 9, 16, 0, 0, tzinfo=timezone.utc),
            total_tokens=1000,
            extra={"metadata": {}},
        )
        mock_client.list_runs.return_value = [run_no_model]

        result = runner.invoke(cli, ["--json", "runs", "usage", "--last", "24h"])
        assert result.exit_code == 0
        data = _extract_json(result.output)
        assert "summary" in data
        assert data["summary"]["run_count"] == 0


class TestUsageApplyPricing:
    """Tests for --apply-pricing with external YAML pricing file."""

    def test_apply_pricing_fills_missing_costs(self, runner, mock_client, tmp_path):
        """INVARIANT: --apply-pricing estimates costs for runs with $0 cost but non-zero tokens."""
        runs = [
            _create_llm_run(
                1,
                model="llama-3.3-70b-versatile",
                total_tokens=1000,
                prompt_tokens=700,
                completion_tokens=300,
                cost="0",
            ),
        ]
        mock_client.list_runs.return_value = runs

        pricing_file = tmp_path / "pricing.yaml"
        pricing_file.write_text(
            "llama-3.3-70b-versatile:\n"
            "  input_per_million: 0.59\n"
            "  output_per_million: 0.79\n"
        )

        result = runner.invoke(
            cli,
            [
                "--json",
                "runs",
                "usage",
                "--last",
                "24h",
                "--apply-pricing",
                str(pricing_file),
            ],
        )

        assert result.exit_code == 0
        data = _extract_json(result.output)
        bucket = data["buckets"][0]
        assert bucket["prompt_cost"] == pytest.approx(0.000413, rel=0.01)
        assert bucket["completion_cost"] == pytest.approx(0.000237, rel=0.01)
        assert bucket["total_cost"] == pytest.approx(0.00065, rel=0.01)

    def test_apply_pricing_does_not_override_existing_costs(
        self, runner, mock_client, tmp_path
    ):
        """INVARIANT: Runs with existing costs from LangSmith are not overridden."""
        runs = [
            _create_llm_run(
                1,
                model="gpt-4",
                total_tokens=1000,
                prompt_tokens=700,
                completion_tokens=300,
                cost="0.05",
                prompt_cost="0.02",
                completion_cost="0.03",
            ),
        ]
        mock_client.list_runs.return_value = runs

        pricing_file = tmp_path / "pricing.yaml"
        pricing_file.write_text(
            "gpt-4:\n  input_per_million: 999.0\n  output_per_million: 999.0\n"
        )

        result = runner.invoke(
            cli,
            [
                "--json",
                "runs",
                "usage",
                "--last",
                "24h",
                "--apply-pricing",
                str(pricing_file),
            ],
        )

        assert result.exit_code == 0
        data = _extract_json(result.output)
        bucket = data["buckets"][0]
        assert bucket["prompt_cost"] == pytest.approx(0.02, rel=0.001)
        assert bucket["completion_cost"] == pytest.approx(0.03, rel=0.001)

    def test_apply_pricing_model_not_in_file(self, runner, mock_client, tmp_path):
        """INVARIANT: Models not in pricing file keep their original $0 cost."""
        runs = [
            _create_llm_run(1, model="unknown-model", cost="0"),
        ]
        mock_client.list_runs.return_value = runs

        pricing_file = tmp_path / "pricing.yaml"
        pricing_file.write_text(
            "gpt-4:\n  input_per_million: 2.5\n  output_per_million: 10.0\n"
        )

        result = runner.invoke(
            cli,
            [
                "--json",
                "runs",
                "usage",
                "--last",
                "24h",
                "--apply-pricing",
                str(pricing_file),
            ],
        )

        assert result.exit_code == 0
        data = _extract_json(result.output)
        bucket = data["buckets"][0]
        assert bucket["total_cost"] == pytest.approx(0.0)


class TestGetServiceTier:
    """Unit tests for _get_service_tier helper."""

    def test_returns_tier_from_invocation_params(self):
        run = _create_llm_run(1, service_tier="priority")
        assert _get_service_tier(run) == "priority"

    def test_returns_unknown_when_not_set(self):
        run = _create_llm_run(1)
        assert _get_service_tier(run) == "unknown"

    def test_returns_unknown_when_extra_is_none(self):
        run = Run(
            id=UUID(make_run_id(99)),
            name="test",
            run_type="llm",
            start_time=datetime(2026, 3, 9, 16, 0, 0, tzinfo=timezone.utc),
            extra=None,
        )
        assert _get_service_tier(run) == "unknown"

    def test_returns_flex_tier(self):
        run = _create_llm_run(1, service_tier="flex")
        assert _get_service_tier(run) == "flex"

    def test_returns_default_tier(self):
        run = _create_llm_run(1, service_tier="default")
        assert _get_service_tier(run) == "default"


class TestUsageServiceTierBreakdown:
    """Tests for --breakdown service_tier dimension."""

    def test_breakdown_by_service_tier_groups_runs(self, runner, mock_client):
        """INVARIANT: --breakdown service_tier separates runs by their tier value."""
        runs = [
            _create_llm_run(1, total_tokens=100, service_tier="priority"),
            _create_llm_run(2, total_tokens=200, service_tier="default"),
            _create_llm_run(3, total_tokens=400, service_tier="priority"),
        ]
        mock_client.list_runs.return_value = runs

        result = runner.invoke(
            cli,
            ["--json", "runs", "usage", "--breakdown", "service_tier", "--last", "24h"],
        )

        assert result.exit_code == 0
        data = _extract_json(result.output)
        buckets = data["buckets"]
        tiers = {b["service_tier"] for b in buckets}
        assert tiers == {"priority", "default"}
        priority_bucket = next(b for b in buckets if b["service_tier"] == "priority")
        assert priority_bucket["total_tokens"] == 500

    def test_breakdown_service_tier_unknown_when_not_set(self, runner, mock_client):
        """INVARIANT: Runs without service_tier get 'unknown' as tier value."""
        runs = [_create_llm_run(1, total_tokens=1000)]
        mock_client.list_runs.return_value = runs

        result = runner.invoke(
            cli,
            ["--json", "runs", "usage", "--breakdown", "service_tier", "--last", "24h"],
        )

        assert result.exit_code == 0
        data = _extract_json(result.output)
        assert data["buckets"][0]["service_tier"] == "unknown"

    def test_breakdown_service_tier_combined_with_model(self, runner, mock_client):
        """INVARIANT: service_tier and model breakdowns can be combined."""
        runs = [
            _create_llm_run(
                1, model="gpt-5-nano", total_tokens=100, service_tier="priority"
            ),
            _create_llm_run(
                2, model="gpt-5-nano", total_tokens=200, service_tier="default"
            ),
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
                "service_tier",
                "--last",
                "24h",
            ],
        )

        assert result.exit_code == 0
        data = _extract_json(result.output)
        assert len(data["buckets"]) == 2
        assert all("model" in b for b in data["buckets"])
        assert all("service_tier" in b for b in data["buckets"])


class TestUsageApplyPricingWithTiers:
    """Tests for --apply-pricing with model+tier compound keys."""

    def test_tier_compound_key_takes_precedence_over_base_model(
        self, runner, mock_client, tmp_path
    ):
        """INVARIANT: model+tier key is used when present, overriding plain model key."""
        runs = [
            _create_llm_run(
                1,
                model="gpt-5-nano",
                total_tokens=1000,
                prompt_tokens=700,
                completion_tokens=300,
                cost="0",
                service_tier="priority",
            ),
        ]
        mock_client.list_runs.return_value = runs

        pricing_file = tmp_path / "pricing.yaml"
        pricing_file.write_text(
            "gpt-5-nano:\n"
            "  input_per_million: 1.10\n"
            "  output_per_million: 4.40\n"
            "gpt-5-nano+priority:\n"
            "  input_per_million: 2.20\n"
            "  output_per_million: 8.80\n"
        )

        result = runner.invoke(
            cli,
            [
                "--json",
                "runs",
                "usage",
                "--last",
                "24h",
                "--apply-pricing",
                str(pricing_file),
            ],
        )

        assert result.exit_code == 0
        data = _extract_json(result.output)
        bucket = data["buckets"][0]
        # priority pricing: 700 * 2.20/1M = 0.00154; 300 * 8.80/1M = 0.00264
        assert bucket["prompt_cost"] == pytest.approx(0.00154, rel=0.01)
        assert bucket["completion_cost"] == pytest.approx(0.00264, rel=0.01)

    def test_apply_pricing_falls_back_to_base_model_when_no_tier_key(
        self, runner, mock_client, tmp_path
    ):
        """INVARIANT: Falls back to plain model key when no tier-specific entry exists."""
        runs = [
            _create_llm_run(
                1,
                model="gpt-5-nano",
                total_tokens=1000,
                prompt_tokens=700,
                completion_tokens=300,
                cost="0",
                service_tier="priority",
            ),
        ]
        mock_client.list_runs.return_value = runs

        pricing_file = tmp_path / "pricing.yaml"
        pricing_file.write_text(
            "gpt-5-nano:\n  input_per_million: 1.10\n  output_per_million: 4.40\n"
        )

        result = runner.invoke(
            cli,
            [
                "--json",
                "runs",
                "usage",
                "--last",
                "24h",
                "--apply-pricing",
                str(pricing_file),
            ],
        )

        assert result.exit_code == 0
        data = _extract_json(result.output)
        bucket = data["buckets"][0]
        # base pricing: 700 * 1.10/1M = 0.00077; 300 * 4.40/1M = 0.00132
        assert bucket["prompt_cost"] == pytest.approx(0.00077, rel=0.01)
        assert bucket["completion_cost"] == pytest.approx(0.00132, rel=0.01)

    def test_apply_pricing_unknown_tier_skips_tier_key_lookup(
        self, runner, mock_client, tmp_path
    ):
        """INVARIANT: When service_tier is 'unknown', only the base model key is tried."""
        runs = [
            _create_llm_run(
                1,
                model="gpt-5-nano",
                total_tokens=1000,
                prompt_tokens=700,
                completion_tokens=300,
                cost="0",
            ),
        ]
        mock_client.list_runs.return_value = runs

        pricing_file = tmp_path / "pricing.yaml"
        pricing_file.write_text(
            "gpt-5-nano:\n"
            "  input_per_million: 1.10\n"
            "  output_per_million: 4.40\n"
            "gpt-5-nano+unknown:\n"
            "  input_per_million: 999.0\n"
            "  output_per_million: 999.0\n"
        )

        result = runner.invoke(
            cli,
            [
                "--json",
                "runs",
                "usage",
                "--last",
                "24h",
                "--apply-pricing",
                str(pricing_file),
            ],
        )

        assert result.exit_code == 0
        data = _extract_json(result.output)
        bucket = data["buckets"][0]
        # Should use base pricing, not gpt-5-nano+unknown
        assert bucket["prompt_cost"] == pytest.approx(0.00077, rel=0.01)
        assert bucket["completion_cost"] == pytest.approx(0.00132, rel=0.01)

    def test_apply_pricing_flex_tier_uses_tier_compound_key(
        self, runner, mock_client, tmp_path
    ):
        """INVARIANT: flex tier uses model+flex key when available."""
        runs = [
            _create_llm_run(
                1,
                model="gpt-5-nano",
                total_tokens=1000,
                prompt_tokens=700,
                completion_tokens=300,
                cost="0",
                service_tier="flex",
            ),
        ]
        mock_client.list_runs.return_value = runs

        pricing_file = tmp_path / "pricing.yaml"
        pricing_file.write_text(
            "gpt-5-nano:\n"
            "  input_per_million: 1.10\n"
            "  output_per_million: 4.40\n"
            "gpt-5-nano+flex:\n"
            "  input_per_million: 0.55\n"
            "  output_per_million: 2.20\n"
        )

        result = runner.invoke(
            cli,
            [
                "--json",
                "runs",
                "usage",
                "--last",
                "24h",
                "--apply-pricing",
                str(pricing_file),
            ],
        )

        assert result.exit_code == 0
        data = _extract_json(result.output)
        bucket = data["buckets"][0]
        # flex pricing: 700 * 0.55/1M = 0.000385; 300 * 2.20/1M = 0.00066
        assert bucket["prompt_cost"] == pytest.approx(0.000385, rel=0.01)
        assert bucket["completion_cost"] == pytest.approx(0.00066, rel=0.01)


class TestUsageOutputOption:
    """Tests for runs usage --output file writing."""

    def test_usage_output_writes_jsonl_to_file(self, runner, mock_client, tmp_path):
        """INVARIANT: --output writes usage bucket results as JSONL to the specified file."""
        runs = [_create_llm_run(1, model="gpt-4", total_tokens=500)]
        mock_client.list_runs.return_value = runs
        output_file = tmp_path / "usage.jsonl"

        result = runner.invoke(
            cli,
            [
                "--json",
                "runs",
                "usage",
                "--project",
                "test-proj",
                "--last",
                "24h",
                "--output",
                str(output_file),
            ],
        )

        assert result.exit_code == 0
        assert output_file.exists(), "--output file was not created"
        lines = [ln for ln in output_file.read_text().splitlines() if ln.strip()]
        assert len(lines) >= 1
        bucket = json.loads(lines[0])
        assert "total_tokens" in bucket

    def test_usage_output_option_accepted_without_error(
        self, runner, mock_client, tmp_path
    ):
        """INVARIANT: --output is a recognized option for runs usage (no NoSuchOption error)."""
        mock_client.list_runs.return_value = []
        output_file = tmp_path / "out.jsonl"

        result = runner.invoke(
            cli,
            [
                "runs",
                "usage",
                "--project",
                "test",
                "--last",
                "1h",
                "--output",
                str(output_file),
            ],
        )

        assert result.exit_code == 0, (
            f"--output should be accepted. Got exit_code={result.exit_code}, output={result.output!r}"
        )
