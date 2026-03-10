from dataclasses import dataclass
from typing import Any
import json

import click
from rich.console import Console
from rich.table import Table
from langsmith.schemas import Run

from langsmith_cli.utils import (
    add_grep_options,
    add_metadata_filter_options,
    add_project_filter_options,
    add_time_filter_options,
    apply_client_side_limit,
    apply_exclude_filter,
    apply_grep_filter,
    build_metadata_fql_filters,
    build_runs_list_filter,
    build_runs_table,
    build_tag_fql_filters,
    build_time_fql_filters,
    combine_fql_filters,
    count_option,
    determine_output_format,
    exclude_option,
    fetch_from_projects,
    fields_option,
    filter_fields,
    filter_runs_by_tags,
    get_matching_items,
    get_or_create_client,
    get_project_suggestions,
    json_dumps,
    output_formatted_data,
    output_option,
    output_single_item,
    parse_duration_to_seconds,
    parse_fields_option,
    raise_if_all_failed_with_suggestions,
    render_output,
    render_run_details,
    resolve_project_filters,
    sort_items,
    write_output_to_file,
)

console = Console()


@click.group()
def runs():
    """Inspect and filter application traces."""
    pass


# LangSmith API rejects limit > 100 in /runs/query requests.
# When we need more items, omit the limit from the SDK call
# (letting cursor pagination handle paging) and use islice to cap.
_API_MAX_LIMIT = 100


