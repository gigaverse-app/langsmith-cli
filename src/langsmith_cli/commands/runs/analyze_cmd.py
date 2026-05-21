"""Analyze command and grouping/metrics helpers for runs."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import click

from langsmith_cli.commands.runs._group import runs, console, _make_fetch_runs
from langsmith_cli.run_helpers import run_extra_metadata, run_metadata_mapping
from langsmith_cli.utils import (
    add_project_filter_options,
    add_time_filter_options,
    build_tag_fql_filters,
    build_time_fql_filters,
    combine_fql_filters,
    collect_runs_streaming,
    configure_logger_streams,
    determine_output_format,
    fetch_from_projects,
    get_or_create_client,
    is_json_context,
    output_formatted_data,
    resolve_project_filters,
)

if TYPE_CHECKING:
    from langsmith.schemas import Run


def _parse_single_grouping(grouping_str: str) -> tuple[str, str]:
    """Helper to parse a single 'type:field' string.

    Args:
        grouping_str: String in format "tag:field_name" or "metadata:field_name"

    Returns:
        Tuple of (grouping_type, field_name)

    Raises:
        click.BadParameter: If format is invalid
    """
    if ":" not in grouping_str:
        raise click.BadParameter(
            f"Invalid grouping format: {grouping_str}. "
            "Use 'tag:field_name' or 'metadata:field_name'"
        )

    parts = grouping_str.split(":", 1)
    grouping_type = parts[0].strip()
    field_name = parts[1].strip()

    if grouping_type not in ["tag", "metadata"]:
        raise click.BadParameter(
            f"Invalid grouping type: {grouping_type}. Must be 'tag' or 'metadata'"
        )

    if not field_name:
        raise click.BadParameter("Field name cannot be empty")

    return grouping_type, field_name


def parse_grouping_field(grouping_str: str) -> tuple[str, str] | list[tuple[str, str]]:
    """Parse single or multiple grouping fields.

    Args:
        grouping_str: Either 'tag:field' or 'tag:f1,metadata:f2' (comma-separated)

    Returns:
        Single tuple for single dimension, or list of tuples for multi-dimensional

    Raises:
        click.BadParameter: If format is invalid

    \b
    Examples:
        >>> parse_grouping_field("tag:length_category")
        ("tag", "length_category")
        >>> parse_grouping_field("metadata:user_tier")
        ("metadata", "user_tier")
        >>> parse_grouping_field("tag:length,tag:content_type")
        [("tag", "length"), ("tag", "content_type")]
    """
    # Check for multi-dimensional (comma-separated dimensions)
    if "," in grouping_str:
        # Multi-dimensional: parse each dimension
        dimensions = [d.strip() for d in grouping_str.split(",")]
        return [_parse_single_grouping(d) for d in dimensions]
    else:
        # Single dimension: backward compatible
        return _parse_single_grouping(grouping_str)


def build_grouping_fql_filter(grouping_type: str, field_name: str, value: str) -> str:
    """Build FQL filter for a specific group value.

    Args:
        grouping_type: Either "tag" or "metadata"
        field_name: Name of the field
        value: Value to filter for

    Returns:
        FQL filter string

    \b
    Examples:
        >>> build_grouping_fql_filter("tag", "length_category", "short")
        'has(tags, "length_category:short")'

        >>> build_grouping_fql_filter("metadata", "user_tier", "premium")
        'and(in(metadata_key, ["user_tier"]), eq(metadata_value, "premium"))'
    """
    if grouping_type == "tag":
        # Tags are stored as "field_name:value" strings
        return f'has(tags, "{field_name}:{value}")'
    else:  # metadata
        # Metadata requires matching both key and value
        return f'and(in(metadata_key, ["{field_name}"]), eq(metadata_value, "{value}"))'


def build_multi_dimensional_fql_filter(
    dimensions: list[tuple[str, str]], combination_values: list[str]
) -> str:
    """Build FQL filter for multi-dimensional combination.

    Args:
        dimensions: List of (grouping_type, field_name) tuples
        combination_values: List of values, one per dimension

    Returns:
        Combined FQL filter using 'and()' to match all dimensions

    Raises:
        ValueError: If dimensions and values lists have different lengths

    \b
    Examples:
        >>> build_multi_dimensional_fql_filter(
        ...     [("tag", "length"), ("tag", "content_type")],
        ...     ["short", "news"]
        ... )
        'and(has(tags, "length:short"), has(tags, "content_type:news"))'

        >>> build_multi_dimensional_fql_filter(
        ...     [("tag", "length")],
        ...     ["medium"]
        ... )
        'has(tags, "length:medium")'
    """
    if len(dimensions) != len(combination_values):
        raise ValueError(
            f"Dimensions and values must have same length: "
            f"{len(dimensions)} dimensions vs {len(combination_values)} values"
        )

    filters = []
    for (grouping_type, field_name), value in zip(dimensions, combination_values):
        fql = build_grouping_fql_filter(grouping_type, field_name, value)
        filters.append(fql)

    # combine_fql_filters returns None for empty list, but we always have at least one
    return combine_fql_filters(filters) or filters[0]


def extract_group_value(run: Run, grouping_type: str, field_name: str) -> str | None:
    """Extract the group value from a run based on grouping configuration.

    Args:
        run: LangSmith Run instance
        grouping_type: Either "tag" or "metadata"
        field_name: Name of the field to extract

    Returns:
        Group value string, or None if not found

    \b
    Examples:
        Given run.tags = ["env:prod", "length_category:short", "user:123"]
        >>> extract_group_value(run, "tag", "length_category")
        "short"

        Given run.metadata = {"user_tier": "premium", "region": "us-east"}
        >>> extract_group_value(run, "metadata", "user_tier")
        "premium"
    """
    if grouping_type == "tag":
        # Search for tag matching "field_name:*"
        prefix = f"{field_name}:"
        if run.tags:
            for tag in run.tags:
                if tag.startswith(prefix):
                    return tag[len(prefix) :]
        return None
    else:  # metadata
        # Look up field_name in metadata dict
        # Check both run.metadata and run.extra["metadata"]
        metadata = run_metadata_mapping(run)
        if field_name in metadata:
            value = metadata[field_name]
            if value is not None:
                return str(value)
        # Fallback to checking run.extra["metadata"]
        extra_metadata = run_extra_metadata(run)
        if field_name in extra_metadata:
            value = extra_metadata[field_name]
            if value is not None:
                return str(value)

        return None


def compute_metrics(
    runs: list[Run], requested_metrics: list[str]
) -> dict[str, float | int]:
    """Compute aggregate metrics over a list of runs.

    Args:
        runs: List of Run instances
        requested_metrics: List of metric names to compute

    Returns:
        Dictionary mapping metric names to computed values

    Supported Metrics:
        - count: Number of runs
        - error_rate: Fraction of runs with error (0.0-1.0)
        - p50_latency, p95_latency, p99_latency: Latency percentiles (seconds)
        - avg_latency: Average latency (seconds)
        - total_tokens: Sum of total_tokens
        - avg_cost: Average cost (if available)
    """
    import statistics

    result: dict[str, float | int] = {}

    if not runs:
        # Return 0 for all metrics if no runs
        for metric in requested_metrics:
            result[metric] = 0
        return result

    # Count
    if "count" in requested_metrics:
        result["count"] = len(runs)

    # Error rate
    if "error_rate" in requested_metrics:
        error_count = sum(1 for r in runs if r.error is not None)
        result["error_rate"] = error_count / len(runs)

    # Latency metrics (filter out None values)
    latencies = [r.latency for r in runs if r.latency is not None]

    if latencies:
        if "avg_latency" in requested_metrics:
            result["avg_latency"] = statistics.mean(latencies)

        if "p50_latency" in requested_metrics:
            result["p50_latency"] = statistics.median(latencies)

        if "p95_latency" in requested_metrics:
            result["p95_latency"] = statistics.quantiles(latencies, n=20)[18]

        if "p99_latency" in requested_metrics:
            result["p99_latency"] = statistics.quantiles(latencies, n=100)[98]
    else:
        # No latency data available
        for metric in ["avg_latency", "p50_latency", "p95_latency", "p99_latency"]:
            if metric in requested_metrics:
                result[metric] = 0.0

    # Token metrics
    if "total_tokens" in requested_metrics:
        result["total_tokens"] = sum(r.total_tokens or 0 for r in runs)

    # Cost metrics (if available in SDK)
    if "avg_cost" in requested_metrics:
        costs = [r.total_cost for r in runs if r.total_cost is not None]
        result["avg_cost"] = float(statistics.mean(costs)) if costs else 0.0

    return result


def _select_fields_for_analyze(
    grouping_type: str, requested_metrics: list[str]
) -> set[str]:
    """Decide which Run fields the SDK should return for analyze.

    Field pruning is the main reason ``analyze`` is tolerable on large
    projects — fetching only what each metric needs cuts ~14x of bandwidth
    per run. Computing this set is mechanical but easy to get wrong (e.g.
    forgetting that latency metrics need both ``start_time`` and
    ``end_time``), so we isolate it.
    """
    select_fields = {"start_time"}
    if grouping_type == "tag":
        select_fields.add("tags")
    else:
        select_fields.add("extra")

    for metric in requested_metrics:
        if metric == "error_rate":
            select_fields.add("error")
        elif metric in ("p50_latency", "p95_latency", "p99_latency", "avg_latency"):
            select_fields.add("end_time")
        elif metric == "total_tokens":
            select_fields.add("total_tokens")
        elif metric == "avg_cost":
            select_fields.add("total_cost")
    return select_fields


def _render_analyze_table(
    results: list[dict[str, Any]],
    requested_metrics: list[str],
    group_by_label: str,
) -> Any:
    """Build a Rich Table from grouped metric rows. View layer only."""
    from rich.table import Table

    table = Table(title=f"Analysis: {group_by_label}")
    table.add_column("Group", style="cyan")
    for metric in requested_metrics:
        table.add_column(metric.replace("_", " ").title(), justify="right")

    for result in results:
        row_values: list[str] = [str(result["group"])]
        for metric in requested_metrics:
            value = result[metric] if metric in result else 0
            if isinstance(value, float):
                if metric == "error_rate":
                    row_values.append(f"{value:.2%}")
                else:
                    row_values.append(f"{value:.2f}")
            else:
                row_values.append(str(value))
        table.add_row(*row_values)
    return table


@runs.command("analyze")
@add_project_filter_options
@add_time_filter_options
@click.option(
    "--group-by",
    required=True,
    help="Grouping field (e.g., 'tag:length_category', 'metadata:user_tier')",
)
@click.option(
    "--metrics",
    default="count,error_rate,p50_latency,p95_latency",
    help="Comma-separated list of metrics to compute",
)
@click.option(
    "--tag",
    multiple=True,
    help="Filter by tag server-side (can specify multiple times for AND logic)",
)
@click.option(
    "--filter",
    "additional_filter",
    help="Additional FQL filter to apply before grouping",
)
@click.option(
    "--sample-size",
    default=300,
    type=int,
    help="Number of recent runs to analyze (default: 300, use 0 for all runs)",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["table", "json", "csv", "yaml"]),
    help="Output format (default: table, or json if --json flag used)",
)
@click.pass_context
def analyze_runs(
    ctx,
    project,
    project_id,
    project_name,
    project_name_exact,
    project_name_pattern,
    project_name_regex,
    since,
    before,
    last,
    group_by,
    metrics,
    tag,
    additional_filter,
    sample_size,
    output_format,
):
    """Analyze runs grouped by tags or metadata with aggregate metrics.

    This command groups runs by a specified field (tag or metadata) and computes
    aggregate statistics for each group.

    By default, analyzes the 300 most recent runs using field selection for
    fast performance. Use --sample-size 0 to analyze all runs (slower but complete).

    Supported Metrics:
        - count: Number of runs in group
        - error_rate: Fraction of runs with errors (0.0-1.0)
        - p50_latency, p95_latency, p99_latency: Latency percentiles (seconds)
        - avg_latency: Average latency (seconds)
        - total_tokens: Sum of total tokens
        - avg_cost: Average cost per run

    \b
    Examples:
        # Analyze recent 300 runs (default - fast, ~8 seconds)
        langsmith-cli runs analyze \\
          --project my-project \\
          --group-by "tag:schema" \\
          --metrics "count,error_rate,p50_latency"
        # Quick check with smaller sample (~2 seconds)
        langsmith-cli runs analyze \\
          --project my-project \\
          --group-by "tag:schema" \\
          --metrics "count,error_rate" \\
          --sample-size 100
        # Larger sample for better accuracy (~28 seconds)
        langsmith-cli runs analyze \\
          --project my-project \\
          --group-by "tag:schema" \\
          --metrics "count,error_rate,p50_latency" \\
          --sample-size 1000
        # Analyze ALL runs (slower, but complete)
        langsmith-cli runs analyze \\
          --project my-project \\
          --group-by "tag:schema" \\
          --metrics "count,error_rate,p50_latency" \\
          --sample-size 0
    """
    logger = ctx.obj["logger"]
    configure_logger_streams(ctx, logger, output_format=output_format)

    from collections import defaultdict

    logger.debug(
        f"Analyzing runs: group_by={group_by}, metrics={metrics}, sample_size={sample_size}"
    )

    # Build time filters and combine with additional_filter
    time_filters = build_time_fql_filters(since=since, last=last, before=before)
    base_filters = time_filters.copy()
    # Server-side tag filtering (AND logic — all tags must be present)
    if tag:
        base_filters.extend(build_tag_fql_filters(tag))
    if additional_filter:
        base_filters.append(additional_filter)

    # Combine base filters into a single filter
    combined_filter = combine_fql_filters(base_filters)

    client = get_or_create_client(ctx)

    # Parse group-by field
    parsed = parse_grouping_field(group_by)

    # analyze command currently only supports single-dimensional grouping
    if isinstance(parsed, list):
        raise click.BadParameter(
            "Multi-dimensional grouping is not yet supported in 'runs analyze'. "
            "Use a single dimension like 'tag:field' or 'metadata:field'"
        )

    grouping_type, field_name = parsed

    # Parse metrics
    requested_metrics = [m.strip() for m in metrics.split(",")]

    # Get matching projects
    pq = resolve_project_filters(
        client,
        project=project,
        project_id=project_id,
        name=project_name,
        name_exact=project_name_exact,
        name_pattern=project_name_pattern,
        name_regex=project_name_regex,
    )

    select_fields = _select_fields_for_analyze(grouping_type, requested_metrics)

    # -------------------------------------------------------------------------
    # Fetch Optimization History & Future Improvements
    # -------------------------------------------------------------------------
    # CURRENT: Simple sample-based approach with field selection
    #   - Default: 300 most recent runs with smart field selection
    #   - Performance: 100 runs in ~2s, 300 runs in ~8s, 1000 runs in ~28s (vs 45s timeout)
    #   - Data reduction: 14x smaller per run (36KB → 2.6KB with select)
    #
    # ATTEMPTED: Parallel time-based pagination with ThreadPoolExecutor
    #   - Divided time into N windows and fetched in parallel
    #   - Result: Only 4s improvement (28s → 24s) for 1000 runs
    #   - Reverted: 50+ lines of complexity not worth 14% speedup
    #
    # ATTEMPTED: Adaptive recursive subdivision for dense time periods
    #   - If window returned 100 runs (max), subdivide to get better coverage
    #   - Addressed sampling bias (e.g., 100 from 20,000 runs = 0.5% sample)
    #   - Reverted: Too complex for marginal benefit
    #
    # FUTURE IMPROVEMENT: Adaptive time-based windowing could work if:
    #   1. Use FQL time filters to discover high-density periods
    #      Example: Query run counts per hour to find busy periods
    #   2. Allocate sample budget proportionally across time windows
    #      Example: 60% of runs in last 6 hours → fetch 180 of 300 from there
    #   3. This ensures representative sampling across time while maintaining speed
    #   4. Trade-off: One extra API call to count runs, but better statistical accuracy
    #
    # For now, simple approach solves the timeout problem with minimal complexity.
    # -------------------------------------------------------------------------

    # Fetch runs (with optional filter and sample size limit)
    # Use field selection for 10-20x faster fetches
    if sample_size == 0:
        # User wants ALL runs. Keep serial pagination, but still push selected
        # fields to the SDK because this path benefits most from sparse runs.
        result = fetch_from_projects(
            client,
            pq.names,
            _make_fetch_runs(),
            project_query=pq,
            filter=combined_filter,
            limit=None,
            select=list(select_fields) if select_fields else None,
            console=console,
        )
        all_runs = result.items
    else:
        # Sample-based path: stream up to `sample_size` runs total, capped on
        # the first project that fills the budget.
        stream_result = collect_runs_streaming(
            client,
            pq,
            filter=combined_filter,
            select=list(select_fields) if select_fields else None,
            sample_size=sample_size,
        )
        all_runs = stream_result.items
        if stream_result.all_failed:
            # Only complain when we got zero runs — analyze is sampling-oriented
            # and a partial-success path shouldn't spam the console.
            logger.warning("Some projects failed to fetch:")
            for proj, error_msg in stream_result.failed_sources[:3]:
                short_error = (
                    error_msg[:100] + "..." if len(error_msg) > 100 else error_msg
                )
                logger.warning(f"  • {proj}: {short_error}")

    # Group runs by extracted field value
    groups: dict[str, list[Any]] = defaultdict(list)
    for run in all_runs:
        group_value = extract_group_value(run, grouping_type, field_name)
        if group_value:
            groups[group_value].append(run)

    # Compute metrics for each group
    results = []
    for group_value, group_runs in groups.items():
        metrics_dict = compute_metrics(group_runs, requested_metrics)
        result = {
            "group": f"{field_name}:{group_value}",
            **metrics_dict,
        }
        results.append(result)

    # Sort by group name for consistency
    results.sort(key=lambda r: r["group"])

    # Determine output format
    format_type = determine_output_format(output_format, is_json_context(ctx))

    # Handle non-table formats
    if format_type != "table":
        output_formatted_data(results, format_type)
        return

    if not results:
        logger.warning("No groups found.")
        return

    console.print(_render_analyze_table(results, requested_metrics, group_by))
