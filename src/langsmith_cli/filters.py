"""Filter classes for consolidating CLI filtering logic.

This module provides type-safe Pydantic filter models that represent all filtering
options across the CLI. Each filter knows how to:
1. Convert itself to SDK parameters (server-side filtering)
2. Convert itself to FQL expressions (LangSmith query language)
3. Apply client-side filtering when needed
4. Determine if client-side filtering is required

The design separates concerns:
- Server-side: Passed to SDK methods like client.list_runs()
- Client-side: Applied after fetching data (for features SDK doesn't support)
"""

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class StatusFilter(BaseModel):
    """Filter by run status (success/error/running)."""

    model_config = {"frozen": True}

    status: Literal["error", "success"] | None = None
    failed: bool = False
    succeeded: bool = False

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: str | None) -> str | None:
        """Ensure status is a valid value."""
        if v is not None and v not in ("error", "success"):
            raise ValueError("status must be 'error' or 'success'")
        return v

    def to_sdk_params(self) -> dict[str, Any]:
        """Convert to parameters for client.list_runs()."""
        if self.status == "error" or self.failed:
            return {"error": True}
        elif self.status == "success" or self.succeeded:
            return {"error": False}
        return {}

    def needs_client_filtering(self) -> bool:
        """Status filtering is always server-side."""
        return False


class TimeFilter(BaseModel):
    """Filter by time ranges (since, last N hours, recent, today)."""

    model_config = {"frozen": True}

    since: str | None = None
    last: str | None = None
    recent: bool = False
    today: bool = False

    def to_fql_filters(self) -> list[str]:
        """Convert to FQL filter expressions."""
        from datetime import datetime, timedelta, timezone

        filters: list[str] = []

        if self.since:
            # Parse ISO timestamp or relative time
            filters.append(f'gt(start_time, "{self.since}")')
        elif self.last:
            # Parse "5h", "2d", etc.
            from langsmith_cli.utils import parse_relative_time

            cutoff = parse_relative_time(self.last)
            filters.append(f'gt(start_time, "{cutoff.isoformat()}")')
        elif self.recent:
            # Last hour
            cutoff = datetime.now(timezone.utc) - timedelta(hours=1)
            filters.append(f'gt(start_time, "{cutoff.isoformat()}")')
        elif self.today:
            # Since midnight UTC
            today_start = datetime.now(timezone.utc).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            filters.append(f'gt(start_time, "{today_start.isoformat()}")')

        return filters

    def to_sdk_params(self) -> dict[str, Any]:
        """Convert to parameters for client.list_runs()."""
        # Time filters are handled via FQL, so we return empty dict
        # but include FQL in the filter parameter
        fql_filters = self.to_fql_filters()
        if fql_filters:
            # Caller will combine this with other FQL filters
            return {"_fql_filters": fql_filters}
        return {}

    def needs_client_filtering(self) -> bool:
        """Time filtering is server-side via FQL."""
        return False


class LatencyFilter(BaseModel):
    """Filter by run latency (duration)."""

    model_config = {"frozen": True}

    min_latency: str | None = None
    max_latency: str | None = None

    def to_fql_filters(self) -> list[str]:
        """Convert to FQL filter expressions."""
        filters: list[str] = []

        if self.min_latency:
            filters.append(f'gt(latency, "{self.min_latency}")')
        if self.max_latency:
            filters.append(f'lt(latency, "{self.max_latency}")')

        return filters

    def to_sdk_params(self) -> dict[str, Any]:
        """Convert to parameters for client.list_runs()."""
        fql_filters = self.to_fql_filters()
        if fql_filters:
            return {"_fql_filters": fql_filters}
        return {}

    def needs_client_filtering(self) -> bool:
        """Latency filtering is server-side via FQL."""
        return False


