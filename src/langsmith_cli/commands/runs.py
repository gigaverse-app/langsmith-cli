from typing import Any

import click
from rich.console import Console
from rich.table import Table

from langsmith_cli.utils import (
    add_project_filter_options,
    apply_client_side_limit,
    determine_output_format,
    fields_option,
    filter_fields,
    get_matching_items,
    get_matching_projects,
    get_or_create_client,
    output_formatted_data,
    sort_items,
)

console = Console()


@click.group()
def runs():
    """Inspect and filter application traces."""
    pass


def parse_duration_to_seconds(duration_str):
    """Parse duration string like '2s', '500ms', '1.5s' to FQL format."""
    import re

    # LangSmith FQL accepts durations like "2s", "500ms", "1.5s"
    # Just validate format and return as-is
    if not re.match(r"^\d+(\.\d+)?(s|ms|m|h|d)$", duration_str):
        raise click.BadParameter(
            f"Invalid duration format: {duration_str}. Use format like '2s', '500ms', '1.5s', '5m', '2h', '7d'"
        )
    return duration_str


def parse_relative_time(time_str):
    """Parse relative time like '24h', '7d', '30m' to datetime."""
    import re
    import datetime

    match = re.match(r"^(\d+)(m|h|d)$", time_str)
    if not match:
        raise click.BadParameter(
            f"Invalid time format: {time_str}. Use format like '30m', '24h', '7d'"
        )

    value, unit = int(match.group(1)), match.group(2)

    if unit == "m":
        delta = datetime.timedelta(minutes=value)
    elif unit == "h":
        delta = datetime.timedelta(hours=value)
    elif unit == "d":
        delta = datetime.timedelta(days=value)
    else:
        raise click.BadParameter(f"Unsupported time unit: {unit}")

    return datetime.datetime.now(datetime.timezone.utc) - delta


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

    if len(filters) == 1:
        return filters[0]
    else:
        return f"and({', '.join(filters)})"


def extract_group_value(run: Any, grouping_type: str, field_name: str) -> str | None:
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
    runs: list[Any], requested_metrics: list[str]
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
        result["avg_cost"] = statistics.mean(costs) if costs else 0.0

    return result