def _make_fetch_runs() -> Any:
    """Create a fetch function for list_runs that respects the API's max limit of 100.

    Returns a function suitable for use with fetch_from_projects.
    """
    from itertools import islice

    def _fetch_runs(c: Any, proj: str | None, **kw: Any) -> Any:
        requested_limit = kw.pop("limit", None)
        sdk_limit = requested_limit
        if requested_limit is not None and requested_limit > _API_MAX_LIMIT:
            sdk_limit = None

        if proj is not None:
            it = c.list_runs(project_name=proj, limit=sdk_limit, **kw)
        else:
            it = c.list_runs(limit=sdk_limit, **kw)

        if requested_limit is not None and requested_limit > _API_MAX_LIMIT:
            return list(islice(it, requested_limit))
        return it

    return _fetch_runs


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
    "--before",
    help="Show runs before time (ISO format, '3d', or '3 days ago'). Upper bound for time window.",
)
@click.option(
    "--last",
    help="Show runs from last duration (e.g., '24h', '7d', '30m', '2w').",
)
@click.option(
    "--query",
    help="Server-side full-text search in inputs/outputs (fast, but searches only first ~250 chars). Use --grep for unlimited content search.",
)
@add_grep_options
@click.option(
    "--fetch",
    type=int,
    help="Number of runs to fetch when using client-side filters (--grep, --name-pattern, etc.). Overrides automatic 3x multiplier. Example: --limit 10 --fetch 500 fetches 500 runs and returns up to 10 matches.",
)
@add_metadata_filter_options
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
    project_id,
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
    before,
    last,
    query,
    grep,
    grep_ignore_case,
    grep_regex,
    grep_in,
    fetch,
    metadata_filters,
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

    # Resolve project filters (--project-id bypasses name resolution)
    pq = resolve_project_filters(
        client,
        project=project,
        project_id=project_id,
        name=project_name,
        name_exact=project_name_exact,
        name_pattern=project_name_pattern,
        name_regex=project_name_regex,
    )
    projects_to_query = pq.names

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
        fql_filters.extend(build_tag_fql_filters(tag))

    # Metadata filtering (server-side, fast)
    if metadata_filters:
        fql_filters.extend(build_metadata_fql_filters(metadata_filters))

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
    time_filters = build_time_fql_filters(since=since, last=last, before=before)
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
        _make_fetch_runs(),
        project_query=pq,
        limit=api_limit,
        query=query,
        error=error_filter,
        filter=combined_filter,
        trace_id=trace_id,
        run_type=run_type,
        is_root=is_root,
        trace_filter=trace_filter,
        tree_filter=tree_filter,
        reference_example_id=reference_example_id,
        console=None,  # Don't auto-report warnings (we have custom diagnostics below)
    )
    all_runs = result.items
    failed_projects = result.failed_sources

    # CRITICAL: Fail fast if ALL sources failed (prevents silent failures)
    # The global error handler in LangSmithCLIGroup outputs JSON errors in --json mode,
    # so we don't need to output [] here (which would cause double output on stdout).
    # Uses raise_if_all_failed_with_suggestions to suggest similar project names.
    raise_if_all_failed_with_suggestions(result, client, pq, logger, "runs")

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
@output_option()
@click.pass_context
def get_run(ctx, run_id, fields, output):
    """Fetch details of a single run."""
    client = get_or_create_client(ctx)
    run = client.read_run(run_id)

    data = filter_fields(run, fields)
    output_single_item(ctx, data, console, output=output, render_fn=render_run_details)


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
@click.option(
    "--before",
    help="Show runs before time (ISO format, '3d', or '3 days ago'). Upper bound for time window.",
)
@click.option("--last", help="Show runs from last duration (e.g., '24h', '7d', '30m').")
@click.option("--filter", "filter_", help="Custom FQL filter string.")
@fields_option(
    "Comma-separated field names (e.g., 'id,name,inputs,outputs'). Reduces context."
)
@output_option()
@click.pass_context
def get_latest_run(
    ctx,
    project,
    project_id,
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
    before,
    last,
    filter_,
    fields,
    output,
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
    is_machine_readable = ctx.obj.get("json") or fields or output
    logger.use_stderr = is_machine_readable

    client = get_or_create_client(ctx)
    logger.debug(f"Getting latest run with filters: project={project}, status={status}")

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
    projects_to_query = pq.names

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
        before=before,
        last=last,
    )

    # Search projects in order until we find a run
    latest_run = None
    failed_projects = []
    run_kwargs: dict[str, Any] = dict(
        limit=1,
        error=error_filter,
        filter=combined_filter,
        is_root=roots,
    )

    if pq.use_id:
        # Direct project ID lookup - no iteration needed
        try:
            runs_iter = client.list_runs(project_id=pq.project_id, **run_kwargs)
            latest_run = next(runs_iter, None)
        except Exception as e:
            failed_projects.append((f"id:{pq.project_id}", str(e)))
    else:
        for proj_name in projects_to_query:
            try:
                runs_iter = client.list_runs(project_name=proj_name, **run_kwargs)
                latest_run = next(runs_iter, None)
                if latest_run:
                    break  # Found a run, stop searching
            except Exception as e:
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

            # Suggest similar project names for single-project failures
            failed_names = [
                name for name, _ in failed_projects if not name.startswith("id:")
            ]
            if len(failed_names) == 1:
                suggestions = get_project_suggestions(client, failed_names[0])
                if suggestions:
                    suggestion_list = ", ".join(f"'{s}'" for s in suggestions[:5])
                    logger.info(f"Did you mean: {suggestion_list}?")

        raise click.Abort()

    data = filter_fields(latest_run, fields)

    def render_latest(data: dict, console: Any) -> None:
        render_run_details(data, console, title="Latest Run")

    output_single_item(ctx, data, console, output=output, render_fn=render_latest)


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
    project_id,
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
    pq = resolve_project_filters(
        client,
        project=project,
        project_id=project_id,
        name=project_name,
        name_exact=project_name_exact,
        name_pattern=project_name_pattern,
        name_regex=project_name_regex,
    )

    # Resolve to project IDs for the stats API
    resolved_project_ids = []
    if pq.use_id:
        resolved_project_ids.append(pq.project_id)
    else:
        for proj_name in pq.names:
            try:
                p = client.read_project(project_name=proj_name)
                resolved_project_ids.append(p.id)
            except Exception:
                # Fallback: use project name as ID (user might have passed ID directly)
                resolved_project_ids.append(proj_name)

    if not resolved_project_ids:
        if ctx.obj.get("json"):
            click.echo(
                json_dumps(
                    {"error": "NotFoundError", "message": "No matching projects found."}
                )
            )
        else:
            console.print("[yellow]No matching projects found.[/yellow]")
        return

    stats = client.get_run_stats(project_ids=resolved_project_ids)

    if ctx.obj.get("json"):
        click.echo(json_dumps(stats))
        return

    # Build descriptive title
    if pq.use_id:
        table_title = f"Stats: id:{pq.project_id}"
    elif len(pq.names) == 1:
        table_title = f"Stats: {pq.names[0]}"
    else:
        table_title = f"Stats: {len(pq.names)} projects"

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

    if ctx.obj.get("json"):
        click.echo(json_dumps({"run_id": run_id, "url": url}))
    else:
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
    project_id,
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
        pq = resolve_project_filters(
            client,
            project=project,
            project_id=project_id,
            name=project_name,
            name_exact=project_name_exact,
            name_pattern=project_name_pattern,
            name_regex=project_name_regex,
        )
        # Build descriptive title based on filter used
        if pq.use_id:
            title = f"Watching: id:{pq.project_id}"
        elif project_name_exact:
            title = f"Watching: {project_name_exact}"
        elif project_name_regex:
            title = f"Watching: regex({project_name_regex}) ({len(pq.names)} projects)"
        elif project_name_pattern:
            title = f"Watching: {project_name_pattern} ({len(pq.names)} projects)"
        elif project_name:
            title = f"Watching: *{project_name}* ({len(pq.names)} projects)"
        elif len(pq.names) > 1:
            title = f"Watching: {len(pq.names)} projects"
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
        if pq.use_id:
            try:
                runs = list(
                    client.list_runs(
                        project_id=pq.project_id,
                        limit=10,
                        is_root=True,
                    )
                )
                label = f"id:{pq.project_id}"
                all_runs.extend((label, run) for run in runs)
            except Exception:
                failed_count += 1
        else:
            for proj_name in pq.names:
                try:
                    runs = list(
                        client.list_runs(
                            project_name=proj_name,
                            limit=5 if project_name_pattern else 10,
                            is_root=True,
                        )
                    )
                    all_runs.extend((proj_name, run) for run in runs)
                except Exception:
                    failed_count += 1

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
    project_id,
    project_name,
    project_name_exact,
    project_name_pattern,
    project_name_regex,
    since,
    before,
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
        project_id=project_id,
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
        before=before,  # Pass through time filters
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
    project_id,
    project_name,
    project_name_exact,
    project_name_pattern,
    project_name_regex,
    since,
    before,
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
    time_filters = build_time_fql_filters(since=since, last=last, before=before)
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
    pq = resolve_project_filters(
        client,
        project=project,
        project_id=project_id,
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
                pq.names,
                _make_fetch_runs(),
                project_query=pq,
                limit=sample_limit,
                filter=combined_filter,
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
                pq.names,
                _make_fetch_runs(),
                project_query=pq,
                limit=samples_per_stratum,
                filter=combined_filter,
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
    before: str | None,
    last: str | None,
    sample_size: int,
    select: list[str] | None = None,
    cmd_name: str = "discovery",
    project_id: str | None = None,
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
    time_filters = build_time_fql_filters(since=since, last=last, before=before)
    combined_filter = combine_fql_filters(time_filters)

    # Resolve project filters (--project-id bypasses name resolution)
    pq = resolve_project_filters(
        client,
        project=project,
        project_id=project_id,
        name=project_name,
        name_exact=project_name_exact,
        name_pattern=project_name_pattern,
        name_regex=project_name_regex,
    )

    # Fetch runs
    logger.debug(f"Fetching {sample_size} runs for {cmd_name}...")

    result = fetch_from_projects(
        client,
        pq.names,
        _make_fetch_runs(),
        project_query=pq,
        limit=sample_size,
        select=select,
        filter=combined_filter,
        console=console,
    )

    return DiscoveryContext(
        runs=result.items,
        projects=pq.names if not pq.use_id else [f"id:{pq.project_id}"],
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
    project_id,
    project_name,
    project_name_exact,
    project_name_pattern,
    project_name_regex,
    since,
    before,
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
        project_id=project_id,
        project_name=project_name,
        project_name_exact=project_name_exact,
        project_name_pattern=project_name_pattern,
        project_name_regex=project_name_regex,
        since=since,
        before=before,
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
    project_id,
    project_name,
    project_name_exact,
    project_name_pattern,
    project_name_regex,
    since,
    before,
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
        project_id=project_id,
        project_name=project_name,
        project_name_exact=project_name_exact,
        project_name_pattern=project_name_pattern,
        project_name_regex=project_name_regex,
        since=since,
        before=before,
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
    before: str | None,
    last: str | None,
    sample_size: int,
    include: str | None,
    exclude: str | None,
    no_language: bool,
    show_detailed_stats: bool,
    project_id: str | None = None,
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
        project_id: Optional project UUID (bypasses name resolution)
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
        project_id=project_id,
        project_name=project_name,
        project_name_exact=project_name_exact,
        project_name_pattern=project_name_pattern,
        project_name_regex=project_name_regex,
        since=since,
        before=before,
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
        table.add_column("Field Path", style="cyan")
        table.add_column("Type", style="dim")
        table.add_column("Present", justify="right")
        table.add_column("Length/Numeric Stats")
        table.add_column("Languages")
        for stats in stats_list:
            table.add_row(*render_describe_row(stats))
    else:
        table = Table(title=f"Fields ({len(discovery.runs)} runs analyzed)")
        table.add_column("Field Path", style="cyan")
        table.add_column("Type", style="dim")
        table.add_column("Present", justify="right")
        table.add_column("Languages")
        table.add_column("Sample", max_width=40, overflow="fold")
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
    project_id,
    project_name,
    project_name_exact,
    project_name_pattern,
    project_name_regex,
    since,
    before,
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
        project_id=project_id,
        project_name=project_name,
        project_name_exact=project_name_exact,
        project_name_pattern=project_name_pattern,
        project_name_regex=project_name_regex,
        since=since,
        before=before,
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
    project_id,
    project_name,
    project_name_exact,
    project_name_pattern,
    project_name_regex,
    since,
    before,
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
        project_id=project_id,
        project_name=project_name,
        project_name_exact=project_name_exact,
        project_name_pattern=project_name_pattern,
        project_name_regex=project_name_regex,
        since=since,
        before=before,
        last=last,
        sample_size=sample_size,
        include=include,
        exclude=exclude,
        no_language=no_language,
        show_detailed_stats=True,
    )


def _get_model_name(run: Run) -> str:
    """Extract model name from a run, checking multiple locations."""
    extra = run.extra or {}
    metadata = extra.get("metadata", {}) or {}
    model = metadata.get("ls_model_name")
    if model:
        return str(model)
    invocation = extra.get("invocation_params", {}) or {}
    model = invocation.get("model") or invocation.get("model_name")
    if model:
        return str(model)
    return "unknown"


def _get_project_name(run: Run) -> str:
    """Extract project name from a run, handling missing attribute when using select."""
    try:
        name = run.session_name
        if name:
            return name
    except AttributeError:
        pass
    return "unknown"


def _extract_input_context(run: Run) -> dict[str, str]:
    """Extract structured context from run inputs.

    Looks for known patterns like channel_info JSON embedded in inputs,
    and returns a flat dict of extracted key-value pairs.
    """
    import json as json_mod

    result: dict[str, str] = {}
    inputs = run.inputs or {}

    # Look for channel_info (common pattern: JSON string in inputs)
    channel_info = inputs.get("channel_info", "")
    if isinstance(channel_info, str) and channel_info.strip().startswith("{"):
        try:
            parsed = json_mod.loads(channel_info)
            if isinstance(parsed, dict):
                for key in ("community_name", "channel_id", "channel_name"):
                    val = parsed.get(key)
                    if val:
                        result[key] = str(val)
        except (json_mod.JSONDecodeError, ValueError):
            pass
    elif isinstance(channel_info, dict):
        for key in ("community_name", "channel_id", "channel_name"):
            val = channel_info.get(key)
            if val:
                result[key] = str(val)

    return result


def _metadata_value_matches(candidate: str | None, pattern: str) -> bool:
    """Check if a candidate value matches a metadata filter pattern.

    Supports three matching modes:
    - Exact match: ``channel_id=room-A``
    - Wildcard match: ``channel_id=room-*`` (``*`` and ``?`` supported)
    - Regex match: ``channel_id=/^room-[A-Z]+$/`` (slash-delimited)

    Args:
        candidate: The value to test (from metadata, tag, or trace context)
        pattern: The filter pattern string

    Returns:
        True if the candidate matches the pattern
    """
    if candidate is None:
        return False

    # Regex mode: /pattern/
    if pattern.startswith("/") and pattern.endswith("/") and len(pattern) > 2:
        import re

        try:
            return bool(re.search(pattern[1:-1], candidate))
        except re.error:
            return candidate == pattern

    # Wildcard mode: contains * or ?
    if "*" in pattern or "?" in pattern:
        import re

        regex = pattern.replace("*", ".*").replace("?", ".")
        if not pattern.startswith("*"):
            regex = "^" + regex
        if not pattern.endswith("*"):
            regex = regex + "$"
        return bool(re.match(regex, candidate))

    # Exact match (default)
    return candidate == pattern


def _truncate_hour(dt: Any) -> str:
    """Truncate a datetime to the hour, return as ISO string."""
    from datetime import datetime, timezone

    if isinstance(dt, str):
        dt = datetime.fromisoformat(dt)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:00Z")


@runs.command("usage")
@add_project_filter_options
@add_time_filter_options
@click.option(
    "--group-by",
    help="Group by a metadata or tag field (e.g., 'metadata:channel_id', 'tag:env'). "
    "Shows per-group breakdown.",
)
@click.option(
    "--breakdown",
    multiple=True,
    type=click.Choice(["model", "project"]),
    help="Add breakdown dimensions (can specify multiple: --breakdown model --breakdown project).",
)
@click.option(
    "--interval",
    default="hour",
    type=click.Choice(["hour", "day"]),
    help="Time bucket interval (default: hour).",
)
@click.option(
    "--active-only",
    is_flag=True,
    help="Only show time buckets with non-zero token usage.",
)
@click.option(
    "--sample-size",
    default=0,
    type=int,
    help="Number of runs to analyze (default: 0 = all runs in time range).",
)
@click.option(
    "--tag",
    multiple=True,
    help="Filter by tag (can specify multiple times for AND logic).",
)
@add_grep_options
@add_metadata_filter_options
@click.option(
    "--filter",
    "additional_filter",
    help="Additional FQL filter (e.g., 'eq(run_type, \"llm\")').",
)
@click.option(
    "--from-cache",
    is_flag=True,
    help="Read runs from local cache instead of API. Use 'runs cache download' first.",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["table", "json", "csv", "yaml"]),
    help="Output format (default: table, or json if --json flag used).",
)
@click.pass_context
def usage_runs(
    ctx: click.Context,
    project: str | None,
    project_id: str | None,
    project_name: str | None,
    project_name_exact: str | None,
    project_name_pattern: str | None,
    project_name_regex: str | None,
    since: str | None,
    before: str | None,
    last: str | None,
    group_by: str | None,
    breakdown: tuple[str, ...],
    interval: str,
    active_only: bool,
    sample_size: int,
    tag: tuple[str, ...],
    grep: str | None,
    grep_ignore_case: bool,
    grep_regex: bool,
    grep_in: str | None,
    metadata_filters: tuple[str, ...],
    additional_filter: str | None,
    from_cache: bool,
    output_format: str | None,
) -> None:
    """Analyze token usage over time with flexible grouping and breakdowns.

    Fetches LLM runs and aggregates token usage into time buckets (hour/day),
    with optional grouping by metadata fields and breakdowns by model/project.

    Only counts run_type="llm" runs with ls_model_name set to avoid
    double-counting tokens from parent chain runs.

    Examples:
        # Token usage per hour across all prd/* projects
        langsmith-cli runs usage --project-name-pattern "prd/*" --last 7d

        # Per channel_id breakdown with model detail
        langsmith-cli runs usage \\
          --project-name-pattern "prd/*" \\
          --group-by metadata:channel_id \\
          --breakdown model \\
          --last 7d --active-only

        # Session analysis: filter by specific channel_id
        langsmith-cli runs usage \\
          --project-name-pattern "prd/*" \\
          --metadata channel_id=chat:MyRoom-abc123 \\
          --breakdown model --breakdown project

        # From cache (fast, offline)
        langsmith-cli runs usage \\
          --project-name-pattern "prd/*" \\
          --from-cache --group-by metadata:channel_id \\
          --breakdown model --active-only

        # JSON output for further processing
        langsmith-cli --json runs usage \\
          --project-name-pattern "prd/*" \\
          --group-by metadata:channel_id \\
          --breakdown model \\
          --last 7d --active-only
    """
    from collections import defaultdict
    from datetime import timezone

    logger = ctx.obj["logger"]
    is_machine_readable = ctx.obj.get("json") or output_format in ["csv", "yaml"]
    logger.use_stderr = is_machine_readable

    # Build filters: only LLM runs (to avoid double-counting from chains)
    time_filters = build_time_fql_filters(since=since, last=last, before=before)
    base_filters = time_filters.copy()
    base_filters.append('eq(run_type, "llm")')

    # Tag filtering (AND logic - all tags must be present)
    if tag:
        base_filters.extend(build_tag_fql_filters(tag))

    # Add metadata filters (server-side, fast)
    if metadata_filters:
        base_filters.extend(build_metadata_fql_filters(metadata_filters))

    if additional_filter:
        base_filters.append(additional_filter)
    combined_filter = combine_fql_filters(base_filters)

    # Fetch runs - either from cache or API
    all_runs: list[Run] = []
    run_project_map: dict[str, str] = {}  # run.id -> project_name
    trace_context: dict[str, dict[str, str]] = {}  # trace_id -> {field: value}

    if from_cache:
        from langsmith_cli.cache import load_runs_from_cache

        # Resolve project names (need client for pattern matching)
        client = get_or_create_client(ctx)
        pq = resolve_project_filters(
            client,
            project=project,
            project_id=project_id,
            name=project_name,
            name_exact=project_name_exact,
            name_pattern=project_name_pattern,
            name_regex=project_name_regex,
        )
        project_names = pq.names if not pq.use_id else [f"id:{pq.project_id}"]

        # Parse time filters for client-side filtering
        from langsmith_cli.filters import parse_time_filter

        since_dt, until_dt = parse_time_filter(since=since, last=last, before=before)

        logger.info(f"Loading from cache: {len(project_names)} project(s)...")
        result = load_runs_from_cache(project_names, since=since_dt, until=until_dt)
        if result.has_failures:
            for src, err in result.failed_sources[:3]:
                logger.warning(f"  {src}: {err}")

        # Build trace context map from all runs (for group-by/metadata propagation)
        # When LLM runs lack a metadata field, we can look it up from root/chain runs
        if group_by or metadata_filters:
            for run in result.items:
                tid = str(run.trace_id) if run.trace_id else None
                if not tid:
                    continue
                # Extract context from metadata
                meta = {}
                if run.extra and isinstance(run.extra, dict):
                    meta = run.extra.get("metadata", {}) or {}
                if run.metadata and isinstance(run.metadata, dict):
                    meta.update(run.metadata)
                # Extract context from inputs (e.g. channel_info JSON)
                input_ctx = _extract_input_context(run)
                # Merge (prefer root/chain data = runs with no parent)
                if tid not in trace_context:
                    trace_context[tid] = {}
                # Root runs (no parent) get priority
                is_root = run.parent_run_id is None
                for k, v in {**meta, **input_ctx}.items():
                    if v and (is_root or k not in trace_context[tid]):
                        trace_context[tid][k] = str(v)

        # Client-side filter: only LLM runs
        for run in result.items:
            if run.run_type != "llm":
                continue
            all_runs.append(run)
            # Use item_source_map from cache loader for accurate project attribution
            run_id = str(run.id)
            if run_id in result.item_source_map:
                run_project_map[run_id] = result.item_source_map[run_id]

        # Apply tag filters client-side
        if tag:
            all_runs = filter_runs_by_tags(all_runs, tag)

        # Apply metadata filters client-side (check metadata, tags, and trace context)
        # Supports exact match, wildcards (*/?), and regex (/pattern/)
        for mf in metadata_filters:
            if "=" not in mf:
                continue
            key, value = mf.split("=", 1)
            filtered: list[Run] = []
            for r in all_runs:
                # Check metadata
                direct = extract_group_value(r, "metadata", key)
                if _metadata_value_matches(direct, value):
                    filtered.append(r)
                    continue
                # Check tags: value may appear as a tag directly ("chat:Foo")
                # or as "key:value" ("channel_id:chat:Foo")
                if r.tags:
                    for tag in r.tags:
                        if _metadata_value_matches(
                            tag, value
                        ) or _metadata_value_matches(tag, f"{key}:{value}"):
                            filtered.append(r)
                            break
                    else:
                        # Fallback to trace context
                        tid = str(r.trace_id) if r.trace_id else None
                        if tid and _metadata_value_matches(
                            trace_context.get(tid, {}).get(key), value
                        ):
                            filtered.append(r)
                else:
                    # No tags — check trace context
                    tid = str(r.trace_id) if r.trace_id else None
                    if tid and _metadata_value_matches(
                        trace_context.get(tid, {}).get(key), value
                    ):
                        filtered.append(r)
            all_runs = filtered

        if not all_runs and not result.successful_sources:
            raise click.ClickException(
                "No cached data found. Run 'runs cache download' first."
            )
    else:
        client = get_or_create_client(ctx)

        pq = resolve_project_filters(
            client,
            project=project,
            project_id=project_id,
            name=project_name,
            name_exact=project_name_exact,
            name_pattern=project_name_pattern,
            name_regex=project_name_regex,
        )

        logger.info(f"Fetching LLM runs from {len(pq.names)} project(s)...")

        select_fields = [
            "start_time",
            "total_tokens",
            "prompt_tokens",
            "completion_tokens",
            "total_cost",
            "extra",
            "run_type",
        ]
        # Grep needs content fields to search
        if grep:
            select_fields.extend(["inputs", "outputs", "error"])

        failed_projects: list[tuple[str, str]] = []
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
                    limit=None,
                    select=select_fields,
                )
                collected = 0
                for run in runs_iter:
                    all_runs.append(run)
                    run_project_map[str(run.id)] = source_label
                    collected += 1
                    if sample_size > 0 and collected >= sample_size:
                        break
            except Exception as e:
                failed_projects.append((source_label, str(e)))

        if failed_projects and len(all_runs) == 0:
            logger.warning("All projects failed to fetch:")
            for proj, error_msg in failed_projects[:3]:
                short = error_msg[:100] + "..." if len(error_msg) > 100 else error_msg
                logger.warning(f"  {proj}: {short}")
            raise click.ClickException(
                "No runs fetched. Check project names and API key."
            )

    # Apply grep filter (client-side content search)
    if grep:
        grep_fields_tuple: tuple[str, ...] = ()
        if grep_in:
            grep_fields_tuple = tuple(
                f.strip() for f in grep_in.split(",") if f.strip()
            )
        all_runs = apply_grep_filter(
            all_runs,
            grep_pattern=grep,
            grep_fields=grep_fields_tuple,
            ignore_case=grep_ignore_case,
            use_regex=grep_regex,
        )

    # Filter to only runs with a model name (avoids counting non-LLM chain wrappers)
    model_runs = [r for r in all_runs if _get_model_name(r) != "unknown"]
    source_label_str = "cache" if from_cache else "API"
    logger.info(
        f"Loaded {len(all_runs)} LLM runs ({len(model_runs)} with model info) "
        f"from {source_label_str}"
    )

    if not model_runs:
        logger.warning("No LLM runs with model info found in the selected time range.")
        return

    # Parse group-by if provided
    group_type: str | None = None
    group_field: str | None = None
    if group_by:
        parsed = parse_grouping_field(group_by)
        if isinstance(parsed, list):
            raise click.BadParameter(
                "Multi-dimensional grouping not supported for usage. Use a single dimension."
            )
        group_type, group_field = parsed

    # Build bucket key function
    def _bucket_key(run: Run) -> str:
        if interval == "day":
            dt = run.start_time
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.strftime("%Y-%m-%d")
        return _truncate_hour(run.start_time)

    # Aggregate into buckets
    # Key: (time_bucket, group_value, *breakdown_values) -> metrics
    UsageBucket = dict[str, float | int | str]
    buckets: dict[tuple[str, ...], UsageBucket] = defaultdict(
        lambda: {
            "total_tokens": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_cost": 0.0,
            "run_count": 0,
        }
    )

    for run in model_runs:
        time_key = _bucket_key(run)

        # Group value (with trace context fallback for cached runs)
        group_val = "all"
        if group_type and group_field:
            extracted = extract_group_value(run, group_type, group_field)
            if not extracted and from_cache:
                # Fallback: look up from trace context (root/chain runs)
                tid = str(run.trace_id) if run.trace_id else None
                if tid and tid in trace_context:
                    extracted = trace_context[tid].get(group_field)
            group_val = extracted or "ungrouped"

        # Breakdown values
        breakdown_vals: list[str] = []
        for dim in breakdown:
            if dim == "model":
                breakdown_vals.append(_get_model_name(run))
            elif dim == "project":
                breakdown_vals.append(
                    run_project_map.get(str(run.id), _get_project_name(run))
                )

        key = (time_key, group_val, *breakdown_vals)

        bucket = buckets[key]
        bucket["total_tokens"] = int(bucket["total_tokens"]) + (run.total_tokens or 0)
        bucket["prompt_tokens"] = int(bucket["prompt_tokens"]) + (
            run.prompt_tokens or 0
        )
        bucket["completion_tokens"] = int(bucket["completion_tokens"]) + (
            run.completion_tokens or 0
        )
        bucket["total_cost"] = float(bucket["total_cost"]) + float(
            run.total_cost or 0.0
        )
        bucket["run_count"] = int(bucket["run_count"]) + 1

    # Build results list
    results: list[dict[str, Any]] = []
    for key, metrics in buckets.items():
        row: dict[str, Any] = {
            "time": key[0],
            "group": key[1],
        }
        # Add breakdown columns
        for i, dim in enumerate(breakdown):
            row[dim] = key[2 + i]

        row.update(metrics)
        results.append(row)

    # Sort by time, then group
    results.sort(key=lambda r: (r["time"], r["group"]))

    # Filter active-only
    if active_only:
        results = [r for r in results if r["total_tokens"] > 0]

    if not results:
        logger.warning("No usage data found for the selected filters.")
        return

    # Compute summary stats
    unique_groups = {r["group"] for r in results}
    unique_times = {r["time"] for r in results}
    total_tokens_all = sum(r["total_tokens"] for r in results)
    total_cost_all = sum(r["total_cost"] for r in results)

    # Concurrent groups per time bucket
    groups_per_bucket: dict[str, set[str]] = defaultdict(set)
    for r in results:
        groups_per_bucket[r["time"]].add(r["group"])
    max_concurrent = (
        max(len(v) for v in groups_per_bucket.values()) if groups_per_bucket else 0
    )
    avg_concurrent = (
        sum(len(v) for v in groups_per_bucket.values()) / len(groups_per_bucket)
        if groups_per_bucket
        else 0
    )

    # Determine output format
    format_type = determine_output_format(output_format, ctx.obj.get("json"))

    if format_type != "table":
        output_data: dict[str, Any] | list[dict[str, Any]] = {
            "summary": {
                "total_tokens": total_tokens_all,
                "total_cost": round(total_cost_all, 6),
                "active_buckets": len(unique_times),
                "unique_groups": len(unique_groups),
                "max_concurrent_groups": max_concurrent,
                "avg_concurrent_groups": round(avg_concurrent, 1),
                "interval": interval,
                "run_count": sum(r["run_count"] for r in results),
            },
            "buckets": results,
        }
        # CSV/YAML need a flat list; JSON gets the full structure
        if format_type in ("csv", "yaml"):
            output_data = results
        output_formatted_data(output_data, format_type)
        return

    # Print summary
    group_label = group_field or "group"
    console.print("\n[bold]Token Usage Summary[/bold]")
    console.print(f"  Total tokens: [cyan]{total_tokens_all:,}[/cyan]")
    console.print(f"  Total cost: [cyan]${total_cost_all:.4f}[/cyan]")
    console.print(f"  Active {interval}s: [cyan]{len(unique_times)}[/cyan]")
    if group_by:
        console.print(f"  Unique {group_label}s: [cyan]{len(unique_groups)}[/cyan]")
        console.print(f"  Max concurrent {group_label}s: [cyan]{max_concurrent}[/cyan]")
        console.print(
            f"  Avg concurrent {group_label}s: [cyan]{avg_concurrent:.1f}[/cyan]"
        )
    console.print()

    # Build table
    table = Table(title=f"Token Usage by {interval.title()}")
    table.add_column("Time", style="cyan")
    if group_by:
        table.add_column(group_label.title(), style="green")
    for dim in breakdown:
        table.add_column(dim.title(), style="yellow")
    table.add_column("Runs", justify="right")
    table.add_column("Total Tokens", justify="right", style="bold")
    table.add_column("Prompt", justify="right")
    table.add_column("Completion", justify="right")
    table.add_column("Cost", justify="right")

    for r in results:
        row_values = [r["time"]]
        if group_by:
            row_values.append(str(r["group"]))
        for dim in breakdown:
            row_values.append(str(r.get(dim, "")))
        row_values.extend(
            [
                str(r["run_count"]),
                f"{r['total_tokens']:,}",
                f"{r['prompt_tokens']:,}",
                f"{r['completion_tokens']:,}",
                f"${r['total_cost']:.4f}",
            ]
        )
        table.add_row(*row_values)

    console.print(table)


