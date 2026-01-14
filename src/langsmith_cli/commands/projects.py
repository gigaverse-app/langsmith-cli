import click
from rich.console import Console
from rich.table import Table
import langsmith
import json
from langsmith_cli.utils import (
    output_formatted_data,
    sort_items,
    apply_regex_filter,
    apply_wildcard_filter,
    determine_output_format,
)

console = Console()


@click.group()
def projects():
    """Manage LangSmith projects."""
    pass


@projects.command("list")
@click.option("--limit", default=100, help="Limit number of projects (default 100).")
@click.option("--name", "name_", help="Filter by project name substring.")
@click.option("--name-pattern", help="Filter by name with wildcards (e.g. '*prod*').")
@click.option(
    "--name-regex", help="Filter by name with regex (e.g. '^prod-.*-v[0-9]+$')."
)
@click.option(
    "--reference-dataset-id", help="Filter experiments for a dataset (by ID)."
)
@click.option(
    "--reference-dataset-name", help="Filter experiments for a dataset (by name)."
)
@click.option(
    "--has-runs", is_flag=True, help="Show only projects with runs (run_count > 0)."
)
@click.option(
    "--sort-by", help="Sort by field (name, run_count). Prefix with - for descending."
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["table", "json", "csv", "yaml"]),
    help="Output format (default: table, or json if --json flag used).",
)
@click.pass_context
def list_projects(
    ctx,
    limit,
    name_,
    name_pattern,
    name_regex,
    reference_dataset_id,
    reference_dataset_name,
    has_runs,
    sort_by,
    output_format,
):
    """List all projects."""

    client = langsmith.Client()

    # Use name_ (SDK substring filter) if provided and no pattern/regex
    # Use name_pattern/name_regex as a fallback to name_ for API optimization
    api_name_filter = name_
    if name_pattern and not name_:
        # Only optimize if pattern is unanchored (*term*) - anchored patterns (*term or term*)
        # need client-side filtering for correct results
        if name_pattern.startswith("*") and name_pattern.endswith("*"):
            # Extract search term from wildcard pattern for API filtering
            search_term = name_pattern.replace("*", "")
            if search_term:
                api_name_filter = search_term
    elif name_regex and not name_ and not name_pattern:
        # Extract search term from regex pattern for API filtering
        import re

        # Remove common regex metacharacters to find literal substring
        search_term = re.sub(r"[.*+?^${}()\[\]\\|]", "", name_regex)
        if search_term and len(search_term) >= 2:  # Only use if reasonably long
            api_name_filter = search_term

    # list_projects returns a generator
    projects_gen = client.list_projects(
        limit=limit,
        name_contains=api_name_filter,
        reference_dataset_id=reference_dataset_id,
        reference_dataset_name=reference_dataset_name,
    )

    # Materialize the list to count and process
    projects_list = list(projects_gen)

    # Client-side pattern matching (wildcards)
    projects_list = apply_wildcard_filter(projects_list, name_pattern, lambda p: p.name)

    # Client-side regex filtering
    projects_list = apply_regex_filter(projects_list, name_regex, lambda p: p.name)

    # Filter by projects with runs
    if has_runs:
        projects_list = [
            p
            for p in projects_list
            if hasattr(p, "run_count") and p.run_count and p.run_count > 0
        ]

    # Client-side sorting for table output
    if sort_by and not ctx.obj.get("json"):
        # Map sort field to project attribute
        sort_key_map = {
            "name": lambda p: (p.name or "").lower(),
            "run_count": lambda p: p.run_count
            if hasattr(p, "run_count") and p.run_count
            else 0,
        }
        projects_list = sort_items(projects_list, sort_by, sort_key_map, console)

    # Determine output format
    format_type = determine_output_format(output_format, ctx.obj.get("json"))

    # Handle non-table formats
    if format_type != "table":
        # Use SDK's Pydantic models with focused field selection for context efficiency
        data = [
            p.model_dump(
                include={"name", "id"},
                mode="json",
            )
            for p in projects_list
        ]
        output_formatted_data(data, format_type)
        return

    table = Table(title="Projects")
    table.add_column("Name", style="cyan")
    table.add_column("ID", style="dim")

    for p in projects_list:
        # Access attributes directly (type-safe)
        table.add_row(p.name, str(p.id))

    if not projects_list:
        console.print("[yellow]No projects found.[/yellow]")
    else:
        console.print(table)


@projects.command("create")
@click.argument("name")
@click.option("--description", help="Project description.")
@click.pass_context
def create_project(ctx, name, description):
    """Create a new project."""
    from langsmith.utils import LangSmithConflictError

    client = langsmith.Client()
    try:
        project = client.create_project(project_name=name, description=description)
        if ctx.obj.get("json"):
            # Use SDK's Pydantic model directly
            data = project.model_dump(mode="json")
            click.echo(json.dumps(data, default=str))
            return

        console.print(
            f"[green]Created project {project.name}[/green] (ID: {project.id})"
        )
    except LangSmithConflictError:
        # Project already exists - handle gracefully for idempotency
        console.print(f"[yellow]Project {name} already exists.[/yellow]")