@runs.command("list")
@add_project_filter_options
@click.option("--limit", default=20, help="Max runs to fetch (per project).")
@click.option(
    "--status", type=click.Choice(["success", "error"]), help="Filter by status."
)
@click.option("--filter", "filter_", help="LangSmith filter string.")
@click.option("--trace-id", help="Get all runs in a specific trace.")
@click.option(
    "--run-type", help="Filter by run type (llm, chain, tool, retriever, etc)."
)
@click.option("--is-root", type=bool, help="Filter root traces only (true/false).")
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
@click.option("--name-pattern", help="Filter run names with wildcards (e.g. '*auth*').")
@click.option(
    "--name-regex", help="Filter run names with regex (e.g. '^test-.*-v[0-9]+$')."
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
    "--since", help="Show runs since time (ISO format or relative like '1 hour ago')."
)
@click.option("--last", help="Show runs from last duration (e.g., '24h', '7d', '30m').")
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
@fields_option()
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
    sort_by,
    output_format,
    fields,
):
    """Fetch recent runs from one or more projects.

    Use project filters (--project-name, --project-name-pattern, --project-name-regex, --project-name-exact) to match multiple projects.
    Use run name filters (--name-pattern, --name-regex) to filter specific run names.
    """
    import datetime

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

    # Flexible time filters
    if since:
        # Try parsing as ISO timestamp first, then as relative time
        try:
            # ISO format (Python 3.7+ fromisoformat)
            timestamp = datetime.datetime.fromisoformat(since.replace("Z", "+00:00"))
        except Exception:
            # Try relative time parsing
            try:
                timestamp = parse_relative_time(since)
            except Exception:
                raise click.BadParameter(
                    f"Invalid --since format: {since}. Use ISO format (2024-01-14T10:00:00Z) or relative time (24h, 7d)"
                )
        fql_filters.append(f'gt(start_time, "{timestamp.isoformat()}")')

    if last:
        timestamp = parse_relative_time(last)
        fql_filters.append(f'gt(start_time, "{timestamp.isoformat()}")')

    # Combine all filters with AND logic
    combined_filter = None
    if fql_filters:
        if len(fql_filters) == 1:
            combined_filter = fql_filters[0]
        else:
            # Wrap in and() for multiple filters
            filter_str = ", ".join(fql_filters)
            combined_filter = f"and({filter_str})"

    # Determine if client-side filtering is needed
    # (for run name pattern/regex matching)
    needs_client_filtering = bool(name_regex or name_pattern)

    # If client-side filtering needed, fetch more results
    api_limit = None if needs_client_filtering else limit

    # Fetch runs from all matching projects
    all_runs = []
    for proj_name in projects_to_query:
        try:
            project_runs = client.list_runs(
                project_name=proj_name,
                limit=api_limit,
                error=error_filter,
                filter=combined_filter,
                trace_id=trace_id,
                run_type=run_type,
                is_root=is_root,
                trace_filter=trace_filter,
                tree_filter=tree_filter,
                order_by=order_by,
                reference_example_id=reference_example_id,
            )
            all_runs.extend(list(project_runs))
        except Exception:
            # Skip projects that fail to fetch (e.g., permissions)
            pass

    # Apply universal filtering to run names (client-side filtering)
    # FQL doesn't support full regex or complex patterns for run names
    runs = get_matching_items(
        all_runs,
        name_pattern=name_pattern,
        name_regex=name_regex,
        name_getter=lambda r: r.name or "",
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

    # Apply user's limit AFTER all client-side filtering/sorting
    runs = apply_client_side_limit(runs, limit, needs_client_filtering)

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

    table = Table(title=table_title)
    table.add_column("ID", style="dim", no_wrap=True)
    table.add_column("Name")
    table.add_column("Status", justify="center")
    table.add_column("Latency")

    count = 0
    for r in runs:
        count += 1
        # Access SDK model fields directly (type-safe)
        r_id = str(r.id)
        r_name = r.name or "Unknown"
        r_status = r.status

        # Colorize status
        status_style = (
            "green"
            if r_status == "success"
            else "red"
            if r_status == "error"
            else "yellow"
        )

        latency = f"{r.latency:.2f}s" if r.latency is not None else "-"

        table.add_row(
            r_id, r_name, f"[{status_style}]{r_status}[/{status_style}]", latency
        )

    if count == 0:
        console.print("[yellow]No runs found.[/yellow]")
    else:
        console.print(table)


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
        import json

        click.echo(json.dumps(data, default=str))
        return

    # Human readable output
    from rich.syntax import Syntax
    import json

    console.print(f"[bold]Run ID:[/bold] {data.get('id')}")
    console.print(f"[bold]Name:[/bold] {data.get('name')}")

    # Print other fields
    for k, v in data.items():
        if k in ["id", "name"]:
            continue
        console.print(f"\n[bold]{k}:[/bold]")
        if isinstance(v, (dict, list)):
            formatted = json.dumps(v, indent=2, default=str)
            console.print(Syntax(formatted, "json"))
        else:
            console.print(str(v))


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
        import json

        click.echo(json.dumps(stats, default=str))
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
        all_runs: list[tuple[str, Any]] = []
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
                # Skip projects that fail to fetch
                pass

        # Sort by start time (most recent first) and limit to 10
        all_runs.sort(key=lambda item: item[1].start_time or "", reverse=True)
        all_runs = all_runs[:10]

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
@click.option("--limit", default=10, help="Max results.")
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
    limit,
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

    # Combine filters with AND
    combined_filter = filters[0] if len(filters) == 1 else f"and({', '.join(filters)})"

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
        since=None,
        last=None,
        sort_by=None,
    )


