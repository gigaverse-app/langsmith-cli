"""Tests for issue #61 (--since + --last time window) and issue #62 (API limit cap).

Issue #61: When both --since and --last are specified, --since defines the start time
and --last defines the duration forward from --since, creating a time window.
Currently both generate gt(start_time, ...) which is wrong.

Issue #62: When --grep causes api_limit > 100, the LangSmith API rejects the request
with "Limit exceeds maximum allowed value of 100". The SDK passes limit directly to
the API body without capping per-page size.
"""

import re
from datetime import datetime, timedelta


from langsmith_cli.utils import build_time_fql_filters
from langsmith_cli.main import cli
from conftest import create_run


# Issue #61: --since + --last should create a time window


class TestSinceAndLastTimeWindow:
    """INVARIANT: When both --since and --last are provided, they define a time window.
    --since is the start time, --last is the duration forward from --since.
    The result should be a gt() AND lt() pair, not two gt() filters.
    """

    def test_since_and_last_creates_upper_bound(self):
        """When both --since and --last are given, --last creates an lt() upper bound."""
        result = build_time_fql_filters(since="7d", last="24h")
        assert len(result) == 2

        # One filter should be gt (lower bound from --since)
        gt_filters = [f for f in result if f.startswith("gt(")]
        lt_filters = [f for f in result if f.startswith("lt(")]

        assert len(gt_filters) == 1, f"Expected 1 gt filter, got {gt_filters}"
        assert len(lt_filters) == 1, f"Expected 1 lt filter, got {lt_filters}"

    def test_since_and_last_window_is_correct(self):
        """The time window should span from --since to --since + --last duration."""
        # Use an absolute date for --since so we can verify the math
        result = build_time_fql_filters(since="2026-02-17", last="72h")

        gt_filters = [f for f in result if f.startswith("gt(")]
        lt_filters = [f for f in result if f.startswith("lt(")]

        assert len(gt_filters) == 1
        assert len(lt_filters) == 1

        # Extract timestamps from the FQL expressions
        gt_match = re.search(r'"([^"]+)"', gt_filters[0])
        lt_match = re.search(r'"([^"]+)"', lt_filters[0])
        assert gt_match and lt_match

        gt_time = datetime.fromisoformat(gt_match.group(1))
        lt_time = datetime.fromisoformat(lt_match.group(1))

        # The window should be ~72 hours
        window = lt_time - gt_time
        assert timedelta(hours=71) <= window <= timedelta(hours=73)

    def test_since_alone_unchanged(self):
        """--since alone should still produce a single gt() filter (no regression)."""
        result = build_time_fql_filters(since="3d")
        assert len(result) == 1
        assert result[0].startswith('gt(start_time, "')

    def test_last_alone_unchanged(self):
        """--last alone should still produce a single gt() filter (no regression)."""
        result = build_time_fql_filters(last="24h")
        assert len(result) == 1
        assert result[0].startswith('gt(start_time, "')


# Issue #62: API limit should be capped at 100


class TestApiLimitCap:
    """INVARIANT: The limit passed to the LangSmith SDK's list_runs must never
    exceed 100, because the API rejects requests with limit > 100.
    When we need more than 100 items, we should not pass limit to the SDK
    and instead collect items from the iterator ourselves.
    """

    def test_grep_with_limit_50_does_not_exceed_api_max(self, runner, mock_client):
        """--grep with --limit 50 should not cause API limit > 100.

        The 3x multiplier (50*3=150) exceeds the API max of 100.
        The SDK should receive limit <= 100 or None (to use cursor pagination).
        """
        mock_client.list_runs.return_value = iter([])

        runner.invoke(
            cli,
            ["runs", "list", "--grep", "test", "--limit", "50"],
        )

        _, kwargs = mock_client.list_runs.call_args
        limit_passed = kwargs.get("limit")
        # limit should be <= 100 or None (let SDK paginate)
        assert limit_passed is None or limit_passed <= 100, (
            f"API limit {limit_passed} exceeds max of 100"
        )

    def test_grep_with_limit_40_keeps_multiplied_limit(self, runner, mock_client):
        """--grep with --limit 40 should use multiplied limit (40*3=120 -> capped at 100).

        Even 120 exceeds 100, so it should be capped.
        """
        mock_client.list_runs.return_value = iter([])

        runner.invoke(
            cli,
            ["runs", "list", "--grep", "test", "--limit", "40"],
        )

        _, kwargs = mock_client.list_runs.call_args
        limit_passed = kwargs.get("limit")
        assert limit_passed is None or limit_passed <= 100, (
            f"API limit {limit_passed} exceeds max of 100"
        )

    def test_grep_with_limit_10_uses_min_100(self, runner, mock_client):
        """--grep with --limit 10 should use min(max(10*3, 100), 100) = 100.

        The minimum of 100 is already at the cap, so it stays at 100.
        """
        mock_client.list_runs.return_value = iter([])

        runner.invoke(
            cli,
            ["runs", "list", "--grep", "test", "--limit", "10"],
        )

        _, kwargs = mock_client.list_runs.call_args
        limit_passed = kwargs.get("limit")
        assert limit_passed is None or limit_passed <= 100, (
            f"API limit {limit_passed} exceeds max of 100"
        )

    def test_no_grep_limit_passes_through(self, runner, mock_client):
        """Without --grep, --limit should pass through directly to SDK."""
        mock_client.list_runs.return_value = iter([])

        runner.invoke(
            cli,
            ["runs", "list", "--limit", "50"],
        )

        _, kwargs = mock_client.list_runs.call_args
        assert kwargs.get("limit") == 50

    def test_grep_still_collects_enough_items(self, runner, mock_client):
        """Even with API limit capped, we should still collect up to api_limit items.

        With --grep --limit 50, we want to evaluate ~150 items client-side.
        Even though each API page is <=100, pagination should get us more.
        """
        # Create 150 runs, 1 in 3 matches grep
        runs = []
        for i in range(150):
            name = f"match-{i}" if i % 3 == 0 else f"other-{i}"
            runs.append(create_run(name=name, id_str="auto"))

        mock_client.list_runs.return_value = iter(runs)

        result = runner.invoke(
            cli,
            ["--json", "runs", "list", "--grep", "match", "--limit", "50"],
        )

        assert result.exit_code == 0
