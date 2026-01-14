import click
from rich.console import Console
from rich.table import Table
import langsmith

console = Console()


@click.group()
def runs():
    """Inspect and filter application traces."""
    pass


@runs.command("list")
@click.option("--project", default="default", help="Project name.")
@click.option("--limit", default=20, help="Max runs to fetch.")
@click.option(
    "--status", type=click.Choice(["success", "error"]), help="Filter by status."
)
@click.option("--filter", "filter_", help="LangSmith filter string.")
@click.option("--trace-id", help="Get all runs in a specific trace.")
@click.option("--run-type", help="Filter by run type (llm, chain, tool, retriever, etc).")
@click.option("--is-root", type=bool, help="Filter root traces only (true/false).")
@click.option("--trace-filter", help="Filter applied to root trace.")
@click.option("--tree-filter", help="Filter if any run in trace tree matches.")
@click.option("--order-by", default="-start_time", help="Sort field (prefix with - for desc).")
@click.option("--reference-example-id", help="Filter runs for a specific example.")
@click.option("--tag", multiple=True, help="Filter by tag (can specify multiple times for AND logic).")
@click.option("--name-pattern", help="Filter by name with wildcards (e.g. '*auth*').")
@click.option("--slow", is_flag=True, help="Filter to slow runs (latency > 5s).")
@click.option("--expensive", is_flag=True, help="Filter to expensive runs (cost > $0.01).")
@click.option("--recent", is_flag=True, help="Filter to recent runs (last hour).")
@click.option("--today", is_flag=True, help="Filter to today's runs.")
@click.pass_context
def list_runs(ctx, project, limit, status, filter_, trace_id, run_type, is_root, trace_filter, tree_filter, order_by, reference_example_id, tag, name_pattern, slow, expensive, recent, today):
    """Fetch recent runs."""
    import datetime

    client = langsmith.Client()

    error_filter = None
    if status == "error":
        error_filter = True
    elif status == "success":
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

    # Smart filters
    if slow:
        fql_filters.append('gt(latency, "5s")')

    if expensive:
        fql_filters.append('gt(total_cost, "0.01")')

    if recent:
        # Last hour
        one_hour_ago = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=1)
        fql_filters.append(f'gt(start_time, "{one_hour_ago.isoformat()}")')

    if today:
        # Today's runs (midnight to now)
        today_start = datetime.datetime.now(datetime.timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        fql_filters.append(f'gt(start_time, "{today_start.isoformat()}")')

    # Combine all filters with AND logic
    combined_filter = None
    if fql_filters:
        if len(fql_filters) == 1:
            combined_filter = fql_filters[0]
        else:
            # Wrap in and() for multiple filters
            filter_str = ", ".join(fql_filters)
            combined_filter = f'and({filter_str})'

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

    if ctx.obj.get("json"):
        import json

        data = [r.dict() if hasattr(r, "dict") else dict(r) for r in runs]
        click.echo(json.dumps(data, default=str))
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
@click.option("--filter", "filter_", required=True, help="LangSmith filter string.")
@click.option("--project", default="default", help="Project name.")
@click.option("--limit", default=10, help="Max results.")
@click.pass_context
def search_runs(ctx, filter_, project, limit):
    """Search runs using advanced filter syntax."""
    # Reuse list_runs logic
    return ctx.invoke(list_runs, project=project, limit=limit, filter_=filter_)
