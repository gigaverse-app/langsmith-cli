import click
from rich.console import Console
from rich.table import Table
from langsmith import Client

console = Console()


@click.group()
def projects():
    """Manage LangSmith projects."""
    pass


@projects.command("list")
@click.option("--limit", default=20, help="Limit number of projects.")
@click.option("--offset", default=0, help="Offset for pagination.")
@click.pass_context
def list_projects(ctx, limit, offset):
    """List all projects."""
    client = Client()
    projects = client.list_projects(limit=limit, offset=offset)

    # Check if JSON mode involves just dumping Pydantic models or dicts
    if ctx.obj.get("json"):
        import json

        # Handle generator return from list_projects
        data = [p.dict() if hasattr(p, "dict") else dict(p) for p in projects]
        click.echo(json.dumps(data, default=str))  # default=str for datetimes
        return

    table = Table(title="Projects")
    table.add_column("Name", style="cyan")
    table.add_column("ID", style="dim")
    table.add_column("Runs", justify="right")
    table.add_column("Active", justify="center")

    count = 0
    for p in projects:
        count += 1
        # Access attributes safely
        name = getattr(p, "name", "Unknown")
        pid = str(getattr(p, "id", ""))
        runs = str(getattr(p, "run_count", 0))
        # active might not be on all models, check if relevant
        active = "[green]Yes[/green]"

        table.add_row(name, pid, runs, active)

    if count == 0:
        console.print("[yellow]No projects found.[/yellow]")
    else:
        console.print(table)


@projects.command("create")
@click.argument("name")
@click.option("--description", help="Project description.")
@click.pass_context
def create_project(ctx, name, description):
    """Create a new project."""
    client = Client()
    try:
        project = client.create_project(project_name=name, description=description)
        if ctx.obj.get("json"):
            import json

            data = project.dict() if hasattr(project, "dict") else dict(project)
            click.echo(json.dumps(data, default=str))
            return

        console.print(
            f"[green]Created project {project.name}[/green] (ID: {project.id})"
        )
    except Exception as e:
        # Idempotency check or error handling
        if "already exists" in str(e).lower():
            console.print(f"[yellow]Project {name} already exists.[/yellow]")
        else:
            raise e