# Cache commands


@runs.group("cache")
def cache_group():
    """Manage local JSONL cache of runs for fast offline analysis."""
    pass


@cache_group.command("download")
@add_project_filter_options
@add_time_filter_options
@click.option(
    "--filter",
    "additional_filter",
    help="Additional FQL filter to apply.",
)
@click.option(
    "--run-type",
    help="Filter by run type (llm, chain, tool, etc).",
)
@click.option(
    "--full",
    is_flag=True,
    help="Re-fetch all runs in the time range (ignores incremental state, deduplicates safely).",
)
@click.option(
    "--workers",
    type=int,
    default=None,
    help="Number of parallel workers (default: min(4, num_projects)).",
)
@click.pass_context
def cache_download(
    ctx: click.Context,
    project: str | None,
    project_id: str | None,
    project_name: str | None,
    project_name_exact: str | None,
    project_name_pattern: str | None,
    project_name_regex: str | None,
    since: str | None,
    before: str | None,
    last: str | None,
    additional_filter: str | None,
    run_type: str | None,
    full: bool,
    workers: int | None,
) -> None:
    """Download runs to local JSONL cache for fast offline analysis.

    By default, incrementally fetches only new runs since last download.
    Uses parallel workers to fetch multiple projects simultaneously.
    Use --full to re-download everything.

    Examples:
        # Cache all runs from prd/* for last 7 days
        langsmith-cli runs cache download --project-name-pattern "prd/*" --last 7d

        # Incremental update (only new runs since last download)
        langsmith-cli runs cache download --project-name-pattern "prd/*"

        # Full re-download with 4 workers
        langsmith-cli runs cache download --project prd/video_moderation_service --full --workers 4

        # Cache only LLM runs
        langsmith-cli runs cache download --project-name-pattern "prd/*" --run-type llm
    """
    import time
    from concurrent.futures import ThreadPoolExecutor, as_completed

    from langsmith_cli.cache import (
        append_runs_streaming,
        get_cache_path,
        get_existing_run_ids,
        read_cache_metadata,
    )

    logger = ctx.obj["logger"]
    is_json = ctx.obj.get("json", False)
    logger.use_stderr = is_json

    client = get_or_create_client(ctx)

    # Resolve projects
    pq = resolve_project_filters(
        client,
        project=project,
        project_id=project_id,
        name=project_name,
        name_exact=project_name_exact,
        name_pattern=project_name_pattern,
        name_regex=project_name_regex,
    )

    project_names = pq.names if not pq.use_id else [f"id:{pq.project_id}"]
    num_projects = len(project_names)
    num_workers = workers if workers else min(4, num_projects)

    # Build filters
    time_filters = build_time_fql_filters(since=since, last=last, before=before)
    base_filters = time_filters.copy()
    if additional_filter:
        base_filters.append(additional_filter)
    if run_type:
        base_filters.append(f'eq(run_type, "{run_type}")')

    # Results tracking
    results: list[dict[str, Any]] = []
    overall_start = time.monotonic()

    def download_project(proj_name: str) -> dict[str, Any]:
        """Download runs for a single project. Runs in thread pool."""
        proj_start = time.monotonic()
        result: dict[str, Any] = {
            "project": proj_name,
            "status": "success",
            "new_runs": 0,
            "total_runs": 0,
            "size_mb": 0.0,
            "mode": "full",
            "elapsed_s": 0.0,
        }

        try:
            # Check for incremental update (--full skips incremental filter
            # but keeps existing cache data; dedup via existing_ids prevents
            # duplicates — this is safe even if the download fails mid-way)
            existing_meta = read_cache_metadata(proj_name)
            incremental_filters = base_filters.copy()
            if existing_meta and existing_meta.newest_run_start_time and not full:
                newest = existing_meta.newest_run_start_time.isoformat()
                incremental_filters.append(f'gt(start_time, "{newest}")')
                result["mode"] = "incremental"
                result["incremental_from"] = (
                    existing_meta.newest_run_start_time.isoformat()
                )

            combined_filter = combine_fql_filters(incremental_filters)

            # Pre-load existing IDs for dedup
            existing_ids = get_existing_run_ids(proj_name)

            # Build SDK kwargs
            proj_kwargs: dict[str, Any] = {}
            if proj_name.startswith("id:"):
                proj_kwargs["project_id"] = proj_name[3:]
            else:
                proj_kwargs["project_name"] = proj_name

            # Stream runs from SDK into cache with progress callback
            def on_batch(cumulative_count: int) -> None:
                result["new_runs"] = cumulative_count
                if progress_task_ids and proj_name in progress_task_ids:
                    progress.update(
                        progress_task_ids[proj_name],
                        completed=cumulative_count,
                    )

            runs_iter = client.list_runs(
                **proj_kwargs,
                filter=combined_filter,
                limit=None,
            )

            meta, new_count = append_runs_streaming(
                proj_name,
                runs_iter,
                existing_ids=existing_ids,
                on_progress=on_batch,
            )

            result["new_runs"] = new_count
            result["total_runs"] = meta.run_count
            cache_path = get_cache_path(proj_name)
            if cache_path.exists():
                result["size_mb"] = round(cache_path.stat().st_size / (1024 * 1024), 2)

            if new_count == 0:
                result["status"] = "no_new_runs"

        except Exception as e:
            result["status"] = "error"
            result["error"] = str(e)[:200]

        result["elapsed_s"] = round(time.monotonic() - proj_start, 1)
        return result

    # Choose progress display based on mode
    progress_task_ids: dict[str, Any] = {}

    if is_json:
        # Agent mode: emit structured progress to stderr
        import json as json_mod
        import sys

        logger.info(
            json_mod.dumps(
                {
                    "event": "download_start",
                    "projects": num_projects,
                    "workers": num_workers,
                }
            )
        )

        # No Rich Progress in JSON mode - use simple callbacks
        with ThreadPoolExecutor(max_workers=num_workers) as executor:
            futures = {
                executor.submit(download_project, name): name for name in project_names
            }
            for future in as_completed(futures):
                r = future.result()
                results.append(r)
                # Emit per-project completion to stderr
                print(
                    json_mod.dumps({"event": "project_done", **r}),
                    file=sys.stderr,
                    flush=True,
                )

        elapsed = round(time.monotonic() - overall_start, 1)
        total_new = sum(r["new_runs"] for r in results)
        total_cached = sum(r["total_runs"] for r in results)
        errors = [r for r in results if r["status"] == "error"]

        summary = {
            "event": "download_complete",
            "projects": num_projects,
            "total_new_runs": total_new,
            "total_cached_runs": total_cached,
            "errors": len(errors),
            "elapsed_s": elapsed,
            "results": results,
        }
        click.echo(json_mod.dumps(summary, default=str))

    else:
        # Human mode: Rich live progress
        from rich.progress import (
            BarColumn,
            Progress,
            SpinnerColumn,
            TextColumn,
            TimeElapsedColumn,
        )

        progress = Progress(
            SpinnerColumn(),
            TextColumn("[bold]{task.description}"),
            BarColumn(bar_width=30),
            TextColumn("{task.completed} runs"),
            TimeElapsedColumn(),
            console=logger.diagnostic_console,
        )

        overall_task = progress.add_task(
            f"[cyan]Downloading from {num_projects} project(s) ({num_workers} workers)",
            total=None,
        )

        # Create per-project tasks
        for name in project_names:
            task_id = progress.add_task(f"  {name}", total=None)
            progress_task_ids[name] = task_id

        with progress:
            with ThreadPoolExecutor(max_workers=num_workers) as executor:
                futures = {
                    executor.submit(download_project, name): name
                    for name in project_names
                }
                for future in as_completed(futures):
                    r = future.result()
                    results.append(r)
                    name = r["project"]
                    task_id = progress_task_ids[name]

                    if r["status"] == "error":
                        progress.update(
                            task_id,
                            description=f"  [red]{name}: FAILED[/red]",
                        )
                    elif r["new_runs"] == 0:
                        progress.update(
                            task_id,
                            description=f"  [dim]{name}: up to date[/dim]",
                        )
                    else:
                        progress.update(
                            task_id,
                            description=(
                                f"  [green]{name}: "
                                f"{r['new_runs']} new runs "
                                f"(total: {r['total_runs']}, "
                                f"{r['size_mb']:.1f}MB)[/green]"
                            ),
                        )
                    progress.update(task_id, completed=r["new_runs"])

            progress.update(overall_task, completed=num_projects, total=num_projects)

        # Print summary
        elapsed = round(time.monotonic() - overall_start, 1)
        total_new = sum(r["new_runs"] for r in results)
        errors = [r for r in results if r["status"] == "error"]

        logger.success(
            f"Done: {total_new} new runs cached from "
            f"{num_projects} project(s) in {elapsed}s"
        )
        if errors:
            for e in errors:
                logger.warning(
                    f"  Failed: {e['project']} - {e.get('error', 'unknown')}"
                )


