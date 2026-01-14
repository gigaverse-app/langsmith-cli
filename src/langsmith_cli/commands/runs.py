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
@click.pass_context
def list_runs(ctx, project, limit, status, filter_):
    """Fetch recent runs."""
    client = langsmith.Client()

    error_filter = None
    if status == "error":
        error_filter = True
    elif status == "success":
        error_filter = False

    runs = client.list_runs(
        project_name=project, limit=limit, error=error_filter, filter=filter_
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
        r_id = str(getattr(r, "id", ""))
        r_name = getattr(r, "name", "Unknown")
        r_status = getattr(r, "status", "unknown")

        # Colorize status
        status_style = (
            "green"
            if r_status == "success"
            else "red"
            if r_status == "error"
            else "yellow"
        )

        latency = (
            f"{getattr(r, 'latency', 0):.2f}s"
            if getattr(r, "latency") is not None
            else "-"
        )

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
    stats = client.get_run_stats(project_name=project)

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
            r_id = str(getattr(r, "id", ""))
            r_name = getattr(r, "name", "Unknown")
            r_status = getattr(r, "status", "unknown")
            status_style = (
                "green"
                if r_status == "success"
                else "red"
                if r_status == "error"
                else "yellow"
            )
            latency = (
                f"{getattr(r, 'latency', 0):.2f}s"
                if getattr(r, "latency") is not None
                else "-"
            )
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
