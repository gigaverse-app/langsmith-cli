"""Analyze command and grouping/metrics helpers for runs."""

from typing import Any

import click
from rich.table import Table
from langsmith.schemas import Run

from langsmith_cli.commands.runs._group import runs, console, _make_fetch_runs
from langsmith_cli.utils import (
    add_project_filter_options,
    add_time_filter_options,
    build_time_fql_filters,
    combine_fql_filters,
    determine_output_format,
    fetch_from_projects,
    get_or_create_client,
    output_formatted_data,
    resolve_project_filters,
)


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
        if run.metadata and isinstance(run.metadata, dict):
            value = run.metadata.get(field_name)
            if value is not None:
                return value

        # Fallback to checking run.extra["metadata"]
        if run.extra and isinstance(run.extra, dict):
            metadata = run.extra.get("metadata")
            if metadata and isinstance(metadata, dict):
                return metadata.get(field_name)

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
        costs = [
            r.total_cost
            for r in runs
            if hasattr(r, "total_cost") and r.total_cost is not None
        ]
        result["avg_cost"] = float(statistics.mean(costs)) if costs else 0.0

    return result


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

    # Determine if output is machine-readable
    is_machine_readable = ctx.obj.get("json") or output_format in ["csv", "yaml"]
    logger.use_stderr = is_machine_readable

    from collections import defaultdict

    logger.debug(
        f"Analyzing runs: group_by={group_by}, metrics={metrics}, sample_size={sample_size}"
    )

    # Build time filters and combine with additional_filter
    time_filters = build_time_fql_filters(since=since, last=last, before=before)
    base_filters = time_filters.copy()
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

    # Determine which fields to fetch based on requested metrics and grouping
    # Use field selection to reduce data transfer and speed up fetch
    select_fields = set()

    # Add fields for grouping
    if grouping_type == "tag":
        select_fields.add("tags")
    else:  # metadata
        select_fields.add("extra")

    # Always add start_time for sorting and latency computation
    select_fields.add("start_time")

    # Add fields based on requested metrics
    for metric in requested_metrics:
        if metric in ["error_rate"]:
            select_fields.add("error")
        elif metric in ["p50_latency", "p95_latency", "p99_latency", "avg_latency"]:
            # latency is computed from start_time and end_time
            select_fields.add("end_time")  # start_time already added above
        elif metric == "total_tokens":
            select_fields.add("total_tokens")
        elif metric == "avg_cost":
            select_fields.add("total_cost")

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
    all_runs = []

    if sample_size == 0:
        # User wants ALL runs - don't use select (would be slow for large datasets)
        # Use serial pagination without field selection
        result = fetch_from_projects(
            client,
            pq.names,
            _make_fetch_runs(),
            project_query=pq,
            filter=combined_filter,
            limit=None,
            console=console,
        )
        all_runs = result.items
    else:
        # Use sample-based approach with field selection (FAST!)
        # API has max limit of 100 when using select, so manually collect from iterator
        failed_projects = []
        sources: list[tuple[str, dict[str, Any]]] = []
        if pq.use_id:
            sources = [(f"id:{pq.project_id}", {"project_id": pq.project_id})]
        else:
            sources = [(name, {"project_name": name}) for name in pq.names]
        for source_label, proj_kwargs in sources:
            try:
                runs_iter = client.list_runs(
                    **proj_kwargs,
                    filter=combined_filter,
                    limit=None,  # SDK paginates automatically
                    select=list(select_fields) if select_fields else None,
                )

                # Manually collect up to sample_size
                collected = 0
                for run in runs_iter:
                    all_runs.append(run)
                    collected += 1
                    if collected >= sample_size:
                        break  # Stop early when we have enough
            except Exception as e:
                failed_projects.append((source_label, str(e)))

        # Report failures if any (but don't spam console in analyze mode)
        if failed_projects and len(all_runs) == 0:
            # Only report if we got zero runs (might be all failures)
            logger.warning("Some projects failed to fetch:")
            for proj, error_msg in failed_projects[:3]:
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
    format_type = determine_output_format(output_format, ctx.obj.get("json"))

    # Handle non-table formats
    if format_type != "table":
        output_formatted_data(results, format_type)
        return

    # Build table for human-readable output
    table = Table(title=f"Analysis: {group_by}")
    table.add_column("Group", style="cyan")

    # Add metric columns
    for metric in requested_metrics:
        table.add_column(metric.replace("_", " ").title(), justify="right")

    # Add rows
    for result in results:
        row_values = [result["group"]]
        for metric in requested_metrics:
            value = result.get(metric, 0)
            # Format numbers nicely
            if isinstance(value, float):
                if metric == "error_rate":
                    row_values.append(f"{value:.2%}")
                else:
                    row_values.append(f"{value:.2f}")
            else:
                row_values.append(str(value))
        table.add_row(*row_values)

    if not results:
        logger.warning("No groups found.")
    else:
        console.print(table)