@runs.command("sample")
@add_project_filter_options
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
    """
    import json
    import itertools

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

            # Combine with additional filter if provided
            if additional_filter:
                combined_filter = f"and({stratum_filter}, {additional_filter})"
            else:
                combined_filter = stratum_filter

            # Fetch samples from all matching projects
            stratum_runs = []
            for proj_name in projects_to_query:
                try:
                    project_runs = client.list_runs(
                        project_name=proj_name,
                        limit=sample_limit,
                        filter=combined_filter,
                        order_by="-start_time",
                    )
                    stratum_runs.extend(list(project_runs))
                except Exception:
                    # Skip projects that fail to fetch
                    pass

            # Limit to sample_limit
            stratum_runs = stratum_runs[:sample_limit]

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

            # Combine with additional filter if provided
            if additional_filter:
                combined_filter = f"and({stratum_filter}, {additional_filter})"
            else:
                combined_filter = stratum_filter

            # Fetch samples from all matching projects
            stratum_runs = []
            for proj_name in projects_to_query:
                try:
                    project_runs = client.list_runs(
                        project_name=proj_name,
                        limit=samples_per_stratum,
                        filter=combined_filter,
                        order_by="-start_time",
                    )
                    stratum_runs.extend(list(project_runs))
                except Exception:
                    # Skip projects that fail to fetch
                    pass

            # Limit to samples_per_stratum
            stratum_runs = stratum_runs[:samples_per_stratum]

            # Add stratum field and convert to dicts
            for run in stratum_runs:
                run_dict = filter_fields(run, fields)
                run_dict["stratum"] = f"{field_name}:{stratum_value}"
                all_samples.append(run_dict)

    # Output as JSONL
    if output:
        # Write to file
        try:
            with open(output, "w") as f:
                for sample in all_samples:
                    f.write(json.dumps(sample, default=str) + "\n")
            console.print(
                f"[green]Wrote {len(all_samples)} samples to {output}[/green]"
            )
        except Exception as e:
            console.print(f"[red]Error writing to file {output}: {e}[/red]")
            raise click.Abort()
    else:
        # Write to stdout (JSONL format)
        for sample in all_samples:
            click.echo(json.dumps(sample, default=str))


@runs.command("analyze")
@add_project_filter_options
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
    from collections import defaultdict

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
        for proj_name in projects_to_query:
            try:
                project_runs = client.list_runs(
                    project_name=proj_name,
                    filter=additional_filter,
                    limit=None,
                    order_by="-start_time",
                )
                all_runs.extend(list(project_runs))
            except Exception:
                pass
    else:
        # Use sample-based approach with field selection (FAST!)
        # API has max limit of 100 when using select, so manually collect from iterator
        for proj_name in projects_to_query:
            try:
                runs_iter = client.list_runs(
                    project_name=proj_name,
                    filter=additional_filter,
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
            except Exception:
                pass

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
        console.print("[yellow]No groups found.[/yellow]")
    else:
        console.print(table)


@runs.command("tags")
@add_project_filter_options
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
    import json

    client = get_or_create_client(ctx)

    # Get matching projects
    projects_to_query = get_matching_projects(
        client,
        project=project,
        name=project_name,
        name_exact=project_name_exact,
        name_pattern=project_name_pattern,
        name_regex=project_name_regex,
    )

    # Fetch sample of recent runs
    # Use select to only fetch fields we need (much faster - 2x speedup, 14x less data)
    all_runs = []
    for proj_name in projects_to_query:
        try:
            project_runs = client.list_runs(
                project_name=proj_name,
                limit=sample_size,
                order_by="-start_time",
                select=["tags"],  # Only fetch tags field
            )
            all_runs.extend(list(project_runs))
        except Exception:
            # Skip projects that fail to fetch
            pass

    # Parse tags to extract key:value patterns
    tag_patterns: dict[str, set[str]] = defaultdict(set)

    for run in all_runs:
        if run.tags:
            for tag in run.tags:
                if ":" in tag:
                    # Structured tag: key:value
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
        click.echo(json.dumps(result, default=str))
    else:
        from rich.table import Table

        table = Table(title="Tag Patterns")
        table.add_column("Tag Key", style="cyan")
        table.add_column("Values", style="green")

        for key, values in result["tag_patterns"].items():
            # Truncate long value lists for readability
            value_str = ", ".join(values[:10])
            if len(values) > 10:
                value_str += f" ... (+{len(values) - 10} more)"
            table.add_row(key, value_str)

        if not result["tag_patterns"]:
            console.print(
                "[yellow]No structured tags found (key:value format).[/yellow]"
            )
        else:
            console.print(table)
            console.print(
                f"\n[dim]Analyzed {len(all_runs)} runs from {len(projects_to_query)} project(s)[/dim]"
            )


@runs.command("metadata-keys")
@add_project_filter_options
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
    import json

    client = get_or_create_client(ctx)

    # Get matching projects
    projects_to_query = get_matching_projects(
        client,
        project=project,
        name=project_name,
        name_exact=project_name_exact,
        name_pattern=project_name_pattern,
        name_regex=project_name_regex,
    )

    # Fetch sample of recent runs
    # Use select to only fetch fields we need (much faster - 2x speedup, 14x less data)
    all_runs = []
    for proj_name in projects_to_query:
        try:
            project_runs = client.list_runs(
                project_name=proj_name,
                limit=sample_size,
                order_by="-start_time",
                select=["extra"],  # Only fetch metadata (stored in extra field)
            )
            all_runs.extend(list(project_runs))
        except Exception:
            # Skip projects that fail to fetch
            pass

    # Extract all metadata keys
    metadata_keys: set[str] = set()

    for run in all_runs:
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
        click.echo(json.dumps(result, default=str))
    else:
        from rich.table import Table

        table = Table(title="Metadata Keys")
        table.add_column("Key", style="cyan")
        table.add_column("Type", style="dim")

        for key in result["metadata_keys"]:
            table.add_row(key, "metadata")

        if not result["metadata_keys"]:
            console.print("[yellow]No metadata keys found.[/yellow]")
        else:
            console.print(table)
            console.print(
                f"\n[dim]Analyzed {len(all_runs)} runs from {len(projects_to_query)} project(s)[/dim]"
            )
