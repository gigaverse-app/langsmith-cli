"""Tests for runs analyze command and grouping helper functions."""

import json
from datetime import datetime, timedelta, timezone
from uuid import UUID

import click
import pytest
from langsmith.schemas import Run

from conftest import create_run, make_run_id
from langsmith_cli.main import cli


class TestGroupingHelpers:
    """Tests for grouping helper functions."""

    def test_parse_grouping_field_valid(self):
        """Parse valid grouping field specifications."""
        from langsmith_cli.commands.runs import parse_grouping_field

        assert parse_grouping_field("tag:length_category") == ("tag", "length_category")
        assert parse_grouping_field("metadata:user_tier") == ("metadata", "user_tier")

    def test_parse_grouping_field_invalid(self):
        """Parse invalid grouping field specifications."""
        from langsmith_cli.commands.runs import parse_grouping_field

        with pytest.raises(click.BadParameter, match="Invalid grouping format"):
            parse_grouping_field("invalid")

        with pytest.raises(click.BadParameter, match="Invalid grouping type"):
            parse_grouping_field("unknown:field")

        with pytest.raises(click.BadParameter, match="Field name cannot be empty"):
            parse_grouping_field("tag:")

    def test_parse_grouping_field_single_dimension(self):
        """Single dimension returns tuple."""
        from langsmith_cli.commands.runs import parse_grouping_field

        assert parse_grouping_field("tag:length") == ("tag", "length")
        assert parse_grouping_field("metadata:user_tier") == ("metadata", "user_tier")

    def test_parse_grouping_field_multi_dimensional(self):
        """Multiple dimensions return list of tuples."""
        from langsmith_cli.commands.runs import parse_grouping_field

        assert parse_grouping_field("tag:length,tag:content_type") == [
            ("tag", "length"),
            ("tag", "content_type"),
        ]
        assert parse_grouping_field("tag:length,metadata:user_tier") == [
            ("tag", "length"),
            ("metadata", "user_tier"),
        ]

    def test_build_grouping_fql_filter_tag(self):
        """Build FQL filter for tag-based grouping."""
        from langsmith_cli.commands.runs import build_grouping_fql_filter

        assert (
            build_grouping_fql_filter("tag", "length_category", "short")
            == 'has(tags, "length_category:short")'
        )

    def test_build_grouping_fql_filter_metadata(self):
        """Build FQL filter for metadata-based grouping."""
        from langsmith_cli.commands.runs import build_grouping_fql_filter

        result = build_grouping_fql_filter("metadata", "user_tier", "premium")
        assert (
            result
            == 'and(in(metadata_key, ["user_tier"]), eq(metadata_value, "premium"))'
        )

    def test_build_multi_dimensional_fql_filter(self):
        """Build combined FQL filters."""
        from langsmith_cli.commands.runs import build_multi_dimensional_fql_filter

        result = build_multi_dimensional_fql_filter(
            [("tag", "length"), ("tag", "content_type")], ["short", "news"]
        )
        assert (
            result == 'and(has(tags, "length:short"), has(tags, "content_type:news"))'
        )

        # Single dimension should not use 'and'
        result = build_multi_dimensional_fql_filter([("tag", "length")], ["medium"])
        assert result == 'has(tags, "length:medium")'

        # Mixed tag and metadata
        result = build_multi_dimensional_fql_filter(
            [("tag", "length"), ("metadata", "user_tier")], ["short", "premium"]
        )
        assert 'has(tags, "length:short")' in result
        assert 'in(metadata_key, ["user_tier"])' in result

    def test_build_multi_dimensional_fql_filter_validation(self):
        """Dimension/value length mismatch raises error."""
        from langsmith_cli.commands.runs import build_multi_dimensional_fql_filter

        with pytest.raises(
            ValueError, match="Dimensions and values must have same length"
        ):
            build_multi_dimensional_fql_filter(
                [("tag", "length"), ("tag", "content_type")], ["short"]
            )

    def test_extract_group_value_from_tags(self):
        """Extract group value from run tags."""
        from langsmith_cli.commands.runs import extract_group_value

        run = Run(
            id=UUID(make_run_id(1)),
            name="test",
            run_type="chain",
            start_time=datetime.now(timezone.utc),
            tags=["env:prod", "length_category:short", "user:123"],
        )

        assert extract_group_value(run, "tag", "length_category") == "short"
        assert extract_group_value(run, "tag", "nonexistent") is None

    def test_extract_group_value_from_metadata(self):
        """Extract group value from run metadata."""
        from langsmith_cli.commands.runs import extract_group_value

        run = Run(
            id=UUID(make_run_id(2)),
            name="test",
            run_type="chain",
            start_time=datetime.now(timezone.utc),
            extra={"metadata": {"user_tier": "premium", "region": "us-east"}},
        )

        assert extract_group_value(run, "metadata", "user_tier") == "premium"
        assert extract_group_value(run, "metadata", "nonexistent") is None


