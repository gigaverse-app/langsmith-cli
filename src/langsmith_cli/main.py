import click
from rich.console import Console
from langsmith_cli.commands.auth import login
from langsmith_cli.commands.projects import projects

console = Console()


@click.group()
@click.version_option()
@click.option("--json", is_flag=True, help="Output strict JSON for agents.")
@click.pass_context
def cli(ctx, json):
    """
    LangSmith CLI - A context-efficient interface for LangSmith.
    """
    ctx.ensure_object(dict)
    ctx.obj["json"] = json


@click.group()
def auth():
    """Manage authentication."""
    pass


auth.add_command(login)
cli.add_command(auth)
cli.add_command(projects)


if __name__ == "__main__":
    cli()