@cache_group.command("list")
@click.pass_context
def cache_list(ctx: click.Context) -> None:
    """Show cached projects and their stats.

    Examples:
        langsmith-cli runs cache list
        langsmith-cli --json runs cache list
    """
    from langsmith_cli.cache import get_cache_path, list_cached_projects

    logger = ctx.obj["logger"]

    projects = list_cached_projects()
    if not projects:
        logger.info("No cached projects. Use 'runs cache download' to cache runs.")
        return

    if ctx.obj.get("json"):
        data = [p.model_dump(mode="json") for p in projects]
        click.echo(json.dumps(data, default=str))
        return

    table = Table(title="Cached Projects")
    table.add_column("Project", style="cyan")
    table.add_column("Runs", justify="right")
    table.add_column("Time Range", style="green")
    table.add_column("Size", justify="right")
    table.add_column("Last Updated", style="yellow")

    for p in projects:
        cache_path = get_cache_path(p.project_name)
        size_mb = (
            cache_path.stat().st_size / (1024 * 1024) if cache_path.exists() else 0
        )

        time_range = ""
        if p.oldest_run_start_time and p.newest_run_start_time:
            oldest = p.oldest_run_start_time.strftime("%m-%d %H:%M")
            newest = p.newest_run_start_time.strftime("%m-%d %H:%M")
            time_range = f"{oldest} → {newest}"

        updated = p.last_updated.strftime("%Y-%m-%d %H:%M") if p.last_updated else ""

        table.add_row(
            p.project_name,
            str(p.run_count),
            time_range,
            f"{size_mb:.1f}MB",
            updated,
        )

    console.print(table)


