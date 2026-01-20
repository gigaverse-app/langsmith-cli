import sys
import json as json_lib
import os
import click
from rich.console import Console
from dotenv import load_dotenv
from langsmith_cli.commands.auth import login
from langsmith_cli.commands.projects import projects
from langsmith_cli.commands.runs import runs
from langsmith_cli.commands.datasets import datasets
from langsmith_cli.commands.examples import examples
from langsmith_cli.commands.prompts import prompts
from langsmith_cli.config import get_credentials_file

# Load credentials with priority order:
# 1. Environment variable LANGSMITH_API_KEY (already loaded if set)
# 2. User config directory (~/.config/langsmith-cli/credentials or platform equivalent)
# 3. Current working directory .env file (backward compatibility)

if "LANGSMITH_API_KEY" not in os.environ:
    # Try loading from user config directory first
    config_file = get_credentials_file()
    if config_file.exists():
        load_dotenv(config_file)

# Try loading from CWD .env as fallback (backward compatibility)
if "LANGSMITH_API_KEY" not in os.environ:
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
                # Generic LangSmith error - check if it's a 403 Forbidden
                error_str = str(e)

                # Check for 403 Forbidden errors (invalid/expired API key)
                if "403" in error_str or "Forbidden" in error_str:
                    error_msg = (
                        "Access forbidden. Your API key may be invalid or expired."
                    )
                    help_msg = "Run 'langsmith-cli auth login' to update your API key."

                    if json_mode:
                        error_data = {
                            "error": "PermissionError",
                            "message": error_msg,
                            "help": help_msg,
                            "details": error_str,
                        }
                        click.echo(json_lib.dumps(error_data))
                    else:
                        console.print(f"[red]Error:[/red] {error_msg}")
                        console.print(f"[yellow]→[/yellow] {help_msg}")
                        console.print(
                            f"[dim]Details: {error_str if len(error_str) < 200 else error_str[:200] + '...'}[/dim]"
                        )

                    sys.exit(1)

                # Check for 401 Unauthorized (catches cases not caught by LangSmithAuthError)
                elif "401" in error_str or "Unauthorized" in error_str:
                    error_msg = (
                        "Authentication failed. Your API key is missing or invalid."
                    )
                    help_msg = (
                        "Run 'langsmith-cli auth login' to configure your API key."
                    )

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

                # Other LangSmith errors
                else:
                    if json_mode:
                        error_data = {"error": "LangSmithError", "message": error_str}
                        click.echo(json_lib.dumps(error_data))
                    else:
                        console.print(f"[red]Error:[/red] {error_str}")
                    sys.exit(1)

            else:
                # Unexpected error - re-raise for debugging
                raise
        finally:
            # Flush stdout to prevent data loss when piping to other processes
            # This fixes race conditions where buffered output may not reach the pipe
            sys.stdout.flush()


@click.group(cls=LangSmithCLIGroup)
@click.version_option()
@click.option("--json", is_flag=True, help="Output strict JSON for agents.")
@click.option(
    "--verbose",
    "-v",
    count=True,
    help="Increase verbosity (-v: DEBUG, -vv: TRACE)",
)
@click.option(
    "--quiet",
    "-q",
    count=True,
    help="Decrease verbosity (-q: warnings only, -qq: errors only)",
)
@click.pass_context
def cli_main(ctx, json, verbose, quiet):
    """
    LangSmith CLI - A context-efficient interface for LangSmith.
    """
    ctx.ensure_object(dict)
    ctx.obj["json"] = json

    # Initialize logger with verbosity level
    from langsmith_cli.logging import CLILogger, Verbosity

    # Determine if using machine-readable mode
    # (will be refined in commands when --format/--count/--output is known)
    is_machine_readable = json

    # Map verbose/quiet counts to logging level
    # Start with default INFO (20), adjust by verbose/quiet
    if quiet >= 2:
        # -qq: Only errors
        verbosity_level = Verbosity.ERROR
    elif quiet == 1:
        # -q: Warnings + errors (no progress)
        verbosity_level = Verbosity.WARNING
    elif verbose == 0:
        # Default: INFO level (progress + warnings + errors)
        verbosity_level = Verbosity.INFO
    elif verbose == 1:
        # -v: DEBUG level (debug details)
        verbosity_level = Verbosity.DEBUG
    else:
        # -vv or more: TRACE level (ultra-verbose)
        verbosity_level = Verbosity.TRACE

    # Create and store logger
    ctx.obj["logger"] = CLILogger(
        verbosity=verbosity_level, use_stderr=is_machine_readable
    )


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
