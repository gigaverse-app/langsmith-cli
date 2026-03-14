"""Open and watch commands for runs."""

import click
from rich.table import Table
from langsmith.schemas import Run

from langsmith_cli.commands.runs._group import runs
from langsmith_cli.utils import (
    add_project_filter_options,
    get_or_create_client,
    json_dumps,
    resolve_project_filters,
)


@runs.command("open")
@click.argument("run_id")
@click.pass_context
def open_run(ctx, run_id):
    """Open a run in the LangSmith UI."""
    import webbrowser

    client = get_or_create_client(ctx)

    run = client.read_run(run_id)
    project = client.read_project(project_id=run.session_id)

    org_id = project.tenant_id
    project_id = run.session_id
    trace_id = run.trace_id or run.id

    url = (
        f"https://smith.langchain.com/o/{org_id}/projects/p/{project_id}"
        f"?peek={run_id}&peeked_trace={trace_id}"
    )

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
                runs_list = list(
                    client.list_runs(
                        project_id=pq.project_id,
                        limit=10,
                        is_root=True,
                    )
                )
                label = f"id:{pq.project_id}"
                all_runs.extend((label, run) for run in runs_list)
            except Exception:
                failed_count += 1
        else:
            for proj_name in pq.names:
                try:
                    runs_list = list(
                        client.list_runs(
                            project_name=proj_name,
                            limit=5 if project_name_pattern else 10,
                            is_root=True,
                        )
                    )
                    all_runs.extend((proj_name, run) for run in runs_list)
                except Exception:
                    failed_count += 1

        # Sort by start time (most recent first) and limit to 10
        all_runs.sort(key=lambda item: item[1].start_time or "", reverse=True)
        all_runs = all_runs[:10]

        # Add failure count to title if any projects failed
        if failed_count > 0:
            table.title = title + f" ({failed_count} failed)"

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