@cache_group.command("clear")
@click.option("--project", help="Clear cache for a specific project only.")
@click.option("--yes", is_flag=True, help="Skip confirmation prompt.")
@click.pass_context
def cache_clear(ctx: click.Context, project: str | None, yes: bool) -> None:
    """Clear cached run data.

    Examples:
        # Clear specific project
        langsmith-cli runs cache clear --project prd/video_moderation_service

        # Clear all cached data
        langsmith-cli runs cache clear --yes
    """
    from langsmith_cli.cache import clear_cache as do_clear

    logger = ctx.obj["logger"]

    if not project and not yes:
        click.confirm("Clear ALL cached run data?", abort=True)

    deleted = do_clear(project)
    if deleted:
        target = project or "all projects"
        logger.success(f"Cleared cache for {target} ({deleted} files)")
    else:
        logger.info("No cache files to clear.")


@cache_group.command("grep")
@click.argument("pattern")
@click.option("--project", help="Search only a specific project's cache.")
@click.option(
    "--ignore-case",
    "-i",
    is_flag=True,
    help="Case-insensitive search.",
)
@click.option(
    "--regex",
    "-E",
    is_flag=True,
    help="Treat pattern as regex.",
)
@click.option(
    "--grep-in",
    help="Comma-separated fields to search in (e.g., 'inputs,outputs,error'). "
    "Searches all fields if not specified.",
)
@click.option("--limit", default=20, help="Max results to return (default 20).")
@add_time_filter_options
@fields_option()
@count_option()
@output_option()
@click.pass_context
def cache_grep(
    ctx: click.Context,
    pattern: str,
    project: str | None,
    ignore_case: bool,
    regex: bool,
    grep_in: str | None,
    limit: int,
    since: str | None,
    before: str | None,
    last: str | None,
    fields: str | None,
    count: bool,
    output: str | None,
) -> None:
    """Search cached runs for a text pattern in inputs/outputs/error.

    Examples:
        # Search all cached projects for "hello"
        langsmith-cli runs cache grep "hello"

        # Case-insensitive regex search in a specific project
        langsmith-cli runs cache grep -i -E "\\\\buser_id\\\\b" --project my-proj

        # Search only inputs field, output as JSON
        langsmith-cli --json runs cache grep "error" --grep-in inputs
    """
    from langsmith_cli.cache import list_cached_projects, load_runs_from_cache
    from langsmith_cli.filters import parse_time_filter

    logger = ctx.obj["logger"]
    is_machine_readable = ctx.obj.get("json") or bool(output) or bool(fields)
    logger.use_stderr = is_machine_readable

    # Determine which projects to search
    if project:
        project_names = [project]
    else:
        cached = list_cached_projects()
        project_names = [p.project_name for p in cached]

    if not project_names:
        logger.warning("No cached projects. Run 'runs cache download' first.")
        return

    # Parse time filters
    since_dt, until_dt = parse_time_filter(since=since, last=last, before=before)

    logger.info(f"Searching {len(project_names)} cached project(s) for '{pattern}'...")
    result = load_runs_from_cache(project_names, since=since_dt, until=until_dt)

    if not result.items:
        logger.warning("No cached runs found.")
        return

    # Parse grep-in fields
    grep_fields_tuple: tuple[str, ...] = ()
    if grep_in:
        grep_fields_tuple = tuple(f.strip() for f in grep_in.split(",") if f.strip())

    # Apply grep filter
    matched = apply_grep_filter(
        result.items,
        grep_pattern=pattern,
        grep_fields=grep_fields_tuple,
        ignore_case=ignore_case,
        use_regex=regex,
    )

    # Apply limit
    total_matched = len(matched)
    if limit and len(matched) > limit:
        matched = matched[:limit]

    # Handle file output
    if output:
        data = filter_fields(matched, fields)
        write_output_to_file(data, output, console, format_type="jsonl")
        return

    include_fields = parse_fields_option(fields)

    render_output(
        matched,
        lambda runs: build_runs_table(runs),
        ctx,
        include_fields=include_fields,
        empty_message=f"No runs matching '{pattern}' found",
        count_flag=count,
    )

    if not count and not ctx.obj.get("json") and total_matched > limit:
        logger.info(
            f"Showing {len(matched)} of {total_matched} matches. "
            f"Use --limit to see more."
        )


