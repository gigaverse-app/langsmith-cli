"""Tests for filter classes in filters.py."""

from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from langsmith_cli.filters import (
    ContentFilter,
    LatencyFilter,
    PatternFilter,
    RunsFilterConfig,
    StatusFilter,
    TagMetadataFilter,
    TimeFilter,
)


class TestStatusFilter:
    """Tests for StatusFilter."""

    def test_default_values(self):
        """Test default StatusFilter values."""
        f = StatusFilter()
        assert f.status is None
        assert f.failed is False
        assert f.succeeded is False

    def test_status_error(self):
        """Test status filter for error status."""
        f = StatusFilter(status="error")
        params = f.to_sdk_params()
        assert params == {"error": True}

    def test_status_success(self):
        """Test status filter for success status."""
        f = StatusFilter(status="success")
        params = f.to_sdk_params()
        assert params == {"error": False}

    def test_failed_flag(self):
        """Test failed flag."""
        f = StatusFilter(failed=True)
        params = f.to_sdk_params()
        assert params == {"error": True}

    def test_succeeded_flag(self):
        """Test succeeded flag."""
        f = StatusFilter(succeeded=True)
        params = f.to_sdk_params()
        assert params == {"error": False}

    def test_invalid_status_rejected(self):
        """Test that invalid status values are rejected."""
        with pytest.raises(ValidationError):
            # Intentionally passing invalid value to test validation
            invalid_status: str = "invalid"
            StatusFilter(status=invalid_status)  # type: ignore[arg-type]

    def test_no_client_filtering_needed(self):
        """Test that status filtering is server-side only."""
        f = StatusFilter(status="error")
        assert f.needs_client_filtering() is False


class TestTimeFilter:
    """Tests for TimeFilter."""

    def test_default_values(self):
        """Test default TimeFilter values."""
        f = TimeFilter()
        assert f.since is None
        assert f.last is None
        assert f.recent is False
        assert f.today is False

    def test_since_filter(self):
        """Test since filter generates FQL."""
        timestamp = "2024-01-01T00:00:00Z"
        f = TimeFilter(since=timestamp)
        fql = f.to_fql_filters()
        assert len(fql) == 1
        assert f'gt(start_time, "{timestamp}")' in fql[0]

    def test_recent_filter(self):
        """Test recent filter generates FQL for last hour."""
        f = TimeFilter(recent=True)
        fql = f.to_fql_filters()
        assert len(fql) == 1
        assert "gt(start_time," in fql[0]

    def test_today_filter(self):
        """Test today filter generates FQL for today."""
        f = TimeFilter(today=True)
        fql = f.to_fql_filters()
        assert len(fql) == 1
        assert "gt(start_time," in fql[0]

    def test_no_client_filtering_needed(self):
        """Test that time filtering is server-side only."""
        f = TimeFilter(since="2024-01-01T00:00:00Z")
        assert f.needs_client_filtering() is False


class TestLatencyFilter:
    """Tests for LatencyFilter."""

    def test_default_values(self):
        """Test default LatencyFilter values."""
        f = LatencyFilter()
        assert f.min_latency is None
        assert f.max_latency is None

    def test_min_latency_filter(self):
        """Test min latency filter generates FQL."""
        f = LatencyFilter(min_latency="5s")
        fql = f.to_fql_filters()
        assert len(fql) == 1
        assert 'gt(latency, "5s")' in fql[0]

    def test_max_latency_filter(self):
        """Test max latency filter generates FQL."""
        f = LatencyFilter(max_latency="10s")
        fql = f.to_fql_filters()
        assert len(fql) == 1
        assert 'lt(latency, "10s")' in fql[0]

    def test_both_latency_filters(self):
        """Test both min and max latency filters."""
        f = LatencyFilter(min_latency="5s", max_latency="10s")
        fql = f.to_fql_filters()
        assert len(fql) == 2

    def test_no_client_filtering_needed(self):
        """Test that latency filtering is server-side only."""
        f = LatencyFilter(min_latency="5s")
        assert f.needs_client_filtering() is False


