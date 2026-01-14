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
@click.option("--reference-dataset-id", help="Filter experiments for a dataset (by ID).")
@click.option("--reference-dataset-name", help="Filter experiments for a dataset (by name).")
@click.pass_context
def list_projects(ctx, limit, name_, reference_dataset_id, reference_dataset_name):
    """List all projects."""
    client = langsmith.Client()
    # list_projects returns a generator
    projects_gen = client.list_projects(
        limit=limit,
        name=name_,
        reference_dataset_id=reference_dataset_id,
        reference_dataset_name=reference_dataset_name,
    )

    # Materialize the list to count and process
    projects_list = list(projects_gen)

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