@runs.command("pricing")
@add_project_filter_options
@add_time_filter_options
@click.option(
    "--tag",
    multiple=True,
    help="Filter by tag (can specify multiple times for AND logic).",
)
@click.option(
    "--from-cache",
    is_flag=True,
    help="Analyze cached runs instead of fetching from API.",
)
@click.option(
    "--lookup/--no-lookup",
    default=True,
    help="Look up missing prices from OpenRouter API (default: enabled).",
)
@click.pass_context
def pricing_check(
    ctx: click.Context,
    project: str | None,
    project_id: str | None,
    project_name: str | None,
    project_name_exact: str | None,
    project_name_pattern: str | None,
    project_name_regex: str | None,
    since: str | None,
    before: str | None,
    last: str | None,
    tag: tuple[str, ...],
    from_cache: bool,
    lookup: bool,
) -> None:
    """Check model pricing coverage and look up missing prices.

    Scans runs to find models with and without cost data, then optionally
    looks up missing prices from the OpenRouter API.

    Models with $0.00 cost despite having tokens are flagged as missing pricing.
    The lookup provides input/output prices per million tokens that can be
    configured in LangSmith Settings > Model Pricing.

    Examples:
        # Check pricing for all prd/* projects from cache
        langsmith-cli runs pricing --project-name-pattern "prd/*" --from-cache

        # Check without OpenRouter lookup
        langsmith-cli runs pricing --project-name-pattern "prd/*" --from-cache --no-lookup

        # Check recent runs from API
        langsmith-cli runs pricing --project my-project --last 7d

        # JSON output for automation
        langsmith-cli --json runs pricing --project-name-pattern "prd/*" --from-cache
    """
    from collections import defaultdict

    logger = ctx.obj["logger"]
    is_json = ctx.obj.get("json")
    logger.use_stderr = bool(is_json)

    # Fetch runs
    all_runs: list[Run] = []
    if from_cache:
        from langsmith_cli.cache import load_runs_from_cache
        from langsmith_cli.filters import parse_time_filter

        client = get_or_create_client(ctx)
        pq = resolve_project_filters(
            client,
            project=project,
            project_id=project_id,
            name=project_name,
            name_exact=project_name_exact,
            name_pattern=project_name_pattern,
            name_regex=project_name_regex,
        )
        project_names = pq.names if not pq.use_id else [f"id:{pq.project_id}"]
        since_dt, until_dt = parse_time_filter(since=since, last=last, before=before)

        logger.info(f"Scanning cached runs from {len(project_names)} project(s)...")
        result = load_runs_from_cache(project_names, since=since_dt, until=until_dt)
        all_runs = [r for r in result.items if r.run_type == "llm"]
        # Apply tag filters client-side
        if tag:
            all_runs = filter_runs_by_tags(all_runs, tag)
    else:
        client = get_or_create_client(ctx)
        pq = resolve_project_filters(
            client,
            project=project,
            project_id=project_id,
            name=project_name,
            name_exact=project_name_exact,
            name_pattern=project_name_pattern,
            name_regex=project_name_regex,
        )

        time_filters = build_time_fql_filters(since=since, last=last, before=before)
        base_filters = time_filters.copy()
        base_filters.append('eq(run_type, "llm")')
        # Tag filtering (AND logic - all tags must be present)
        if tag:
            base_filters.extend(build_tag_fql_filters(tag))
        combined_filter = combine_fql_filters(base_filters)

        logger.info(f"Scanning LLM runs from {len(pq.names)} project(s)...")
        sources = (
            [(f"id:{pq.project_id}", {"project_id": pq.project_id})]
            if pq.use_id
            else [(name, {"project_name": name}) for name in pq.names]
        )
        for _, proj_kwargs in sources:
            try:
                for run in client.list_runs(
                    **proj_kwargs,
                    filter=combined_filter,
                    select=["total_tokens", "total_cost", "extra", "run_type"],
                    limit=None,
                ):
                    all_runs.append(run)
            except Exception:
                pass

    # Aggregate by model
    model_stats: dict[str, dict[str, int | float]] = defaultdict(
        lambda: {"runs": 0, "tokens": 0, "cost": 0.0}
    )
    for r in all_runs:
        model = _get_model_name(r)
        if model == "unknown":
            continue
        model_stats[model]["runs"] += 1
        model_stats[model]["tokens"] += r.total_tokens or 0
        model_stats[model]["cost"] += float(r.total_cost or 0.0)

    if not model_stats:
        logger.warning("No LLM runs with model info found.")
        return

    # Identify missing pricing (has tokens but no cost)
    missing_models = [
        name
        for name, stats in model_stats.items()
        if stats["tokens"] > 0 and stats["cost"] == 0.0
    ]

    # Look up pricing from OpenRouter
    openrouter_prices: dict[str, dict[str, float]] = {}
    if lookup and missing_models:
        openrouter_prices = _fetch_openrouter_pricing(missing_models, logger)

    # Output
    if is_json:
        models_data = []
        for name, stats in sorted(model_stats.items(), key=lambda x: -x[1]["tokens"]):
            entry: dict[str, Any] = {
                "model": name,
                "runs": stats["runs"],
                "total_tokens": stats["tokens"],
                "total_cost": round(float(stats["cost"]), 6),
                "has_pricing": stats["cost"] > 0 or stats["tokens"] == 0,
            }
            if name in openrouter_prices:
                entry["openrouter_pricing"] = openrouter_prices[name]
            models_data.append(entry)
        click.echo(json_dumps({"models": models_data}))
    else:
        # Table output
        table = Table(title="Model Pricing Coverage")
        table.add_column("Model", style="cyan")
        table.add_column("Runs", justify="right")
        table.add_column("Tokens", justify="right")
        table.add_column("Cost", justify="right")
        table.add_column("Status", justify="center")

        for name, stats in sorted(model_stats.items(), key=lambda x: -x[1]["tokens"]):
            has_pricing = stats["cost"] > 0 or stats["tokens"] == 0
            status = "[green]OK[/green]" if has_pricing else "[red]MISSING[/red]"
            table.add_row(
                name,
                f"{stats['runs']:,}",
                f"{stats['tokens']:,}",
                f"${stats['cost']:.4f}",
                status,
            )
        console = Console()
        console.print(table)

        if missing_models:
            console.print()
            if openrouter_prices:
                price_table = Table(title="OpenRouter Pricing (per million tokens)")
                price_table.add_column("Model", style="cyan")
                price_table.add_column("OpenRouter ID", style="dim")
                price_table.add_column("Input $/M", justify="right")
                price_table.add_column("Output $/M", justify="right")

                for model_name in missing_models:
                    if model_name in openrouter_prices:
                        p = openrouter_prices[model_name]
                        price_table.add_row(
                            model_name,
                            p.get("openrouter_id", ""),
                            f"${p['input_per_million']:.4f}",
                            f"${p['output_per_million']:.4f}",
                        )
                    else:
                        price_table.add_row(model_name, "", "[dim]not found[/dim]", "")
                console.print(price_table)

            console.print()
            console.print(
                "[yellow]To add missing pricing:[/yellow]\n"
                "  1. Open LangSmith Settings > Model Pricing\n"
                "  2. Click '+ Model' for each missing model\n"
                "  3. Set the match pattern to the model name shown above\n"
                "  4. Enter input/output prices per million tokens\n"
                "  [dim]Note: Pricing updates are NOT retroactive.[/dim]"
            )