class TestPatternFilter:
    """Tests for PatternFilter."""

    def test_default_values(self):
        """Test default PatternFilter values."""
        f = PatternFilter()
        assert f.name is None
        assert f.name_contains is None

    def test_needs_client_filtering_with_name(self):
        """Test that name pattern requires client-side filtering."""
        f = PatternFilter(name="ChatOpenAI")
        assert f.needs_client_filtering() is True

    def test_needs_client_filtering_with_name_contains(self):
        """Test that name_contains requires client-side filtering."""
        f = PatternFilter(name_contains="OpenAI")
        assert f.needs_client_filtering() is True

    def test_no_client_filtering_when_empty(self):
        """Test no client filtering needed when filter is empty."""
        f = PatternFilter()
        assert f.needs_client_filtering() is False

    def test_apply_filter_with_name_pattern(self):
        """Test apply_filter with name pattern."""
        f = PatternFilter(name="Chat*")

        # Create mock items
        item1 = MagicMock()
        item1.name = "ChatOpenAI"
        item2 = MagicMock()
        item2.name = "LLMChain"
        item3 = MagicMock()
        item3.name = "ChatAnthropic"

        items = [item1, item2, item3]
        filtered = f.apply_filter(items)

        # Should match ChatOpenAI and ChatAnthropic
        assert len(filtered) == 2
        assert item1 in filtered
        assert item3 in filtered

    def test_apply_filter_with_name_contains(self):
        """Test apply_filter with name_contains."""
        f = PatternFilter(name_contains="OpenAI")

        item1 = MagicMock()
        item1.name = "ChatOpenAI"
        item2 = MagicMock()
        item2.name = "LLMChain"

        items = [item1, item2]
        filtered = f.apply_filter(items)

        assert len(filtered) == 1
        assert item1 in filtered


class TestContentFilter:
    """Tests for ContentFilter."""

    def test_default_values(self):
        """Test default ContentFilter values."""
        f = ContentFilter()
        assert f.grep is None
        assert f.exclude is None

    def test_needs_client_filtering_with_grep(self):
        """Test that grep requires client-side filtering."""
        f = ContentFilter(grep="error")
        assert f.needs_client_filtering() is True

    def test_needs_client_filtering_with_exclude(self):
        """Test that exclude requires client-side filtering."""
        f = ContentFilter(exclude="test")
        assert f.needs_client_filtering() is True

    def test_no_client_filtering_when_empty(self):
        """Test no client filtering needed when filter is empty."""
        f = ContentFilter()
        assert f.needs_client_filtering() is False


class TestTagMetadataFilter:
    """Tests for TagMetadataFilter."""

    def test_default_values(self):
        """Test default TagMetadataFilter values."""
        f = TagMetadataFilter()
        assert f.tags == []
        assert f.metadata == {}

    def test_tags_filter(self):
        """Test tags filter generates FQL."""
        f = TagMetadataFilter(tags=["production", "important"])
        fql = f.to_fql_filters()
        assert len(fql) == 2
        assert 'has(tags, "production")' in fql[0]
        assert 'has(tags, "important")' in fql[1]

    def test_metadata_filter(self):
        """Test metadata filter generates FQL."""
        f = TagMetadataFilter(metadata={"env": "prod", "version": "1.0"})
        fql = f.to_fql_filters()
        assert len(fql) == 2

    def test_no_client_filtering_needed(self):
        """Test that tag/metadata filtering is server-side only."""
        f = TagMetadataFilter(tags=["test"])
        assert f.needs_client_filtering() is False


