import click
from rich.console import Console
from dotenv import load_dotenv
from langsmith_cli.commands.auth import login
from langsmith_cli.commands.projects import projects
from langsmith_cli.commands.runs import runs
from langsmith_cli.commands.datasets import datasets
from langsmith_cli.commands.examples import examples
from langsmith_cli.commands.prompts import prompts

# Load .env file from current directory
load_dotenv()

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
cli.add_command(runs)
cli.add_command(datasets)
cli.add_command(examples)
cli.add_command(prompts)


if __name__ == "__main__":
    cli()
