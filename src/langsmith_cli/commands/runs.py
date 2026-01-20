from dataclasses import dataclass
from typing import Any
import json

import click
from rich.console import Console
from rich.table import Table
from langsmith.schemas import Run

from langsmith_cli.utils import (
    add_project_filter_options,
    add_time_filter_options,
    apply_client_side_limit,
    apply_exclude_filter,
    apply_grep_filter,
    build_runs_list_filter,
    build_runs_table,
    build_time_fql_filters,
    combine_fql_filters,
    count_option,
    determine_output_format,
    exclude_option,
    fetch_from_projects,
    fields_option,
    filter_fields,
    get_matching_items,
    get_matching_projects,
    get_or_create_client,
    json_dumps,
    output_formatted_data,
    output_option,
    parse_duration_to_seconds,
    render_run_details,
    sort_items,
    write_output_to_file,
)

console = Console()


@click.group()
def runs():
    """Inspect and filter application traces."""
    pass


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


@runs.command("list")
@add_project_filter_options
@click.option("--limit", default=20, help="Max runs to fetch (per project).")
@click.option(
    "--status", type=click.Choice(["success", "error"]), help="Filter by status."
)
@click.option(
    "--filter",
    "filter_",
    help='LangSmith FQL filter. Examples: eq(name, "extractor"), gt(latency, "5s"), has(tags, "prod"). See --help for full examples.',
)
@click.option("--trace-id", help="Get all runs in a specific trace.")
@click.option(
    "--run-type", help="Filter by run type (llm, chain, tool, retriever, etc)."
)
@click.option("--is-root", type=bool, help="Filter root traces only (true/false).")
@click.option(
    "--roots",
    is_flag=True,
    help="Show only root traces (shorthand for --is-root true). Recommended for cleaner output.",
)
@click.option("--trace-filter", help="Filter applied to root trace.")
@click.option("--tree-filter", help="Filter if any run in trace tree matches.")
@click.option(
    "--order-by", default="-start_time", help="Sort field (prefix with - for desc)."
)
@click.option("--reference-example-id", help="Filter runs for a specific example.")
@click.option(
    "--tag",
    multiple=True,
    help="Filter by tag (can specify multiple times for AND logic).",
)
@click.option(
    "--name-pattern",
    help="Filter run names with wildcards (e.g. '*auth*'). "
    "Uses client-side filtering - searches recent runs only. "
    "Increase --limit to search more runs.",
)
@click.option(
    "--name-regex",
    help="Filter run names with regex (e.g. '^test-.*-v[0-9]+$'). "
    "Uses client-side filtering - searches recent runs only. "
    "Increase --limit to search more runs.",
)
@click.option("--model", help="Filter by model name (e.g. 'gpt-4', 'claude-3').")
@click.option(
    "--failed",
    is_flag=True,
    help="Show only failed/error runs (equivalent to --status error).",
)
@click.option(
    "--succeeded",
    is_flag=True,
    help="Show only successful runs (equivalent to --status success).",
)
@click.option("--slow", is_flag=True, help="Filter to slow runs (latency > 5s).")
@click.option("--recent", is_flag=True, help="Filter to recent runs (last hour).")
@click.option("--today", is_flag=True, help="Filter to today's runs.")
@click.option("--min-latency", help="Minimum latency (e.g., '2s', '500ms', '1.5s').")
@click.option("--max-latency", help="Maximum latency (e.g., '10s', '2000ms').")
@click.option(
    "--since",
    help="Show runs since time (ISO format, '3d', or '3 days ago').",
)
@click.option(
    "--last",
    help="Show runs from last duration (e.g., '24h', '7d', '30m', '2w').",
)
@click.option(
    "--query",
    help="Server-side full-text search in inputs/outputs (fast, but searches only first ~250 chars). Use --grep for unlimited content search.",
)
@click.option(
    "--grep",
    help="Client-side pattern search in run content (inputs, outputs, error). Searches ALL content, parses nested JSON. Slower but more powerful than --query.",
)
@click.option(
    "--grep-ignore-case",
    is_flag=True,
    help="Make --grep search case-insensitive.",
)
@click.option(
    "--grep-regex",
    is_flag=True,
    help="Treat --grep pattern as regex (e.g., --grep '[\u0590-\u05ff]' for Hebrew characters).",
)
@click.option(
    "--grep-in",
    help="Comma-separated fields to search in (e.g., 'inputs,outputs,error'). Searches all fields if not specified.",
)
@click.option(
    "--fetch",
    type=int,
    help="Number of runs to fetch when using client-side filters (--grep, --name-pattern, etc.). Overrides automatic 3x multiplier. Example: --limit 10 --fetch 500 fetches 500 runs and returns up to 10 matches.",
)
@click.option(
    "--sort-by",
    help="Sort by field (name, status, latency, start_time). Prefix with - for descending.",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["table", "json", "csv", "yaml"]),
    help="Output format (default: table, or json if --json flag used).",
)
@click.option(
    "--no-truncate",
    is_flag=True,
    help="Don't truncate long fields in table output (shows full content in all columns).",
)
@exclude_option()
@fields_option()
@count_option()
@output_option()
@click.pass_context
def list_runs(
    ctx,
    project,
    project_name,
    project_name_exact,
    project_name_pattern,
    project_name_regex,
    limit,
    status,
    filter_,
    trace_id,
    run_type,
    is_root,
    roots,
    trace_filter,
    tree_filter,
    order_by,
    reference_example_id,
    tag,
    name_pattern,
    name_regex,
    model,
    failed,
    succeeded,
    slow,
    recent,
    today,
    min_latency,
    max_latency,
    since,
    last,
    query,
    grep,
    grep_ignore_case,
    grep_regex,
    grep_in,
    fetch,
    sort_by,
    output_format,
    no_truncate,
    exclude,
    fields,
    count,
    output,
):
    """Fetch recent runs from one or more projects.

    Use project filters (--project-name, --project-name-pattern, --project-name-regex, --project-name-exact) to match multiple projects.
    Use run name filters (--name-pattern, --name-regex) to filter specific run names.

    \b
    FQL Filter Examples:
      # Filter by name
      --filter 'eq(name, "extractor")'

      # Filter by latency
      --filter 'gt(latency, "5s")'

      # Filter by tags
      --filter 'has(tags, "production")'

      # Combine multiple conditions
      --filter 'and(eq(run_type, "chain"), gt(latency, "10s"))'

      # Complex example: chains that took >10s and had >5000 tokens
      --filter 'and(eq(run_type, "chain"), gt(latency, "10s"), gt(total_tokens, 5000))'

    \b
    Search Examples:
      # Server-side text search (fast, first ~250 chars)
      --query "error message"

      # Client-side grep (slower, unlimited, regex)
      --grep "druze" --grep-in inputs,outputs

      # Regex search for Hebrew characters
      --grep "[\\u0590-\\u05FF]" --grep-regex --grep-in inputs
    """
    logger = ctx.obj["logger"]

    # Determine if output is machine-readable (use stderr for diagnostics)
    is_machine_readable = (
        ctx.obj.get("json") or output_format in ["csv", "yaml"] or count or output
    )
    logger.use_stderr = is_machine_readable

    # When --count is used, default to unlimited (0) unless user explicitly set limit
    # Check if limit was explicitly provided by checking if it's not the default
    if count and limit == 20:
        # User didn't explicitly set limit, so use 0 (unlimited) for counting
        limit = 0

    import datetime

    logger.debug(
        f"Listing runs with filters: project={project}, status={status}, limit={limit}"
    )

    client = get_or_create_client(ctx)

    # Get matching projects using universal helper
    projects_to_query = get_matching_projects(
        client,
        project=project,
        name=project_name,
        name_exact=project_name_exact,
        name_pattern=project_name_pattern,
        name_regex=project_name_regex,
    )

    # Handle --roots flag (convenience for --is-root true)
    if roots:
        is_root = True

    # Handle status filtering with multiple options
    error_filter = None
    if status == "error" or failed:
        error_filter = True
    elif status == "success" or succeeded:
        error_filter = False

    # Build FQL filter from smart flags
    fql_filters = []

    # Add user's custom filter first
    if filter_:
        fql_filters.append(filter_)

    # Tag filtering (AND logic - all tags must be present)
    if tag:
        for t in tag:
            fql_filters.append(f'has(tags, "{t}")')

    # Run name pattern - skip FQL filtering, do client-side instead
    # (FQL search doesn't support proper wildcard matching)

    # Model filtering (search in model-related fields)
    if model:
        # Search for model name in the run data (works across different LLM providers)
        fql_filters.append(f'search("{model}")')

    # Smart filters (deprecated - use flexible filters below)
    if slow:
        fql_filters.append('gt(latency, "5s")')

    if recent:
        # Last hour
        one_hour_ago = datetime.datetime.now(
            datetime.timezone.utc
        ) - datetime.timedelta(hours=1)
        fql_filters.append(f'gt(start_time, "{one_hour_ago.isoformat()}")')

    if today:
        # Today's runs (midnight to now)
        today_start = datetime.datetime.now(datetime.timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        fql_filters.append(f'gt(start_time, "{today_start.isoformat()}")')

    # Flexible latency filters
    if min_latency:
        duration = parse_duration_to_seconds(min_latency)
        fql_filters.append(f'gt(latency, "{duration}")')

    if max_latency:
        duration = parse_duration_to_seconds(max_latency)
        fql_filters.append(f'lt(latency, "{duration}")')

    # Flexible time filters (supports ISO, relative shorthand, and natural language)
    time_filters = build_time_fql_filters(since=since, last=last)
    fql_filters.extend(time_filters)

    # Combine all filters with AND logic
    combined_filter = combine_fql_filters(fql_filters)

    # Determine if client-side filtering is needed
    # (for run name pattern/regex matching, exclude patterns, or grep content search)
    needs_client_filtering = bool(name_regex or name_pattern or exclude or grep)

    # Determine fetch limit (how many runs to fetch from API)
    if fetch is not None:
        # User explicitly specified --fetch, use that value
        api_limit = fetch
    elif needs_client_filtering:
        # Automatic 3x multiplier for client-side filtering
        # Fetch 3x the limit or at least 100 runs to find pattern matches
        # Cap at 500 to avoid API timeouts (10x multiplier caused 0 results for limit=20+)
        # If no limit specified, cap at 1000 to avoid downloading everything
        if limit:
            api_limit = min(max(limit * 3, 100), 500)
        else:
            api_limit = 1000
    else:
        # No client-side filtering, fetch exactly what was requested
        # Convert 0 to None for SDK (0 means "no limit" in CLI, but SDK expects None)
        api_limit = None if limit == 0 else limit

    # Inform user about fetch strategy for client-side filtering
    if needs_client_filtering and api_limit != limit:
        active_filters = []
        if name_pattern:
            active_filters.append(f"--name-pattern '{name_pattern}'")
        if name_regex:
            active_filters.append(f"--name-regex '{name_regex}'")
        if exclude:
            active_filters.append(f"--exclude '{exclude}'")
        if grep:
            active_filters.append(f"--grep '{grep}'")
        filters_str = ", ".join(active_filters)

        if fetch is not None:
            # User explicitly set --fetch
            logger.info(
                f"Fetching {api_limit} runs (--fetch {fetch}) to evaluate client-side filters ({filters_str})"
            )
        else:
            # Automatic 3x multiplier
            logger.info(
                f"Fetching {api_limit} runs to evaluate client-side filters ({filters_str})"
            )
        logger.info(
            f"Will return up to {limit or 'all'} matching results. "
            f"Use --fetch to control how many runs to evaluate."
        )

    # Fetch runs from all matching projects using universal helper
    result = fetch_from_projects(
        client,
        projects_to_query,
        lambda c, proj, **kw: c.list_runs(project_name=proj, **kw),
        limit=api_limit,
        query=query,
        error=error_filter,
        filter=combined_filter,
        trace_id=trace_id,
        run_type=run_type,
        is_root=is_root,
        trace_filter=trace_filter,
        tree_filter=tree_filter,
        order_by=order_by,
        reference_example_id=reference_example_id,
        console=None,  # Don't auto-report warnings (we have custom diagnostics below)
    )
    all_runs = result.items
    failed_projects = result.failed_sources

    # CRITICAL: Fail fast if ALL sources failed (prevents silent failures)
    # In JSON mode, output empty array before failing for parseable output
    if result.all_failed and (ctx.obj.get("json") or output_format in ["csv", "yaml"]):
        format_type = determine_output_format(output_format, ctx.obj.get("json"))
        output_formatted_data([], format_type)
    result.raise_if_all_failed(logger, "runs")

    # Report partial failures (some succeeded, some failed)
    if result.has_failures:
        result.report_failures_to_logger(logger)

    # Apply universal filtering to run names (client-side filtering)
    # FQL doesn't support full regex or complex patterns for run names
    runs = get_matching_items(
        all_runs,
        name_pattern=name_pattern,
        name_regex=name_regex,
        name_getter=lambda r: r.name or "",
    )

    # Client-side exclude filtering
    runs = apply_exclude_filter(runs, exclude, lambda r: r.name or "")

    # Client-side grep/content filtering
    if grep:
        # Parse grep-in fields if specified
        grep_fields_tuple = ()
        if grep_in:
            grep_fields_tuple = tuple(
                f.strip() for f in grep_in.split(",") if f.strip()
            )

        runs = apply_grep_filter(
            runs,
            grep_pattern=grep,
            grep_fields=grep_fields_tuple,
            ignore_case=grep_ignore_case,
            use_regex=grep_regex,
        )

    # Client-side sorting for table output
    if sort_by and not ctx.obj.get("json"):
        # Map sort field to run attribute
        sort_key_map = {
            "name": lambda r: (r.name or "").lower(),
            "status": lambda r: r.status or "",
            "latency": lambda r: r.latency if r.latency is not None else 0,
            "start_time": lambda r: r.start_time
            if hasattr(r, "start_time")
            else datetime.datetime.min,
        }
        runs = sort_items(runs, sort_by, sort_key_map, console)

    # Track total count before applying limit (for showing "more may exist" message)
    total_count = len(runs)

    # Apply user's limit AFTER all client-side filtering/sorting
    runs = apply_client_side_limit(runs, limit, needs_client_filtering)

    # Track if we hit the limit
    hit_limit = limit is not None and limit > 0 and total_count > limit

    # Report filtering results if client-side filtering was used
    if needs_client_filtering and not ctx.obj.get("json"):
        matches_found = len(runs)

        if limit and matches_found < limit:
            # Under-fetched: didn't find enough matches
            logger.warning(
                f"Found {matches_found}/{limit} requested matches "
                f"after evaluating {api_limit} runs."
            )
            logger.warning(
                f"Tip: Increase --limit to fetch more runs and find more matches "
                f"(current fetch limit: {api_limit})."
            )
        elif matches_found > 0:
            # Success: found enough matches
            logger.info(
                f"Found {matches_found} matches after evaluating {len(all_runs)} runs."
            )

    # Handle count mode - short circuit all other output
    if count:
        click.echo(str(len(runs)))
        return

    # Handle file output - short circuit if writing to file
    if output:
        data = filter_fields(runs, fields)
        write_output_to_file(data, output, console, format_type="jsonl")
        return

    # Determine output format
    format_type = determine_output_format(output_format, ctx.obj.get("json"))

    # Handle non-table formats
    if format_type != "table":
        # Use filter_fields for field filtering (runs is always a list)
        data = filter_fields(runs, fields)
        output_formatted_data(data, format_type)
        return

    # Build descriptive table title
    if len(projects_to_query) == 1:
        table_title = f"Runs ({projects_to_query[0]})"
    else:
        table_title = f"Runs ({len(projects_to_query)} projects)"

    # Use shared table builder utility
    table = build_runs_table(runs, table_title, no_truncate)

    if len(runs) == 0:
        # Provide helpful diagnostic message
        logger.warning("No runs found matching your criteria.")

        # Build list of active filters
        active_filters = []
        if len(projects_to_query) == 1:
            active_filters.append(f"project: {projects_to_query[0]}")
        elif len(projects_to_query) > 1:
            active_filters.append(f"projects: {len(projects_to_query)} matched")
        if query:
            active_filters.append(f'--query "{query}"')
        if grep:
            active_filters.append(f'--grep "{grep}"')
        if status:
            active_filters.append(f"--status {status}")
        if failed:
            active_filters.append("--failed")
        if succeeded:
            active_filters.append("--succeeded")
        if roots or is_root:
            active_filters.append("--roots")
        if run_type:
            active_filters.append(f"--run-type {run_type}")
        if name_pattern:
            active_filters.append(f'--name-pattern "{name_pattern}"')
        if name_regex:
            active_filters.append(f'--name-regex "{name_regex}"')
        if filter_:
            active_filters.append("--filter (custom FQL)")
        if limit and limit < 100:
            active_filters.append(f"--limit {limit}")

        if active_filters:
            logger.info("Active filters:")
            for f in active_filters:
                logger.info(f"  • {f}")

        # Show failed projects if any
        if failed_projects:
            logger.warning("Some projects failed to fetch:")
            for proj, error_msg in failed_projects[:3]:  # Show first 3 errors
                # Truncate long error messages
                short_error = (
                    error_msg[:100] + "..." if len(error_msg) > 100 else error_msg
                )
                logger.warning(f"  • {proj}: {short_error}")
            if len(failed_projects) > 3:
                logger.warning(f"  • ... and {len(failed_projects) - 3} more")

        # Provide suggestions
        logger.info("Try:")
        if roots or is_root:
            logger.info("  • Remove --roots flag to see all runs (including nested)")
        if limit and limit < 100:
            logger.info(f"  • Increase --limit (current: {limit})")
        if grep or query or filter_:
            logger.info("  • Broaden search criteria or remove filters")
        if len(projects_to_query) > 0:
            logger.info(
                f"  • Verify project exists: langsmith-cli projects list --name-pattern {projects_to_query[0]}"
            )
        logger.info("  • Check project has runs: langsmith-cli runs list --limit 1")
    else:
        console.print(table)

        # Show message if we hit the limit (not in count mode or JSON mode)
        if hit_limit and not count and not ctx.obj.get("json"):
            # Show the exact number we know
            logger.info(
                f"Showing {len(runs)} of {total_count} runs. "
                f"Use --limit 0 to see all {total_count} runs."
            )


@runs.command("get")
@click.argument("run_id")
@fields_option(
    "Comma-separated field names to include (e.g., 'id,name,inputs,error'). Reduces context usage."
)
@click.pass_context
def get_run(ctx, run_id, fields):
    """Fetch details of a single run."""
    client = get_or_create_client(ctx)
    run = client.read_run(run_id)

    # Use shared field filtering utility
    data = filter_fields(run, fields)

    if ctx.obj.get("json"):
        click.echo(json_dumps(data))
        return

    # Human-readable output using shared helper
    render_run_details(data, console)


@runs.command("get-latest")
@add_project_filter_options
@click.option(
    "--status", type=click.Choice(["success", "error"]), help="Filter by status."
)
@click.option(
    "--failed",
    is_flag=True,
    help="Show only failed runs (shorthand for --status error).",
)
@click.option(
    "--succeeded",
    is_flag=True,
    help="Show only successful runs (shorthand for --status success).",
)
@click.option("--roots", is_flag=True, help="Get latest root trace only.")
@click.option("--tag", multiple=True, help="Filter by tag (can specify multiple).")
@click.option("--model", help="Filter by model name (e.g. 'gpt-4', 'claude-3').")
@click.option("--slow", is_flag=True, help="Filter to slow runs (latency > 5s).")
@click.option("--recent", is_flag=True, help="Filter to recent runs (last hour).")
@click.option("--today", is_flag=True, help="Filter to today's runs.")
@click.option("--min-latency", help="Minimum latency (e.g., '2s', '500ms').")
@click.option("--max-latency", help="Maximum latency (e.g., '10s', '2000ms').")
@click.option(
    "--since", help="Show runs since time (ISO or relative like '1 hour ago')."
)
@click.option("--last", help="Show runs from last duration (e.g., '24h', '7d', '30m').")
@click.option("--filter", "filter_", help="Custom FQL filter string.")
@fields_option(
    "Comma-separated field names (e.g., 'id,name,inputs,outputs'). Reduces context."
)
@click.pass_context
def get_latest_run(
    ctx,
    project,
    project_name,
    project_name_exact,
    project_name_pattern,
    project_name_regex,
    status,
    failed,
    succeeded,
    roots,
    tag,
    model,
    slow,
    recent,
    today,
    min_latency,
    max_latency,
    since,
    last,
    filter_,
    fields,
):
    """Get the most recent run from a project.

    This is a convenience command that fetches the latest run matching your filters,
    eliminating the need for piping `runs list` into `jq` and then `runs get`.

    Examples:
        # Get latest run with just inputs/outputs
        langsmith-cli --json runs get-latest --project my-project --fields inputs,outputs

        # Get latest successful run
        langsmith-cli --json runs get-latest --project my-project --succeeded

        # Get latest error from production projects
        langsmith-cli --json runs get-latest --project-name-pattern "prd/*" --failed --fields id,name,error

        # Get latest slow run from last hour
        langsmith-cli --json runs get-latest --project my-project --slow --recent --fields name,latency
    """
    logger = ctx.obj["logger"]

    # Determine if output is machine-readable (use stderr for diagnostics)
    is_machine_readable = ctx.obj.get("json") or fields
    logger.use_stderr = is_machine_readable

    client = get_or_create_client(ctx)
    logger.debug(f"Getting latest run with filters: project={project}, status={status}")

    # Get matching projects
    projects_to_query = get_matching_projects(
        client,
        project=project,
        name=project_name,
        name_exact=project_name_exact,
        name_pattern=project_name_pattern,
        name_regex=project_name_regex,
    )

    # Build filter using shared helper
    combined_filter, error_filter = build_runs_list_filter(
        filter_=filter_,
        status=status,
        failed=failed,
        succeeded=succeeded,
        tag=tag,
        model=model,
        slow=slow,
        recent=recent,
        today=today,
        min_latency=min_latency,
        max_latency=max_latency,
        since=since,
        last=last,
    )

    # Search projects in order until we find a run
    latest_run = None
    failed_projects = []
    for proj_name in projects_to_query:
        try:
            runs_iter = client.list_runs(
                project_name=proj_name,
                limit=1,
                error=error_filter,
                filter=combined_filter,
                is_root=roots,
                order_by="-start_time",
            )
            latest_run = next(runs_iter, None)
            if latest_run:
                break  # Found a run, stop searching
        except Exception as e:
            # Track failed projects for diagnostics
            failed_projects.append((proj_name, str(e)))
            continue

    if not latest_run:
        logger.warning("No runs found matching the specified filters")

        # Show failed projects if any
        if failed_projects:
            logger.warning("Some projects failed to fetch:")
            for proj, error_msg in failed_projects[:3]:
                short_error = (
                    error_msg[:100] + "..." if len(error_msg) > 100 else error_msg
                )
                logger.warning(f"  • {proj}: {short_error}")
            if len(failed_projects) > 3:
                logger.warning(f"  • ... and {len(failed_projects) - 3} more")

        raise click.Abort()

    # Use shared field filtering
    data = filter_fields(latest_run, fields)

    if ctx.obj.get("json"):
        click.echo(json_dumps(data))
        return

    # Human-readable output using shared helper
    render_run_details(data, console, title="Latest Run")


@runs.command("view-file")
@click.argument("pattern")
@click.option(
    "--no-truncate",
    is_flag=True,
    help="Don't truncate long fields in table output (shows full content in all columns).",
)
@fields_option()
@click.pass_context
def view_file(ctx, pattern, no_truncate, fields):
    """View runs from JSONL files with table display.

    Supports glob patterns to read multiple files.

    Examples:
        langsmith-cli runs view-file samples.jsonl
        langsmith-cli runs view-file "data/*.jsonl"
        langsmith-cli runs view-file samples.jsonl --no-truncate
        langsmith-cli runs view-file samples.jsonl --fields id,name,status
        langsmith-cli --json runs view-file samples.jsonl
    """
    logger = ctx.obj["logger"]

    # Determine if output is machine-readable
    is_machine_readable = ctx.obj.get("json") or fields
    logger.use_stderr = is_machine_readable

    import glob
    from langsmith.schemas import Run

    # Find matching files using glob
    file_paths = glob.glob(pattern)

    if not file_paths:
        logger.error(f"No files match pattern: {pattern}")
        raise click.Abort()

    # Read all runs from matching files
    runs: list[Run] = []
    for file_path in sorted(file_paths):
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                for line_num, line in enumerate(f, 1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        # Convert dict to Run object using Pydantic validation
                        run = Run.model_validate(data)
                        runs.append(run)
                    except json.JSONDecodeError as e:
                        logger.warning(f"Invalid JSON at {file_path}:{line_num} - {e}")
                    except Exception as e:
                        logger.warning(
                            f"Failed to parse run at {file_path}:{line_num} - {e}"
                        )
        except Exception as e:
            logger.error(f"Error reading {file_path}: {e}")
            continue

    if not runs:
        logger.warning("No valid runs found in files.")
        return

    # Handle JSON output
    if ctx.obj.get("json"):
        data = filter_fields(runs, fields)
        output_formatted_data(data, "json")
        return

    # Build descriptive title
    if len(file_paths) == 1:
        table_title = f"Runs from {file_paths[0]}"
    else:
        table_title = f"Runs from {len(file_paths)} files"

    # Use shared table builder utility
    table = build_runs_table(runs, table_title, no_truncate)

    if len(runs) == 0:
        logger.warning("No runs found.")
    else:
        console.print(table)
        logger.info(f"Loaded {len(runs)} runs from {len(file_paths)} file(s)")


@runs.command("stats")
@add_project_filter_options
@click.pass_context
def run_stats(
    ctx,
    project,
    project_name,
    project_name_exact,
    project_name_pattern,
    project_name_regex,
):
    """Fetch aggregated metrics for one or more projects.

    Use project filters to match multiple projects and get combined statistics.
    """
    client = get_or_create_client(ctx)

    # Get matching projects using universal helper
    projects_to_query = get_matching_projects(
        client,
        project=project,
        name=project_name,
        name_exact=project_name_exact,
        name_pattern=project_name_pattern,
        name_regex=project_name_regex,
    )

    # Resolve project names to IDs
    project_ids = []
    for proj_name in projects_to_query:
        try:
            p = client.read_project(project_name=proj_name)
            project_ids.append(p.id)
        except Exception:
            # Fallback: use project name as ID (user might have passed ID directly)
            project_ids.append(proj_name)

    if not project_ids:
        console.print("[yellow]No matching projects found.[/yellow]")
        return

    stats = client.get_run_stats(project_ids=project_ids)

    if ctx.obj.get("json"):
        click.echo(json_dumps(stats))
        return

    # Build descriptive title
    if len(projects_to_query) == 1:
        table_title = f"Stats: {projects_to_query[0]}"
    else:
        table_title = f"Stats: {len(projects_to_query)} projects"

    table = Table(title=table_title)
    table.add_column("Metric")
    table.add_column("Value")

    for k, v in stats.items():
        table.add_row(k.replace("_", " ").title(), str(v))

    console.print(table)


@runs.command("open")
@click.argument("run_id")
@click.pass_context
def open_run(ctx, run_id):
    """Open a run in the LangSmith UI."""
    import webbrowser

    # Construct the URL. Note: A generic URL works if the user is logged in.
    # The SDK also has a way to get the URL but it might require project name.
    url = f"https://smith.langchain.com/r/{run_id}"

    click.echo(f"Opening run {run_id} in browser...")
    click.echo(f"URL: {url}")
    webbrowser.open(url)


@runs.command("watch")
@add_project_filter_options
@click.option("--interval", default=2.0, help="Refresh interval in seconds.")
@click.pass_context
def watch_runs(
    ctx,
    project,
    project_name,
    project_name_exact,
    project_name_pattern,
    project_name_regex,
    interval,
):
    """Live dashboard of runs (root traces only).

    Watch a single project or multiple projects matching filters.

    Examples:
        langsmith-cli runs watch --project my-project
        langsmith-cli runs watch --project-name-pattern "dev/*"
        langsmith-cli runs watch --project-name-exact "production-api"
        langsmith-cli runs watch --project-name-regex "^dev-.*-v[0-9]+$"
        langsmith-cli runs watch --project-name prod
    """
    from rich.live import Live
    import time

    client = get_or_create_client(ctx)

    def generate_table():
        # Get projects to watch using universal helper
        projects_to_watch = get_matching_projects(
            client,
            project=project,
            name=project_name,
            name_exact=project_name_exact,
            name_pattern=project_name_pattern,
            name_regex=project_name_regex,
        )
        # Build descriptive title based on filter used
        if project_name_exact:
            title = f"Watching: {project_name_exact}"
        elif project_name_regex:
            title = f"Watching: regex({project_name_regex}) ({len(projects_to_watch)} projects)"
        elif project_name_pattern:
            title = (
                f"Watching: {project_name_pattern} ({len(projects_to_watch)} projects)"
            )
        elif project_name:
            title = f"Watching: *{project_name}* ({len(projects_to_watch)} projects)"
        elif len(projects_to_watch) > 1:
            title = f"Watching: {len(projects_to_watch)} projects"
        else:
            title = f"Watching: {project}"
        title += f" (Interval: {interval}s)"

        table = Table(title=title)
        table.add_column("Name", style="cyan")
        table.add_column("Project", style="dim")
        table.add_column("Status", justify="center")
        table.add_column("Tokens", justify="right")
        table.add_column("Latency", justify="right")

        # Collect runs from all matching projects
        # Store runs with their project names as tuples
        all_runs: list[tuple[str, Run]] = []
        failed_count = 0
        for proj_name in projects_to_watch:
            try:
                # Get a few runs from each project
                runs = list(
                    client.list_runs(
                        project_name=proj_name,
                        limit=5 if project_name_pattern else 10,
                        is_root=True,
                    )
                )
                # Store each run with its project name
                all_runs.extend((proj_name, run) for run in runs)
            except Exception:
                # Track failed projects but don't spam console in watch mode
                failed_count += 1
                pass

        # Sort by start time (most recent first) and limit to 10
        all_runs.sort(key=lambda item: item[1].start_time or "", reverse=True)
        all_runs = all_runs[:10]

        # Add failure count to title if any projects failed
        if failed_count > 0:
            title += f" [yellow]({failed_count} failed)[/yellow]"

        for proj_name, r in all_runs:
            # Access SDK model fields directly (type-safe)
            r_name = r.name or "Unknown"
            r_project = proj_name
            r_status = r.status
            status_style = (
                "green"
                if r_status == "success"
                else "red"
                if r_status == "error"
                else "yellow"
            )

            # Get token counts
            total_tokens = r.total_tokens or 0
            tokens_str = f"{total_tokens:,}" if total_tokens > 0 else "-"

            latency = f"{r.latency:.2f}s" if r.latency is not None else "-"

            table.add_row(
                r_name,
                r_project,
                f"[{status_style}]{r_status}[/{status_style}]",
                tokens_str,
                latency,
            )
        return table

    with Live(generate_table(), refresh_per_second=1 / interval) as live:
        try:
            while True:
                time.sleep(interval)
                live.update(generate_table())
        except KeyboardInterrupt:
            pass


@runs.command("search")
@click.argument("query")
@add_project_filter_options
@add_time_filter_options
@click.option("--limit", default=10, help="Max results.")
@click.option(
    "--roots",
    is_flag=True,
    help="Show only root traces (cleaner output).",
)
@click.option(
    "--in",
    "search_in",
    type=click.Choice(["all", "inputs", "outputs", "error"]),
    default="all",
    help="Where to search (default: all fields).",
)
@click.option(
    "--input-contains", help="Filter by content in inputs (JSON path or text)."
)
@click.option(
    "--output-contains", help="Filter by content in outputs (JSON path or text)."
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["table", "json", "csv", "yaml"]),
    help="Output format.",
)
@click.pass_context
def search_runs(
    ctx,
    query,
    project,
    project_name,
    project_name_exact,
    project_name_pattern,
    project_name_regex,
    since,
    last,
    limit,
    roots,
    search_in,
    input_contains,
    output_contains,
    output_format,
):
    """Search runs using full-text search across one or more projects.

    QUERY is the text to search for across runs.

    Use project filters to search across multiple projects.

    Examples:
      langsmith-cli runs search "authentication failed"
      langsmith-cli runs search "timeout" --in error
      langsmith-cli runs search "user_123" --in inputs
      langsmith-cli runs search "error" --project-name-pattern "prod-*"
    """
    # Build FQL filter for full-text search
    filter_expr = f'search("{query}")'

    # Add field-specific filters if provided
    filters = [filter_expr]

    if input_contains:
        filters.append(f'search("{input_contains}")')

    if output_contains:
        filters.append(f'search("{output_contains}")')

    # Combine filters with AND (filters always has at least one element from query)
    combined_filter = combine_fql_filters(filters) or filters[0]

    # Invoke list_runs with the filter and project filters
    return ctx.invoke(
        list_runs,
        project=project,
        project_name=project_name,
        project_name_exact=project_name_exact,
        project_name_pattern=project_name_pattern,
        project_name_regex=project_name_regex,
        limit=limit,
        filter_=combined_filter,
        output_format=output_format,
        # Pass through other required args with defaults
        status=None,
        trace_id=None,
        run_type=None,
        is_root=None,
        roots=roots,  # Pass through --roots flag
        trace_filter=None,
        tree_filter=None,
        order_by="-start_time",
        reference_example_id=None,
        tag=(),
        name_pattern=None,
        name_regex=None,
        model=None,
        failed=False,
        succeeded=False,
        slow=False,
        recent=False,
        today=False,
        min_latency=None,
        max_latency=None,
        since=since,  # Pass through time filters
        last=last,  # Pass through time filters
        sort_by=None,
        fields=None,  # Pass through fields parameter
    )


@runs.command("sample")
@add_project_filter_options
@add_time_filter_options
@click.option(
    "--stratify-by",
    required=True,
    help="Grouping field(s). Single: 'tag:length', Multi: 'tag:length,tag:type'",
)
@click.option(
    "--values",
    help="Comma-separated stratum values (single dimension) or colon-separated combinations (multi-dimensional). Examples: 'short,medium,long' or 'short:news,medium:news,long:gaming'",
)
@click.option(
    "--dimension-values",
    help="Pipe-separated values per dimension for Cartesian product (multi-dimensional only). Example: 'short|medium|long,news|gaming' generates all 6 combinations",
)
@click.option(
    "--samples-per-stratum",
    default=10,
    help="Number of samples per stratum (default: 10)",
)
@click.option(
    "--samples-per-combination",
    type=int,
    help="Samples per combination (multi-dimensional). Overrides --samples-per-stratum if set",
)
@click.option(
    "--output",
    help="Output file path (JSONL format). If not specified, writes to stdout.",
)
@click.option(
    "--filter",
    "additional_filter",
    help="Additional FQL filter to apply before sampling",
)
@fields_option()
@click.pass_context
def sample_runs(
    ctx,
    project,
    project_name,
    project_name_exact,
    project_name_pattern,
    project_name_regex,
    since,
    last,
    stratify_by,
    values,
    dimension_values,
    samples_per_stratum,
    samples_per_combination,
    output,
    additional_filter,
    fields,
):
    """Sample runs using stratified sampling by tags or metadata.

    This command collects balanced samples from different groups (strata) to ensure
    representative coverage across categories.

    Supports both single-dimensional and multi-dimensional stratification.

    Examples:
        # Single dimension: Sample by tag-based length categories
        langsmith-cli runs sample \\
          --project my-project \\
          --stratify-by "tag:length_category" \\
          --values "short,medium,long" \\
          --samples-per-stratum 20 \\
          --output stratified_sample.jsonl

        # Multi-dimensional: Sample by length and content type (Cartesian product)
        langsmith-cli runs sample \\
          --project my-project \\
          --stratify-by "tag:length,tag:content_type" \\
          --dimension-values "short|medium|long,news|gaming" \\
          --samples-per-combination 5

        # Multi-dimensional: Manual combinations
        langsmith-cli runs sample \\
          --project my-project \\
          --stratify-by "tag:length,tag:content_type" \\
          --values "short:news,medium:gaming,long:news" \\
          --samples-per-stratum 10

        # With time filtering: Sample only recent runs
        langsmith-cli runs sample \\
          --project my-project \\
          --stratify-by "tag:length_category" \\
          --values "short,medium,long" \\
          --since "3 days ago" \\
          --samples-per-stratum 100
    """
    logger = ctx.obj["logger"]

    # Determine if output is machine-readable
    is_machine_readable = output is not None or fields
    logger.use_stderr = is_machine_readable

    import itertools

    logger.debug(f"Sampling runs with stratify_by={stratify_by}, values={values}")

    # Build time filters and combine with additional_filter
    time_filters = build_time_fql_filters(since=since, last=last)
    base_filters = time_filters.copy()
    if additional_filter:
        base_filters.append(additional_filter)

    # Combine base filters into a single filter
    base_filter = combine_fql_filters(base_filters)

    client = get_or_create_client(ctx)

    # Parse stratify-by field (can be single or multi-dimensional)
    parsed = parse_grouping_field(stratify_by)
    is_multi_dimensional = isinstance(parsed, list)

    # Get matching projects
    projects_to_query = get_matching_projects(
        client,
        project=project,
        name=project_name,
        name_exact=project_name_exact,
        name_pattern=project_name_pattern,
        name_regex=project_name_regex,
    )

    all_samples = []

    if is_multi_dimensional:
        # Multi-dimensional stratification
        dimensions = parsed

        # Determine sample limit
        sample_limit = (
            samples_per_combination if samples_per_combination else samples_per_stratum
        )

        # Generate combinations
        if dimension_values:
            # Cartesian product: parse pipe-separated values per dimension
            dimension_value_lists = [
                [v.strip() for v in dim_vals.split("|")]
                for dim_vals in dimension_values.split(",")
            ]
            if len(dimension_value_lists) != len(dimensions):
                raise click.BadParameter(
                    f"Number of dimension value groups ({len(dimension_value_lists)}) "
                    f"must match number of dimensions ({len(dimensions)})"
                )
            combinations = list(itertools.product(*dimension_value_lists))
        elif values:
            # Manual combinations: parse colon-separated values
            combinations = [
                tuple(v.strip() for v in combo.split(":"))
                for combo in values.split(",")
            ]
            # Validate each combination has correct number of dimensions
            for combo in combinations:
                if len(combo) != len(dimensions):
                    raise click.BadParameter(
                        f"Combination {combo} has {len(combo)} values but expected {len(dimensions)}"
                    )
        else:
            raise click.BadParameter(
                "Multi-dimensional stratification requires --values or --dimension-values"
            )

        # Fetch samples for each combination
        for combination_values in combinations:
            # Build FQL filter for this combination
            stratum_filter = build_multi_dimensional_fql_filter(
                dimensions, list(combination_values)
            )

            # Combine stratum filter with base filter (time + additional filters)
            filters_to_combine = [stratum_filter]
            if base_filter:
                filters_to_combine.append(base_filter)
            combined_filter = combine_fql_filters(filters_to_combine)

            # Fetch samples from all matching projects using universal helper
            result = fetch_from_projects(
                client,
                projects_to_query,
                lambda c, proj, **kw: c.list_runs(project_name=proj, **kw),
                limit=sample_limit,
                filter=combined_filter,
                order_by="-start_time",
                console=console,
            )
            stratum_runs = result.items[:sample_limit]

            # Add stratum field and convert to dicts
            for run in stratum_runs:
                run_dict = filter_fields(run, fields)
                # Build stratum label with all dimensions
                stratum_label = ",".join(
                    f"{field_name}:{value}"
                    for (_, field_name), value in zip(dimensions, combination_values)
                )
                run_dict["stratum"] = stratum_label
                all_samples.append(run_dict)

    else:
        # Single-dimensional stratification (backward compatible)
        grouping_type, field_name = parsed

        if not values:
            raise click.BadParameter(
                "Single-dimensional stratification requires --values"
            )

        # Parse values
        stratum_values = [v.strip() for v in values.split(",")]

        # Collect samples for each stratum
        for stratum_value in stratum_values:
            # Build FQL filter for this stratum
            stratum_filter = build_grouping_fql_filter(
                grouping_type, field_name, stratum_value
            )

            # Combine stratum filter with base filter (time + additional filters)
            filters_to_combine = [stratum_filter]
            if base_filter:
                filters_to_combine.append(base_filter)
            combined_filter = combine_fql_filters(filters_to_combine)

            # Fetch samples from all matching projects using universal helper
            result = fetch_from_projects(
                client,
                projects_to_query,
                lambda c, proj, **kw: c.list_runs(project_name=proj, **kw),
                limit=samples_per_stratum,
                filter=combined_filter,
                order_by="-start_time",
                console=console,
            )
            stratum_runs = result.items[:samples_per_stratum]

            # Add stratum field and convert to dicts
            for run in stratum_runs:
                run_dict = filter_fields(run, fields)
                run_dict["stratum"] = f"{field_name}:{stratum_value}"
                all_samples.append(run_dict)

    # Output as JSONL
    if output:
        # Write to file
        try:
            with open(output, "w", encoding="utf-8") as f:
                for sample in all_samples:
                    f.write(json_dumps(sample) + "\n")
            logger.success(f"Wrote {len(all_samples)} samples to {output}")
        except Exception as e:
            logger.error(f"Error writing to file {output}: {e}")
            raise click.Abort()
    else:
        # Write to stdout (JSONL format)
        for sample in all_samples:
            click.echo(json_dumps(sample))


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
    project_name,
    project_name_exact,
    project_name_pattern,
    project_name_regex,
    since,
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
    time_filters = build_time_fql_filters(since=since, last=last)
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
    projects_to_query = get_matching_projects(
        client,
        project=project,
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
            projects_to_query,
            lambda c, proj, **kw: c.list_runs(project_name=proj, **kw),
            filter=combined_filter,
            limit=None,
            order_by="-start_time",
            console=console,
        )
        all_runs = result.items
    else:
        # Use sample-based approach with field selection (FAST!)
        # API has max limit of 100 when using select, so manually collect from iterator
        failed_projects = []
        for proj_name in projects_to_query:
            try:
                runs_iter = client.list_runs(
                    project_name=proj_name,
                    filter=combined_filter,
                    limit=None,  # SDK paginates automatically
                    order_by="-start_time",
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
                failed_projects.append((proj_name, str(e)))

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


# Discovery command helpers


@dataclass
class DiscoveryContext:
    """Context returned by _fetch_runs_for_discovery."""

    runs: list[Run]
    projects: list[str]
    logger: Any  # CLILogger


def _fetch_runs_for_discovery(
    ctx,
    project: str | None,
    project_name: str | None,
    project_name_exact: str | None,
    project_name_pattern: str | None,
    project_name_regex: str | None,
    since: str | None,
    last: str | None,
    sample_size: int,
    select: list[str] | None = None,
    cmd_name: str = "discovery",
) -> DiscoveryContext:
    """Shared setup for discovery commands (tags, metadata-keys, fields, describe).

    Handles the common pattern of:
    - Setting up logger with stderr mode
    - Building and combining time filters
    - Getting matching projects
    - Fetching runs with optional field selection

    Args:
        ctx: Click context
        project: Project ID or name
        project_name: Substring filter for project name
        project_name_exact: Exact project name filter
        project_name_pattern: Glob pattern for project name
        project_name_regex: Regex pattern for project name
        since: Time filter (since)
        last: Time filter (last duration)
        sample_size: Number of runs to sample
        select: Optional list of fields to fetch (for performance)
        cmd_name: Command name for debug logging

    Returns:
        DiscoveryContext with runs, projects list, and logger
    """
    logger = ctx.obj["logger"]

    # Determine if output is machine-readable (use stderr for diagnostics)
    is_machine_readable = ctx.obj.get("json")
    logger.use_stderr = is_machine_readable

    client = get_or_create_client(ctx)
    logger.debug(f"Running {cmd_name} with sample_size={sample_size}")

    # Build and combine time filters
    time_filters = build_time_fql_filters(since=since, last=last)
    combined_filter = combine_fql_filters(time_filters)

    # Get matching projects
    projects_to_query = get_matching_projects(
        client,
        project=project,
        name=project_name,
        name_exact=project_name_exact,
        name_pattern=project_name_pattern,
        name_regex=project_name_regex,
    )

    # Fetch runs
    logger.debug(f"Fetching {sample_size} runs for {cmd_name}...")

    result = fetch_from_projects(
        client,
        projects_to_query,
        lambda c, proj, **kw: c.list_runs(project_name=proj, **kw),
        limit=sample_size,
        order_by="-start_time",
        select=select,
        filter=combined_filter,
        console=console,
    )

    return DiscoveryContext(
        runs=result.items,
        projects=projects_to_query,
        logger=logger,
    )


@runs.command("tags")
@add_project_filter_options
@add_time_filter_options
@click.option(
    "--sample-size",
    default=1000,
    type=int,
    help="Number of recent runs to sample for discovery (default: 1000)",
)
@click.pass_context
def discover_tags(
    ctx,
    project,
    project_name,
    project_name_exact,
    project_name_pattern,
    project_name_regex,
    since,
    last,
    sample_size,
):
    """Discover tag patterns in a project.

    Analyzes recent runs to extract structured tag patterns (key:value format).
    Useful for understanding available stratification dimensions.

    Examples:
        # Discover tags in default project
        langsmith-cli runs tags

        # Discover tags in specific project with larger sample
        langsmith-cli --json runs tags --project my-project --sample-size 5000

        # Discover tags with pattern filtering
        langsmith-cli runs tags --project-name-pattern "prod/*"
    """
    from collections import defaultdict

    # Fetch runs using shared discovery helper
    discovery = _fetch_runs_for_discovery(
        ctx=ctx,
        project=project,
        project_name=project_name,
        project_name_exact=project_name_exact,
        project_name_pattern=project_name_pattern,
        project_name_regex=project_name_regex,
        since=since,
        last=last,
        sample_size=sample_size,
        select=["tags"],
        cmd_name="tags",
    )

    # Parse tags to extract key:value patterns
    tag_patterns: dict[str, set[str]] = defaultdict(set)

    for run in discovery.runs:
        if run.tags:
            for tag in run.tags:
                if ":" in tag:
                    key, value = tag.split(":", 1)
                    tag_patterns[key].add(value)

    # Convert sets to sorted lists
    result = {
        "tag_patterns": {
            key: sorted(values) for key, values in sorted(tag_patterns.items())
        }
    }

    # Output
    if ctx.obj.get("json"):
        click.echo(json_dumps(result))
    else:
        from rich.table import Table

        table = Table(title="Tag Patterns")
        table.add_column("Tag Key", style="cyan")
        table.add_column("Values", style="green")

        for key, values in result["tag_patterns"].items():
            value_str = ", ".join(values[:10])
            if len(values) > 10:
                value_str += f" ... (+{len(values) - 10} more)"
            table.add_row(key, value_str)

        if not result["tag_patterns"]:
            discovery.logger.warning("No structured tags found (key:value format).")
        else:
            console.print(table)
            discovery.logger.info(
                f"Analyzed {len(discovery.runs)} runs from {len(discovery.projects)} project(s)"
            )


@runs.command("metadata-keys")
@add_project_filter_options
@add_time_filter_options
@click.option(
    "--sample-size",
    default=1000,
    type=int,
    help="Number of recent runs to sample for discovery (default: 1000)",
)
@click.pass_context
def discover_metadata_keys(
    ctx,
    project,
    project_name,
    project_name_exact,
    project_name_pattern,
    project_name_regex,
    since,
    last,
    sample_size,
):
    """Discover metadata keys used in a project.

    Analyzes recent runs to extract all metadata keys.
    Useful for understanding available metadata-based stratification dimensions.

    Examples:
        # Discover metadata keys in default project
        langsmith-cli runs metadata-keys

        # Discover in specific project
        langsmith-cli --json runs metadata-keys --project my-project

        # Discover with pattern filtering
        langsmith-cli runs metadata-keys --project-name-pattern "prod/*"
    """
    # Fetch runs using shared discovery helper
    discovery = _fetch_runs_for_discovery(
        ctx=ctx,
        project=project,
        project_name=project_name,
        project_name_exact=project_name_exact,
        project_name_pattern=project_name_pattern,
        project_name_regex=project_name_regex,
        since=since,
        last=last,
        sample_size=sample_size,
        select=["extra"],  # Metadata is stored in extra field
        cmd_name="metadata-keys",
    )

    # Extract all metadata keys
    metadata_keys: set[str] = set()

    for run in discovery.runs:
        # Check run.metadata
        if run.metadata and isinstance(run.metadata, dict):
            metadata_keys.update(run.metadata.keys())

        # Check run.extra["metadata"]
        if run.extra and isinstance(run.extra, dict):
            extra_metadata = run.extra.get("metadata")
            if extra_metadata and isinstance(extra_metadata, dict):
                metadata_keys.update(extra_metadata.keys())

    result = {"metadata_keys": sorted(metadata_keys)}

    # Output
    if ctx.obj.get("json"):
        click.echo(json_dumps(result))
    else:
        from rich.table import Table

        table = Table(title="Metadata Keys")
        table.add_column("Key", style="cyan")
        table.add_column("Type", style="dim")

        for key in result["metadata_keys"]:
            table.add_row(key, "metadata")

        if not result["metadata_keys"]:
            discovery.logger.warning("No metadata keys found.")
        else:
            console.print(table)
            discovery.logger.info(
                f"Analyzed {len(discovery.runs)} runs from {len(discovery.projects)} project(s)"
            )


def _field_analysis_common(
    ctx,
    project: str | None,
    project_name: str | None,
    project_name_exact: str | None,
    project_name_pattern: str | None,
    project_name_regex: str | None,
    since: str | None,
    last: str | None,
    sample_size: int,
    include: str | None,
    exclude: str | None,
    no_language: bool,
    show_detailed_stats: bool,
) -> None:
    """Shared logic for runs fields and runs describe commands.

    Args:
        ctx: Click context
        project: Project ID or name
        project_name: Substring filter for project name
        project_name_exact: Exact project name filter
        project_name_pattern: Glob pattern for project name
        project_name_regex: Regex pattern for project name
        since: Time filter (since)
        last: Time filter (last duration)
        sample_size: Number of runs to sample
        include: Comma-separated paths to include
        exclude: Comma-separated paths to exclude
        no_language: Skip language detection
        show_detailed_stats: If True, show length/numeric stats (describe mode).
                            If False, show sample values (fields mode).
    """
    from langsmith_cli.field_analysis import (
        FieldStats,
        analyze_runs_fields,
        filter_fields_by_path,
        format_languages_display,
        format_length_stats,
        format_numeric_stats,
    )

    cmd_name = "describe" if show_detailed_stats else "fields"

    # Fetch runs using shared discovery helper (no select - need full run data)
    discovery = _fetch_runs_for_discovery(
        ctx=ctx,
        project=project,
        project_name=project_name,
        project_name_exact=project_name_exact,
        project_name_pattern=project_name_pattern,
        project_name_regex=project_name_regex,
        since=since,
        last=last,
        sample_size=sample_size,
        select=None,  # Need full run data for field analysis
        cmd_name=cmd_name,
    )

    if not discovery.runs:
        if ctx.obj.get("json"):
            click.echo(json_dumps({"fields": [], "total_runs": 0}))
        else:
            discovery.logger.warning("No runs found.")
        return

    # Convert runs to dicts for analysis
    discovery.logger.debug(f"Analyzing fields across {len(discovery.runs)} runs...")
    runs_data = [run.model_dump(mode="json") for run in discovery.runs]

    # Analyze fields
    stats_list = analyze_runs_fields(runs_data, detect_languages=not no_language)

    # Apply path filters
    include_paths = [p.strip() for p in include.split(",")] if include else None
    exclude_paths = [p.strip() for p in exclude.split(",")] if exclude else None
    stats_list = filter_fields_by_path(stats_list, include_paths, exclude_paths)

    # Output JSON (same format for both commands)
    if ctx.obj.get("json"):
        output = {
            "fields": [s.to_dict() for s in stats_list],
            "total_runs": len(discovery.runs),
            "meta": {
                "lang_detect_enabled": not no_language,
                "lang_detect_sample_size": 500,
                "lang_detect_min_length": 30,
            },
        }
        click.echo(json_dumps(output))
        return

    # Table output - differs based on mode
    from rich.table import Table

    def render_fields_row(stats: FieldStats) -> tuple[str, str, str, str, str]:
        """Render row for 'fields' command: Path, Type, Present, Languages, Sample."""
        return (
            stats.path,
            stats.field_type,
            f"{stats.present_pct}%",
            format_languages_display(stats.languages),
            stats.sample or "-",
        )

    def render_describe_row(stats: FieldStats) -> tuple[str, str, str, str, str]:
        """Render row for 'describe' command: Path, Type, Present, Stats, Languages."""
        if stats.field_type in ("int", "float"):
            stats_str = format_numeric_stats(stats)
        else:
            stats_str = format_length_stats(stats)
        return (
            stats.path,
            stats.field_type,
            f"{stats.present_pct}%",
            stats_str,
            format_languages_display(stats.languages),
        )

    if show_detailed_stats:
        table = Table(title=f"Field Statistics ({len(discovery.runs)} runs analyzed)")
        table.add_column("Field Path", style="cyan", no_wrap=True)
        table.add_column("Type", style="dim")
        table.add_column("Present", justify="right")
        table.add_column("Length/Numeric Stats")
        table.add_column("Languages")
        for stats in stats_list:
            table.add_row(*render_describe_row(stats))
    else:
        table = Table(title=f"Fields ({len(discovery.runs)} runs analyzed)")
        table.add_column("Field Path", style="cyan", no_wrap=True)
        table.add_column("Type", style="dim")
        table.add_column("Present", justify="right")
        table.add_column("Languages")
        table.add_column("Sample", max_width=40)
        for stats in stats_list:
            table.add_row(*render_fields_row(stats))

    console.print(table)
    discovery.logger.info(
        f"Analyzed {len(discovery.runs)} runs from {len(discovery.projects)} project(s)"
    )


# Decorator stack for field analysis commands (shared options)
def add_field_analysis_options(func):
    """Add common options for field analysis commands (fields, describe)."""
    func = click.option(
        "--no-language",
        is_flag=True,
        default=False,
        help="Skip language detection (faster)",
    )(func)
    func = click.option(
        "--exclude",
        type=str,
        help="Exclude fields starting with these paths (comma-separated, e.g., 'extra,events')",
    )(func)
    func = click.option(
        "--include",
        type=str,
        help="Only include fields starting with these paths (comma-separated, e.g., 'inputs,outputs')",
    )(func)
    func = click.option(
        "--sample-size",
        default=100,
        type=int,
        help="Number of recent runs to sample (default: 100)",
    )(func)
    return func


@runs.command("fields")
@add_project_filter_options
@add_time_filter_options
@add_field_analysis_options
@click.pass_context
def discover_fields(
    ctx,
    project,
    project_name,
    project_name_exact,
    project_name_pattern,
    project_name_regex,
    since,
    last,
    sample_size,
    include,
    exclude,
    no_language,
):
    """Discover fields and their types across runs.

    Analyzes recent runs to extract all field paths, types, presence rates,
    and language distribution for text fields.

    Examples:
        # Discover fields in default project
        langsmith-cli runs fields

        # Focus on inputs/outputs only
        langsmith-cli --json runs fields --include inputs,outputs

        # Skip language detection for speed
        langsmith-cli runs fields --no-language

        # Exclude verbose fields
        langsmith-cli runs fields --exclude extra,events,serialized
    """
    _field_analysis_common(
        ctx=ctx,
        project=project,
        project_name=project_name,
        project_name_exact=project_name_exact,
        project_name_pattern=project_name_pattern,
        project_name_regex=project_name_regex,
        since=since,
        last=last,
        sample_size=sample_size,
        include=include,
        exclude=exclude,
        no_language=no_language,
        show_detailed_stats=False,
    )


@runs.command("describe")
@add_project_filter_options
@add_time_filter_options
@add_field_analysis_options
@click.pass_context
def describe_fields(
    ctx,
    project,
    project_name,
    project_name_exact,
    project_name_pattern,
    project_name_regex,
    since,
    last,
    sample_size,
    include,
    exclude,
    no_language,
):
    """Detailed field statistics with length/numeric stats.

    Like 'runs fields' but includes detailed statistics:
    - String fields: min/max/avg/p50 length
    - Numeric fields: min/max/avg/p50/sum
    - List fields: min/max/avg/p50 element count

    Examples:
        # Full statistics for all fields
        langsmith-cli runs describe

        # Focus on inputs/outputs with language detection
        langsmith-cli --json runs describe --include inputs,outputs

        # Quick analysis without language detection
        langsmith-cli runs describe --no-language --sample-size 50
    """
    _field_analysis_common(
        ctx=ctx,
        project=project,
        project_name=project_name,
        project_name_exact=project_name_exact,
        project_name_pattern=project_name_pattern,
        project_name_regex=project_name_regex,
        since=since,
        last=last,
        sample_size=sample_size,
        include=include,
        exclude=exclude,
        no_language=no_language,
        show_detailed_stats=True,
    )