def _fetch_openrouter_pricing(
    model_names: list[str],
    logger: Any,
) -> dict[str, dict[str, Any]]:
    """Fetch pricing from OpenRouter API for given model names.

    Returns dict mapping our model name -> pricing info.
    """
    import urllib.request
    import urllib.error

    # Map our model names to likely OpenRouter IDs
    name_mappings: dict[str, list[str]] = {}
    for name in model_names:
        candidates = [name]
        # Common transformations
        if "/" not in name:
            # Try adding common prefixes
            if name.startswith("llama"):
                candidates.append(f"meta-llama/{name}")
                candidates.append(f"meta-llama/{name}-instruct")
            elif name.startswith("qwen"):
                candidates.append(f"qwen/{name}")
            elif name.startswith("gpt"):
                candidates.append(f"openai/{name}")
        # Handle provider-specific suffixes
        if "-versatile" in name:
            base = name.replace("-versatile", "")
            candidates.append(f"meta-llama/{base}-instruct")
        name_mappings[name] = candidates

    # Fetch OpenRouter model list
    try:
        logger.info("Fetching pricing from OpenRouter API...")
        req = urllib.request.Request(
            "https://openrouter.ai/api/v1/models",
            headers={"User-Agent": "langsmith-cli"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
        logger.warning(f"Failed to fetch OpenRouter pricing: {e}")
        return {}

    # Build lookup from OpenRouter data
    or_models: dict[str, dict[str, Any]] = {}
    for m in data.get("data", []):
        model_id = m.get("id", "")
        pricing = m.get("pricing", {})
        prompt_price = pricing.get("prompt")
        completion_price = pricing.get("completion")
        if prompt_price is not None and completion_price is not None:
            or_models[model_id.lower()] = {
                "id": model_id,
                "input_per_token": float(prompt_price),
                "output_per_token": float(completion_price),
            }

    # Match our models to OpenRouter
    result: dict[str, dict[str, Any]] = {}
    for our_name, candidates in name_mappings.items():
        for candidate in candidates:
            key = candidate.lower()
            if key in or_models:
                info = or_models[key]
                result[our_name] = {
                    "openrouter_id": info["id"],
                    "input_per_million": round(info["input_per_token"] * 1_000_000, 4),
                    "output_per_million": round(
                        info["output_per_token"] * 1_000_000, 4
                    ),
                }
                break

    return result


@runs.command("export")
@click.argument("directory", type=click.Path())
@add_project_filter_options
@click.option("--limit", default=50, help="Max runs to export (default 50).")
@click.option(
    "--status", type=click.Choice(["success", "error"]), help="Filter by status."
)
@click.option("--filter", "filter_", help="LangSmith FQL filter.")
@click.option("--is-root", type=bool, help="Filter root traces only (true/false).")
@click.option(
    "--roots",
    is_flag=True,
    help="Export only root traces.",
)
@click.option("--run-type", help="Filter by run type (llm, chain, tool, etc).")
@click.option(
    "--tag",
    multiple=True,
    help="Filter by tag (can specify multiple).",
)
@add_time_filter_options
@click.option(
    "--filename-pattern",
    default="{run_id}.json",
    help="Filename pattern. Placeholders: {run_id}, {trace_id}, {index}, {name}. Default: {run_id}.json",
)
@fields_option()
@click.pass_context
def export_runs(
    ctx,
    directory,
    project,
    project_id,
    project_name,
    project_name_exact,
    project_name_pattern,
    project_name_regex,
    limit,
    status,
    filter_,
    is_root,
    roots,
    run_type,
    tag,
    since,
    before,
    last,
    filename_pattern,
    fields,
):
    """Export runs as individual JSON files to a directory.

    Each run is saved as a separate JSON file, enabling offline analysis
    and integration with AI coding agents.

    \b
    Examples:
        # Export last 50 root traces
        langsmith-cli runs export ./traces --project my-project --roots

        # Export error traces from last 24h
        langsmith-cli runs export ./errors --project my-project --status error --last 24h

        # Export with custom filenames
        langsmith-cli runs export ./traces --project my-project --filename-pattern "{name}_{run_id}.json"

        # Export with field pruning for smaller files
        langsmith-cli runs export ./traces --project my-project --fields name,inputs,outputs,status,latency
    """
    import re
    import pathlib

    logger = ctx.obj["logger"]
    logger.use_stderr = True  # Always use stderr for progress

    logger.debug(f"Exporting runs to: {directory}, limit={limit}")

    client = get_or_create_client(ctx)

    # Create output directory
    out_dir = pathlib.Path(directory)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Resolve project filters
    pq = resolve_project_filters(
        client,
        project=project,
        project_id=project_id,
        name=project_name,
        name_exact=project_name_exact,
        name_pattern=project_name_pattern,
        name_regex=project_name_regex,
    )
    projects_to_query = pq.names

    # Handle --roots flag
    if roots:
        is_root = True

    # Build filter using shared helper (reuse canonical filter builder)
    combined_filter, error_filter = build_runs_list_filter(
        filter_=filter_,
        status=status,
        tag=tag,
        since=since,
        before=before,
        last=last,
    )

    logger.info(f"Fetching up to {limit} runs from project(s)...")

    # Fetch runs using the shared fetch_from_projects helper
    def _fetch_runs(c: Any, proj: str | None, **kw: Any) -> Any:
        if proj is not None:
            return c.list_runs(project_name=proj, **kw)
        return c.list_runs(**kw)

    result = fetch_from_projects(
        client,
        projects_to_query,
        _fetch_runs,
        project_query=pq,
        limit=limit,
        error=error_filter,
        filter=combined_filter,
        run_type=run_type,
        is_root=is_root,
        console=None,
        show_warnings=False,
    )
    all_runs: list[Run] = result.items

    # If all sources failed, raise with suggestions (reports failures internally).
    # Otherwise, report partial failures.
    raise_if_all_failed_with_suggestions(result, client, pq, logger, "runs")
    if result.has_failures:
        result.report_failures_to_logger(logger)

    if not all_runs:
        if ctx.obj.get("json"):
            click.echo(
                json_dumps(
                    {"status": "success", "exported": 0, "directory": str(out_dir)}
                )
            )
        else:
            logger.warning("No runs found matching filters.")
        return

    # Apply limit across all projects
    if len(all_runs) > limit:
        all_runs = all_runs[:limit]

    include_fields = parse_fields_option(fields)

    def _sanitize_filename(name: str) -> str:
        """Sanitize a string for use as a filename."""
        safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
        safe = safe.strip(". ")
        if len(safe) > 200:
            safe = safe[:200]
        return safe or "unnamed"

    # Validate filename pattern before the loop
    _valid_pattern_vars = {"run_id", "trace_id", "index", "name"}
    try:
        filename_pattern.format(run_id="test", trace_id="test", index=0, name="test")
    except KeyError as e:
        raise click.ClickException(
            f"Invalid filename pattern variable {e}. "
            f"Valid variables: {{{', '.join(sorted(_valid_pattern_vars))}}}"
        )

    # Export runs sequentially (local file I/O is fast, no need for threads)
    exported_files: list[str] = []
    errors: list[dict[str, str]] = []

    for index, run in enumerate(all_runs):
        try:
            # Build filename from pattern
            safe_name = _sanitize_filename(run.name or "unnamed")
            fname = filename_pattern.format(
                run_id=run.id,
                trace_id=run.trace_id or run.id,
                index=index,
                name=safe_name,
            )

            # Dump run data
            if include_fields:
                data = run.model_dump(include=include_fields, mode="json")
            else:
                data = run.model_dump(mode="json")

            file_path = out_dir / fname
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(json_dumps(data, indent=2))

            exported_files.append(fname)
        except OSError as e:
            errors.append({"run_id": str(run.id), "error": str(e)})

    if ctx.obj.get("json"):
        click.echo(
            json_dumps(
                {
                    "status": "success",
                    "exported": len(exported_files),
                    "directory": str(out_dir),
                    "files": sorted(exported_files),
                    "errors": errors,
                }
            )
        )
    else:
        logger.success(f"Exported {len(exported_files)} run(s) to {out_dir}/")
        if errors:
            for err in errors:
                logger.warning(f"Failed to export {err['run_id']}: {err['error']}")
