import click
from rich.console import Console
from rich.table import Table
import langsmith

console = Console()


@click.group()
def projects():
    """Manage LangSmith projects."""
    pass


@projects.command("list")
@click.option("--limit", default=100, help="Limit number of projects (default 100).")
@click.pass_context
def list_projects(ctx, limit):
    """List all projects."""
    client = langsmith.Client()
    # list_projects returns a generator
    projects_gen = client.list_projects(limit=limit)

    # Materialize the list to count and process
    projects_list = list(projects_gen)

    if ctx.obj.get("json"):
        import json

        # Handle generator return from list_projects
        data = [p.dict() if hasattr(p, "dict") else dict(p) for p in projects_list]
        click.echo(json.dumps(data, default=str))  # default=str for datetimes
        return

    table = Table(title="Projects")
    table.add_column("Name", style="cyan")
    table.add_column("ID", style="dim")
    table.add_column("Runs", justify="right")
    table.add_column("Type", justify="center")

    for p in projects_list:
        # Access attributes safely
        name = getattr(p, "name", "Unknown")
        pid = str(getattr(p, "id", ""))

        # Correctly handle None or missing run_count
        runs_val = getattr(p, "run_count", 0)
        runs = str(runs_val) if runs_val is not None else "0"

        p_type = getattr(p, "project_type", "tracer")

        table.add_row(name, pid, runs, p_type)

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
    client = langsmith.Client()
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
