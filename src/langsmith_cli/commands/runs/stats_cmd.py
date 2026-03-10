"""Stats command for runs."""

from typing import Any

import click
from rich.table import Table

from langsmith_cli.commands.runs._group import runs, console
from langsmith_cli.utils import (
    add_project_filter_options,
    get_or_create_client,
    json_dumps,
    resolve_project_filters,
)


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
    resolved_project_ids: list[Any] = []
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
