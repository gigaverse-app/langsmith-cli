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
@click.pass_context
def list_projects(ctx, limit, name_, name_pattern, name_regex, reference_dataset_id, reference_dataset_name):
    """List all projects."""
    import re

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