class PatternFilter(BaseModel):
    """Filter by name patterns (glob-style matching)."""

    model_config = {"frozen": True}

    name: str | None = None
    name_contains: str | None = None

    def to_sdk_params(self) -> dict[str, Any]:
        """Convert to parameters for client.list_runs()."""
        # SDK doesn't support name pattern matching
        return {}

    def needs_client_filtering(self) -> bool:
        """Pattern filtering requires client-side processing."""
        return self.name is not None or self.name_contains is not None

    def apply_filter(self, items: list[Any]) -> list[Any]:
        """Apply pattern matching to items.

        Uses get_matching_items() from utils.py for precedence:
        1. Exact match (if pattern has no wildcards)
        2. Glob pattern match (if pattern has wildcards)
        3. Substring match (if name_contains)
        """
        from langsmith_cli.utils import get_matching_items

        if self.name:
            return get_matching_items(
                items, name_pattern=self.name, name_getter=lambda item: item.name
            )
        elif self.name_contains:
            return [item for item in items if self.name_contains in item.name]
        return items


class ContentFilter(BaseModel):
    """Filter by content search (grep across all fields)."""

    model_config = {"frozen": True}

    grep: str | None = None
    exclude: str | None = None

    def to_sdk_params(self) -> dict[str, Any]:
        """Convert to parameters for client.list_runs()."""
        # SDK doesn't support content search
        return {}

    def needs_client_filtering(self) -> bool:
        """Content filtering requires client-side processing."""
        return self.grep is not None or self.exclude is not None

    def apply_filter(
        self, items: list[Any], grep_fields: tuple[str, ...] = ()
    ) -> list[Any]:
        """Apply content filtering to items.

        Args:
            items: Items to filter
            grep_fields: Tuple of field names to search (e.g., ("name", "inputs", "outputs"))
        """
        from langsmith_cli.utils import apply_grep_filter, apply_exclude_filter

        if self.grep:
            items = apply_grep_filter(items, self.grep, grep_fields=grep_fields)
        if self.exclude:
            # Convert single exclude string to tuple
            exclude_patterns = tuple(self.exclude.split(","))
            items = apply_exclude_filter(
                items, exclude_patterns, name_getter=lambda item: item.name
            )
        return items


class TagMetadataFilter(BaseModel):
    """Filter by tags and metadata."""

    model_config = {"frozen": True}

    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, str] = Field(default_factory=dict)

    def to_fql_filters(self) -> list[str]:
        """Convert to FQL filter expressions."""
        filters: list[str] = []

        for tag in self.tags:
            filters.append(f'has(tags, "{tag}")')

        for key, value in self.metadata.items():
            filters.append(
                f'and(eq(metadata_key, "{key}"), eq(metadata_value, "{value}"))'
            )

        return filters

    def to_sdk_params(self) -> dict[str, Any]:
        """Convert to parameters for client.list_runs()."""
        fql_filters = self.to_fql_filters()
        if fql_filters:
            return {"_fql_filters": fql_filters}
        return {}

    def needs_client_filtering(self) -> bool:
        """Tag/metadata filtering is server-side via FQL."""
        return False