class TestComputeMetrics:
    """Tests for compute_metrics function."""

    def test_compute_count(self):
        """Compute count metric."""
        from langsmith_cli.commands.runs import compute_metrics

        runs = [create_run(name=f"run-{i}", id_str=make_run_id(i)) for i in range(5)]
        metrics = compute_metrics(runs, ["count"])
        assert metrics["count"] == 5

    def test_compute_error_rate(self):
        """Compute error rate metric."""
        from langsmith_cli.commands.runs import compute_metrics

        runs = [
            create_run(name="success", id_str=make_run_id(1)),
            create_run(name="error", id_str=make_run_id(2), error="Failed"),
            create_run(name="success2", id_str=make_run_id(3)),
        ]
        metrics = compute_metrics(runs, ["error_rate"])
        assert metrics["error_rate"] == pytest.approx(1 / 3, rel=0.01)

    def test_compute_latency_percentiles(self):
        """Compute latency percentile metrics."""
        from langsmith_cli.commands.runs import compute_metrics

        start_time = datetime.now(timezone.utc)
        runs = [
            Run(
                id=UUID(int=i),
                name=f"run-{i}",
                run_type="chain",
                start_time=start_time,
                end_time=start_time + timedelta(seconds=float(i)),
            )
            for i in range(1, 101)
        ]

        metrics = compute_metrics(runs, ["p50_latency", "p95_latency", "p99_latency"])
        assert metrics["p50_latency"] == pytest.approx(50.5, rel=0.1)
        assert metrics["p95_latency"] >= 95.0
        assert metrics["p99_latency"] >= 99.0


class TestRunsAnalyze:
    """Tests for runs analyze command."""

    def test_analyze_basic(self, runner, mock_client):
        """Basic analyze command groups and computes metrics."""
        start_time = datetime.now(timezone.utc)
        runs = [
            Run(
                id=UUID(int=i + 1),
                name=f"run-{i}",
                run_type="chain",
                start_time=start_time,
                end_time=start_time + timedelta(seconds=1.0 + i * 0.1),
                tags=["length_category:short"],
                error=None if i % 2 == 0 else "Error",
            )
            for i in range(10)
        ]
        mock_client.list_runs.return_value = runs

        result = runner.invoke(
            cli,
            [
                "--json",
                "runs",
                "analyze",
                "--group-by",
                "tag:length_category",
                "--metrics",
                "count,error_rate,p50_latency",
            ],
        )

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["group"] == "length_category:short"
        assert data[0]["count"] == 10
        assert data[0]["error_rate"] == 0.5

    def test_analyze_multiple_groups(self, runner, mock_client):
        """Analyze with multiple groups."""
        start_time = datetime.now(timezone.utc)
        short_runs = [
            Run(
                id=UUID(int=i + 1),
                name=f"short-{i}",
                run_type="chain",
                start_time=start_time,
                end_time=start_time + timedelta(seconds=1.0),
                tags=["length_category:short"],
            )
            for i in range(5)
        ]
        long_runs = [
            Run(
                id=UUID(int=i + 100),
                name=f"long-{i}",
                run_type="chain",
                start_time=start_time,
                end_time=start_time + timedelta(seconds=5.0),
                tags=["length_category:long"],
            )
            for i in range(3)
        ]
        mock_client.list_runs.return_value = short_runs + long_runs

        result = runner.invoke(
            cli,
            [
                "--json",
                "runs",
                "analyze",
                "--group-by",
                "tag:length_category",
                "--metrics",
                "count,avg_latency",
            ],
        )

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data) == 2

        short_group = next(g for g in data if g["group"] == "length_category:short")
        long_group = next(g for g in data if g["group"] == "length_category:long")
        assert short_group["count"] == 5
        assert long_group["count"] == 3
        assert short_group["avg_latency"] == 1.0
        assert long_group["avg_latency"] == 5.0

    def test_analyze_table_output(self, runner, mock_client):
        """Analyze with table output."""
        start_time = datetime.now(timezone.utc)
        runs = [
            Run(
                id=UUID(make_run_id(11)),
                name="run",
                run_type="chain",
                start_time=start_time,
                end_time=start_time + timedelta(seconds=2.5),
                extra={"metadata": {"tier": "premium"}},
            )
        ]
        mock_client.list_runs.return_value = runs

        result = runner.invoke(
            cli,
            [
                "runs",
                "analyze",
                "--group-by",
                "metadata:tier",
                "--metrics",
                "count,p50_latency",
            ],
        )

        assert result.exit_code == 0
        assert "Analysis:" in result.output
        assert "premium" in result.output
        assert "2.5" in result.output or "2.50" in result.output

    def test_analyze_with_filter(self, runner, mock_client):
        """Analyze with additional FQL filter."""
        mock_client.list_runs.return_value = []

        result = runner.invoke(
            cli,
            [
                "--json",
                "runs",
                "analyze",
                "--group-by",
                "tag:category",
                "--metrics",
                "count",
                "--filter",
                'gte(start_time, "2026-01-01")',
            ],
        )

        assert result.exit_code == 0
        _, kwargs = mock_client.list_runs.call_args
        assert 'gte(start_time, "2026-01-01")' in kwargs["filter"]

    def test_analyze_invalid_group_by(self, runner):
        """Invalid group-by format produces error."""
        result = runner.invoke(cli, ["runs", "analyze", "--group-by", "unknown:field"])

        assert result.exit_code != 0
        assert "Invalid grouping type" in result.output

    def test_analyze_multi_dimensional_not_supported(self, runner):
        """Multi-dimensional grouping is not yet supported."""
        result = runner.invoke(
            cli, ["runs", "analyze", "--group-by", "tag:length,tag:content_type"]
        )

        assert result.exit_code != 0
        assert "not yet supported" in result.output