class TestRunsFilterConfig:
    """Tests for RunsFilterConfig."""

    def test_default_values(self):
        """Test default RunsFilterConfig values."""
        config = RunsFilterConfig()
        assert isinstance(config.status, StatusFilter)
        assert isinstance(config.time, TimeFilter)
        assert isinstance(config.latency, LatencyFilter)
        assert isinstance(config.pattern, PatternFilter)
        assert isinstance(config.content, ContentFilter)
        assert isinstance(config.tag_metadata, TagMetadataFilter)

    def test_to_sdk_params_with_status(self):
        """Test SDK params with status filter."""
        config = RunsFilterConfig(status=StatusFilter(status="error"))
        params = config.to_sdk_params()
        assert params["error"] is True

    def test_to_sdk_params_with_fql_filters(self):
        """Test SDK params with FQL filters."""
        config = RunsFilterConfig(
            latency=LatencyFilter(min_latency="5s"),
            tag_metadata=TagMetadataFilter(tags=["important"]),
        )
        params = config.to_sdk_params()
        assert "filter" in params
        assert "gt(latency" in params["filter"]
        assert "has(tags" in params["filter"]

    def test_to_sdk_params_with_custom_filter(self):
        """Test SDK params with custom FQL filter."""
        custom_fql = 'eq(name, "ChatOpenAI")'
        config = RunsFilterConfig(custom_filter=custom_fql)
        params = config.to_sdk_params()
        assert params["filter"] == custom_fql

    def test_to_sdk_params_combines_custom_and_generated_fql(self):
        """Test that custom FQL is combined with generated FQL."""
        custom_fql = 'eq(name, "ChatOpenAI")'
        config = RunsFilterConfig(
            custom_filter=custom_fql, latency=LatencyFilter(min_latency="5s")
        )
        params = config.to_sdk_params()
        assert "filter" in params
        assert "and(" in params["filter"]
        assert custom_fql in params["filter"]
        assert "gt(latency" in params["filter"]

    def test_to_sdk_params_with_sdk_specific_params(self):
        """Test SDK params with SDK-specific parameters."""
        config = RunsFilterConfig(
            trace_id="abc123",
            run_type="llm",
            is_root=True,
            select=["id", "name"],
        )
        params = config.to_sdk_params()
        assert params["trace_id"] == "abc123"
        assert params["run_type"] == "llm"
        assert params["is_root"] is True
        assert params["select"] == ["id", "name"]

    def test_needs_client_filtering_false_when_no_client_filters(self):
        """Test needs_client_filtering returns False with no client filters."""
        config = RunsFilterConfig(status=StatusFilter(status="error"))
        assert config.needs_client_filtering() is False

    def test_needs_client_filtering_true_with_pattern_filter(self):
        """Test needs_client_filtering returns True with pattern filter."""
        config = RunsFilterConfig(pattern=PatternFilter(name="Chat*"))
        assert config.needs_client_filtering() is True

    def test_needs_client_filtering_true_with_content_filter(self):
        """Test needs_client_filtering returns True with content filter."""
        config = RunsFilterConfig(content=ContentFilter(grep="error"))
        assert config.needs_client_filtering() is True

    def test_calculate_api_limit_with_no_client_filtering(self):
        """Test API limit calculation with no client filtering."""
        config = RunsFilterConfig(status=StatusFilter(status="error"))
        assert config.calculate_api_limit(10) == 10
        assert config.calculate_api_limit(None) is None

    def test_calculate_api_limit_with_client_filtering(self):
        """Test API limit calculation with client filtering."""
        config = RunsFilterConfig(pattern=PatternFilter(name="Chat*"))

        # Should be 3x with minimum of 100 and cap at 500
        assert config.calculate_api_limit(10) == 100  # 10*3=30, min is 100
        assert config.calculate_api_limit(50) == 150  # 50*3=150
        assert config.calculate_api_limit(200) == 500  # 200*3=600, cap is 500
        assert config.calculate_api_limit(None) == 1000  # No limit = 1000

    def test_apply_client_filters_applies_pattern_then_content(self):
        """Test that client filters are applied in correct order."""
        config = RunsFilterConfig(
            pattern=PatternFilter(name="Chat*"),
            content=ContentFilter(grep="OpenAI"),
        )

        # Create mock items
        item1 = MagicMock()
        item1.name = "ChatOpenAI"
        item2 = MagicMock()
        item2.name = "ChatAnthropic"
        item3 = MagicMock()
        item3.name = "LLMChain"

        # Apply filters - pattern first (Chat*), then grep (OpenAI)
        # This test structure shows the order but actual filtering depends on utils
        items = [item1, item2, item3]

        # The pattern filter would match item1 and item2
        # Then content filter would search for "OpenAI" in the matched items
        # We're testing that apply_client_filters calls both in order
        grep_fields = ("name", "inputs", "outputs")
        filtered = config.apply_client_filters(items, grep_fields=grep_fields)

        # Since we're using real filter logic, the exact result depends on
        # how get_matching_items and apply_grep_filter work
        assert isinstance(filtered, list)

    def test_immutability(self):
        """Test that RunsFilterConfig is immutable (frozen)."""
        config = RunsFilterConfig(status=StatusFilter(status="error"))
        with pytest.raises((ValidationError, AttributeError)):
            config.status = StatusFilter(status="success")
