import click
from rich.console import Console
from rich.table import Table
import langsmith
import json

console = Console()


@click.group()
def projects():
    """Manage LangSmith projects."""
    pass


@projects.command("list")
@click.option("--limit", default=100, help="Limit number of projects (default 100).")
@click.option("--name", "name_", help="Filter by project name substring.")
@click.option("--name-pattern", help="Filter by name with wildcards (e.g. '*prod*').")
@click.option("--name-regex", help="Filter by name with regex (e.g. '^prod-.*-v[0-9]+$').")
@click.option("--reference-dataset-id", help="Filter experiments for a dataset (by ID).")
@click.option("--reference-dataset-name", help="Filter experiments for a dataset (by name).")
@click.option("--has-runs", is_flag=True, help="Show only projects with runs (run_count > 0).")
@click.option("--sort-by", help="Sort by field (name, run_count). Prefix with - for descending.")
@click.pass_context
def list_projects(ctx, limit, name_, name_pattern, name_regex, reference_dataset_id, reference_dataset_name, has_runs, sort_by):
    """List all projects."""
    import re
    import datetime

    client = langsmith.Client()

    # Use name_ (SDK substring filter) if provided and no pattern/regex
    # Use name_pattern as a fallback to name_ if neither are specific filters
    api_name_filter = name_
    if name_pattern and not name_:
        # Extract search term from wildcard pattern for API filtering
        search_term = name_pattern.replace("*", "")
        if search_term:
            api_name_filter = search_term

    # list_projects returns a generator
    projects_gen = client.list_projects(
        limit=limit,
        name=api_name_filter,
        reference_dataset_id=reference_dataset_id,
        reference_dataset_name=reference_dataset_name,
    )

    # Materialize the list to count and process
    projects_list = list(projects_gen)

    # Client-side pattern matching (wildcards)
    if name_pattern:
        # Convert wildcards to regex
        pattern = name_pattern.replace("*", ".*").replace("?", ".")
        regex_pattern = re.compile(pattern)
        projects_list = [p for p in projects_list if p.name and regex_pattern.search(p.name)]

    # Client-side regex filtering
    if name_regex:
        try:
            regex_pattern = re.compile(name_regex)
        except re.error as e:
            raise click.BadParameter(f"Invalid regex pattern: {name_regex}. Error: {e}")
        projects_list = [p for p in projects_list if p.name and regex_pattern.search(p.name)]

    # Filter by projects with runs
    if has_runs:
        projects_list = [p for p in projects_list if hasattr(p, "run_count") and p.run_count and p.run_count > 0]

    # Client-side sorting for table output
    if sort_by and not ctx.obj.get("json"):
        reverse = sort_by.startswith("-")
        sort_field = sort_by.lstrip("-")

        # Map sort field to project attribute
        sort_key_map = {
            "name": lambda p: (p.name or "").lower(),
            "run_count": lambda p: p.run_count if hasattr(p, "run_count") and p.run_count else 0,
        }

        if sort_field in sort_key_map:
            try:
                projects_list = sorted(projects_list, key=sort_key_map[sort_field], reverse=reverse)
            except Exception as e:
                console.print(f"[yellow]Warning: Could not sort by {sort_field}: {e}[/yellow]")
        else:
            console.print(f"[yellow]Warning: Unknown sort field '{sort_field}'. Using default order.[/yellow]")

    if ctx.obj.get("json"):
        # Use SDK's Pydantic models with focused field selection for context efficiency
        data = [
            p.model_dump(
                include={"name", "id"},
                mode="json",
            )
            for p in projects_list
        ]
        click.echo(json.dumps(data, default=str))
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
