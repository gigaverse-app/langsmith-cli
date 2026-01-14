import click
from rich.console import Console
from rich.table import Table
import langsmith
from langsmith_cli.utils import (
    output_formatted_data,
    sort_items,
    apply_regex_filter,
    determine_output_format,
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


@runs.command("list")
@click.option("--project", default="default", help="Project name.")
@click.option("--limit", default=20, help="Max runs to fetch.")
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
@click.option("--name-pattern", help="Filter by name with wildcards (e.g. '*auth*').")
@click.option(
    "--name-regex", help="Filter by name with regex (e.g. '^test-.*-v[0-9]+$')."
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
@click.pass_context
def list_runs(
    ctx,
    project,
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
):
    """Fetch recent runs."""
    import datetime

    client = langsmith.Client()

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

    # Name pattern (convert wildcards to FQL search)
    if name_pattern:
        # Convert shell wildcards to FQL search pattern
        # For now, simple implementation: * becomes substring search
        search_term = name_pattern.replace("*", "")
        if search_term:
            fql_filters.append(f'search("{search_term}")')

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

    runs = client.list_runs(
        project_name=project,
        limit=limit,
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

    # Convert generator to list
    runs = list(runs)

    # Client-side regex filtering (FQL doesn't support full regex)
    runs = apply_regex_filter(runs, name_regex, lambda r: r.name)

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

    # Determine output format
    format_type = determine_output_format(output_format, ctx.obj.get("json"))

    # Handle non-table formats
    if format_type != "table":
        data = [r.dict() if hasattr(r, "dict") else dict(r) for r in runs]
        output_formatted_data(data, format_type)
        return

    table = Table(title=f"Runs ({project})")
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
@click.option(
    "--fields", help="Comma-separated list of fields to include (e.g. inputs,error)."
)
@click.pass_context
def get_run(ctx, run_id, fields):
    """Fetch details of a single run."""
    client = langsmith.Client()
    run = client.read_run(run_id)

    # Convert to dict
    data = run.dict() if hasattr(run, "dict") else dict(run)

    # Apply context pruning if requested
    if fields:
        field_list = [f.strip() for f in fields.split(",")]
        # Always include ID and name for context
        field_list.extend(["id", "name"])
        data = {k: v for k, v in data.items() if k in field_list}

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
@click.option("--project", default="default", help="Project name.")
@click.pass_context
def run_stats(ctx, project):
    """Fetch aggregated metrics for a project."""
    client = langsmith.Client()
    # Resolve project name to ID
    try:
        p = client.read_project(project_name=project)
        project_id = p.id
    except Exception:
        # Fallback if name fails or user passed ID
        project_id = project

    stats = client.get_run_stats(project_ids=[project_id])

    if ctx.obj.get("json"):
        import json

        click.echo(json.dumps(stats, default=str))
        return

    table = Table(title=f"Stats: {project}")
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
@click.option("--project", default="default", help="Project name.")
@click.option("--interval", default=2.0, help="Refresh interval in seconds.")
@click.pass_context
def watch_runs(ctx, project, interval):
    """Live dashboard of runs."""
    from rich.live import Live
    import time

    client = langsmith.Client()

    def generate_table():
        runs = client.list_runs(project_name=project, limit=10)
        table = Table(title=f"Watching: {project} (Interval: {interval}s)")
        table.add_column("ID", style="dim", no_wrap=True)
        table.add_column("Name")
        table.add_column("Status", justify="center")
        table.add_column("Latency")

        for r in runs:
            # Access SDK model fields directly (type-safe)
            r_id = str(r.id)
            r_name = r.name or "Unknown"
            r_status = r.status
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
@click.option("--project", default="default", help="Project name.")
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
    limit,
    search_in,
    input_contains,
    output_contains,
    output_format,
):
    """Search runs using full-text search.

    QUERY is the text to search for across runs.

    Examples:
      langsmith-cli runs search "authentication failed"
      langsmith-cli runs search "timeout" --in error
      langsmith-cli runs search "user_123" --in inputs
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

    # Invoke list_runs with the filter
    return ctx.invoke(
        list_runs,
        project=project,
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