class RunsFilterConfig(BaseModel):
    """Consolidated filter configuration for runs commands.

    This class combines all filter types and knows how to:
    1. Convert to SDK parameters for client.list_runs()
    2. Determine if client-side filtering is needed
    3. Apply all client-side filters in the correct order
    4. Calculate optimal API fetch limit
    """

    model_config = {"frozen": True}

    status: StatusFilter = Field(default_factory=StatusFilter)
    time: TimeFilter = Field(default_factory=TimeFilter)
    latency: LatencyFilter = Field(default_factory=LatencyFilter)
    pattern: PatternFilter = Field(default_factory=PatternFilter)
    content: ContentFilter = Field(default_factory=ContentFilter)
    tag_metadata: TagMetadataFilter = Field(default_factory=TagMetadataFilter)

    # SDK-specific parameters (pass-through to client.list_runs)
    custom_filter: str | None = None
    trace_filter: str | None = None
    tree_filter: str | None = None
    trace_id: str | None = None
    run_type: str | None = None
    is_root: bool | None = None
    parent_run_id: str | None = None
    reference_example_id: str | None = None
    start_time: str | None = None
    end_time: str | None = None
    select: list[str] | None = None

    def to_sdk_params(self) -> dict[str, Any]:
        """Build parameters to pass to client.list_runs().

        Combines all server-side filters into SDK parameter dict.
        """
        params: dict[str, Any] = {}

        # Add status filter
        params.update(self.status.to_sdk_params())

        # Collect all FQL filters
        fql_filters: list[str] = []

        # Add time FQL filters
        time_params = self.time.to_sdk_params()
        if "_fql_filters" in time_params:
            fql_filters.extend(time_params["_fql_filters"])

        # Add latency FQL filters
        latency_params = self.latency.to_sdk_params()
        if "_fql_filters" in latency_params:
            fql_filters.extend(latency_params["_fql_filters"])

        # Add tag/metadata FQL filters
        tag_params = self.tag_metadata.to_sdk_params()
        if "_fql_filters" in tag_params:
            fql_filters.extend(tag_params["_fql_filters"])

        # Combine all FQL filters with custom filter
        if fql_filters:
            if self.custom_filter:
                # Combine custom filter with generated FQL
                combined = f"and({self.custom_filter}, {', '.join(fql_filters)})"
                params["filter"] = combined
            elif len(fql_filters) == 1:
                params["filter"] = fql_filters[0]
            else:
                # Combine multiple FQL filters with AND
                params["filter"] = f"and({', '.join(fql_filters)})"
        elif self.custom_filter:
            params["filter"] = self.custom_filter

        # Add other SDK parameters
        if self.trace_filter:
            params["trace_filter"] = self.trace_filter
        if self.tree_filter:
            params["tree_filter"] = self.tree_filter
        if self.trace_id:
            params["trace_id"] = self.trace_id
        if self.run_type:
            params["run_type"] = self.run_type
        if self.is_root is not None:
            params["is_root"] = self.is_root
        if self.parent_run_id:
            params["parent_run_id"] = self.parent_run_id
        if self.reference_example_id:
            params["reference_example_id"] = self.reference_example_id
        if self.start_time:
            params["start_time"] = self.start_time
        if self.end_time:
            params["end_time"] = self.end_time
        if self.select:
            params["select"] = self.select

        return params

    def needs_client_filtering(self) -> bool:
        """Check if any client-side filtering is required."""
        return (
            self.pattern.needs_client_filtering()
            or self.content.needs_client_filtering()
        )

    def apply_client_filters(
        self, items: list[Any], grep_fields: tuple[str, ...] = ()
    ) -> list[Any]:
        """Apply all client-side filters in the correct order.

        Order matters:
        1. Pattern matching (name/name_contains)
        2. Content search (grep)
        3. Exclude filter

        Args:
            items: Items to filter
            grep_fields: Tuple of field names to search for content filtering
        """
        # Apply pattern filter
        items = self.pattern.apply_filter(items)

        # Apply content filter
        items = self.content.apply_filter(items, grep_fields=grep_fields)

        return items

    def calculate_api_limit(self, user_limit: int | None) -> int | None:
        """Calculate how many items to fetch from API.

        When client-side filtering is needed, we fetch more items than requested
        to account for items that will be filtered out.

        Strategy:
        - If no client-side filtering: Use user limit directly
        - If client-side filtering: Fetch 3x the limit, capped at 500
        - If no limit: Fetch 1000 (reasonable default)
        """
        if not self.needs_client_filtering():
            return user_limit

        if user_limit:
            # Fetch 3x more, capped at 500
            return min(max(user_limit * 3, 100), 500)
        else:
            # No limit specified, fetch reasonable default
            return 1000
