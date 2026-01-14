import sys
import json as json_lib
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


class LangSmithCLIGroup(click.Group):
    """Custom Click Group that handles LangSmith exceptions gracefully."""

    def invoke(self, ctx):
        """Override invoke to catch and handle LangSmith exceptions."""
        try:
            return super().invoke(ctx)
        except Exception as e:
            # Import SDK exceptions inside handler (lazy loading)
            from langsmith.utils import (
                LangSmithAuthError,
                LangSmithNotFoundError,
                LangSmithConflictError,
                LangSmithError,
            )

            # Get JSON mode from context
            json_mode = ctx.obj.get("json", False) if ctx.obj else False

            # Handle specific exception types with friendly messages
            if isinstance(e, LangSmithAuthError):
                error_msg = "Authentication failed. Your API key is missing or invalid."
                help_msg = "Run 'langsmith-cli auth login' to configure your API key."

                if json_mode:
                    error_data = {
                        "error": "AuthenticationError",
                        "message": error_msg,
                        "help": help_msg,
                    }
                    click.echo(json_lib.dumps(error_data))
                else:
                    console.print(f"[red]Error:[/red] {error_msg}")
                    console.print(f"[yellow]→[/yellow] {help_msg}")

                sys.exit(1)

            elif isinstance(e, LangSmithNotFoundError):
                error_msg = str(e)
                if json_mode:
                    error_data = {"error": "NotFoundError", "message": error_msg}
                    click.echo(json_lib.dumps(error_data))
                else:
                    console.print(f"[red]Error:[/red] {error_msg}")
                sys.exit(1)

            elif isinstance(e, LangSmithConflictError):
                error_msg = str(e)
                if json_mode:
                    error_data = {"error": "ConflictError", "message": error_msg}
                    click.echo(json_lib.dumps(error_data))
                else:
                    console.print(f"[yellow]Warning:[/yellow] {error_msg}")
                # Don't exit for conflicts - they're often non-fatal
                return

            elif isinstance(e, LangSmithError):
                # Generic LangSmith error
                error_msg = str(e)
                if json_mode:
                    error_data = {"error": "LangSmithError", "message": error_msg}
                    click.echo(json_lib.dumps(error_data))
                else:
                    console.print(f"[red]Error:[/red] {error_msg}")
                sys.exit(1)

            else:
                # Unexpected error - re-raise for debugging
                raise


@click.group(cls=LangSmithCLIGroup)
@click.version_option()
@click.option("--json", is_flag=True, help="Output strict JSON for agents.")
@click.pass_context
def cli_main(ctx, json):
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
cli_main.add_command(auth)
cli_main.add_command(projects)
cli_main.add_command(runs)
cli_main.add_command(datasets)
cli_main.add_command(examples)
cli_main.add_command(prompts)

# Backwards compatibility alias
cli = cli_main

if __name__ == "__main__":
    cli_main()
