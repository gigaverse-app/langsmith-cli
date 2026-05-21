import sys
import json as json_lib
import os
from typing import Any
import click
from dotenv import load_dotenv
from langsmith_cli.commands.annotation_queues import annotation_queues
from langsmith_cli.commands.auth import login
from langsmith_cli.commands.datasets import datasets
from langsmith_cli.commands.examples import examples
from langsmith_cli.commands.experiments import experiments
from langsmith_cli.commands.feedback import feedback
from langsmith_cli.commands.projects import projects
from langsmith_cli.commands.prompts import prompts
from langsmith_cli.commands.runs import runs
from langsmith_cli.commands.self_cmd import self_group
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


def _http_status_from_exception(exc: BaseException) -> int | None:
    """Extract an HTTP status from SDK/http exception chains when available."""
    import httpx
    import requests

    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))

        if isinstance(current, (httpx.HTTPStatusError, requests.HTTPError)):
            response = current.response
            if response is not None:
                return response.status_code

        current = current.__cause__ or current.__context__
    return None


def _get_console() -> Any:
    from rich.console import Console

    return Console()


def _is_json_mode(ctx: click.Context) -> bool:
    if ctx.obj is None:
        return False
    if "json" not in ctx.obj:
        return False
    return bool(ctx.obj["json"])


def _close_cached_client(ctx: click.Context) -> None:
    if ctx.obj is None:
        return
    if "client" not in ctx.obj:
        return
    ctx.obj["client"].close()


def _command_path_for_ctx(ctx: click.Context) -> str:
    """Reconstruct the user-facing command path from a Click context.

    Used to stamp the ``command`` field on structured JSON errors. We walk
    up the context chain so that nested subcommand groups (``runs cache
    grep``) are reported in full instead of just the leaf name.
    """
    parts: list[str] = []
    cur: click.Context | None = ctx
    while cur is not None:
        if cur.info_name:
            parts.append(cur.info_name)
        cur = cur.parent
    return " ".join(reversed(parts))


def _command_path_from_args(
    root_name: str | None,
    root_command: click.Command,
    args: list[str],
) -> str:
    """Infer nested command path from argv tokens before invocation."""
    parts = [root_name or "langsmith-cli"]
    command = root_command
    for token in args:
        if token.startswith("-"):
            continue
        if not isinstance(command, click.Group):
            break
        if token not in command.commands:
            break
        parts.append(token)
        command = command.commands[token]
    return " ".join(parts)


def _command_path_from_exception(exc: BaseException) -> str | None:
    """Recover the deepest subcommand path by inspecting an exception's
    traceback frames.

    By the time our top-level Group.invoke catches the error, Click has
    already popped the leaf context, so ``click.get_current_context()``
    only sees the root group. The traceback still has the failed
    subcommand's stack frame, which carries Click's ``ctx`` local —
    that's where we recover the full ``runs cache grep`` path from.

    Returns None when no frame carries a Click Context (e.g. for errors
    raised before any subcommand was entered).
    """
    deepest: click.Context | None = None
    tb = exc.__traceback__
    while tb is not None:
        for value in tb.tb_frame.f_locals.values():
            if isinstance(value, click.Context):
                # Pick the *deepest* (most-nested) ctx we see. We keep
                # walking so a wrapping group ctx doesn't overwrite the
                # leaf subcommand ctx.
                if deepest is None or _ctx_depth(value) >= _ctx_depth(deepest):
                    deepest = value
        tb = tb.tb_next
    return _command_path_for_ctx(deepest) if deepest is not None else None


def _ctx_depth(ctx: click.Context) -> int:
    """Number of ancestors above this context (root = 0)."""
    depth = 0
    cur = ctx.parent
    while cur is not None:
        depth += 1
        cur = cur.parent
    return depth


class LangSmithCLIGroup(click.Group):
    """Custom Click Group that handles LangSmith exceptions gracefully."""

    def parse_args(self, ctx, args):
        # Allow --json anywhere in the command, not just before the subcommand.
        # Click only parses root-group flags before the subcommand name, so we
        # hoist --json to the front before normal parsing begins.
        if "--json" in args:
            args = ["--json"] + [a for a in args if a != "--json"]
        ctx.meta["command_path"] = _command_path_from_args(ctx.info_name, self, args)
        return super().parse_args(ctx, args)

    def invoke(self, ctx):
        """Override invoke to catch and handle LangSmith exceptions."""
        try:
            return super().invoke(ctx)
        except Exception as e:
            # Tag every JSON error payload with the command path so callers
            # (especially agents) can correlate errors with what they ran.
            # Click pops the inner ctx before the exception reaches us, so
            # walking ``click.get_current_context()`` here only sees the
            # root group. Walk the *traceback* instead — the deepest frame
            # with a Click ``Context`` local is the subcommand that blew up.
            command_path = str(
                ctx.meta.get("command_path")
                or _command_path_from_exception(e)
                or _command_path_for_ctx(ctx)
            )

            def _emit_error(payload: dict[str, Any]) -> None:
                """Stamp ``command`` and emit a structured error payload.

                Centralizing the dump means new fields (e.g. ``partial``)
                can be added once instead of at every branch below.
                """
                payload.setdefault("command", command_path)
                click.echo(json_lib.dumps(payload))

            # Import SDK exceptions inside handler (lazy loading)
            from langsmith.utils import (
                LangSmithAuthError,
                LangSmithNotFoundError,
                LangSmithConflictError,
                LangSmithError,
            )

            # Get JSON mode from context
            json_mode = _is_json_mode(ctx)

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
                    _emit_error(error_data)
                else:
                    console = _get_console()
                    console.print(f"[red]Error:[/red] {error_msg}")
                    console.print(f"[yellow]→[/yellow] {help_msg}")

                sys.exit(1)

            elif isinstance(e, LangSmithNotFoundError):
                error_msg = str(e)
                if json_mode:
                    error_data = {"error": "NotFoundError", "message": error_msg}
                    _emit_error(error_data)
                else:
                    console = _get_console()
                    console.print(f"[red]Error:[/red] {error_msg}")
                sys.exit(1)

            elif isinstance(e, LangSmithConflictError):
                error_msg = str(e)
                if json_mode:
                    error_data = {"error": "ConflictError", "message": error_msg}
                    _emit_error(error_data)
                else:
                    console = _get_console()
                    console.print(f"[yellow]Warning:[/yellow] {error_msg}")
                # Don't exit for conflicts - they're often non-fatal
                return

            elif isinstance(e, LangSmithError):
                error_str = str(e)
                status_code = _http_status_from_exception(e)

                if status_code == 403:
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
                        _emit_error(error_data)
                    else:
                        console = _get_console()
                        console.print(f"[red]Error:[/red] {error_msg}")
                        console.print(f"[yellow]→[/yellow] {help_msg}")
                        console.print(
                            f"[dim]Details: {error_str if len(error_str) < 200 else error_str[:200] + '...'}[/dim]"
                        )

                    sys.exit(1)

                elif status_code == 401:
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
                        _emit_error(error_data)
                    else:
                        console = _get_console()
                        console.print(f"[red]Error:[/red] {error_msg}")
                        console.print(f"[yellow]→[/yellow] {help_msg}")

                    sys.exit(1)

                # Other LangSmith errors
                else:
                    if json_mode:
                        error_data = {"error": "LangSmithError", "message": error_str}
                        _emit_error(error_data)
                    else:
                        console = _get_console()
                        console.print(f"[red]Error:[/red] {error_str}")
                    sys.exit(1)

            else:
                # Non-LangSmith error (Click exceptions, Python exceptions, etc.)
                from langsmith_cli.utils import CLIFetchError

                if json_mode:
                    # In JSON mode, ALWAYS output structured JSON to stdout.
                    # Empty stdout breaks piped JSON parsing (json.loads fails).
                    if isinstance(e, CLIFetchError):
                        # Structured error with failure details and suggestions
                        error_data: dict[str, Any] = {
                            "error": "FetchError",
                            "message": e.format_message(),
                            "failed_sources": [
                                {"name": n, "error": err} for n, err in e.failed_sources
                            ],
                            "suggestions": e.suggestions,
                        }
                        exit_code = e.exit_code
                    elif isinstance(e, click.ClickException):
                        error_data = {
                            "error": type(e).__name__,
                            "message": e.format_message(),
                        }
                        exit_code = e.exit_code
                    else:
                        error_data = {
                            "error": type(e).__name__,
                            "message": str(e),
                        }
                        exit_code = 1
                    _emit_error(error_data)
                    sys.exit(exit_code)
                else:
                    # In human mode, re-raise for Click's default formatting
                    raise
        finally:
            try:
                _close_cached_client(ctx)
            finally:
                # Flush stdout to prevent data loss when piping to other processes.
                sys.stdout.flush()


@click.group(cls=LangSmithCLIGroup)
@click.version_option(package_name="langsmith-cli")
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

    \b
    AGENT QUICK START:
      langsmith-cli self skill              # Full usage guide (read this first)
      langsmith-cli self skill --list       # List all reference docs
      langsmith-cli self skill runs         # Runs/traces reference
      langsmith-cli self skill fql          # Filter Query Language reference

    \b
    Pass --json anywhere for machine-readable output:
      langsmith-cli --json runs list --limit 5
      langsmith-cli runs list --limit 5 --json
    """
    ctx.ensure_object(dict)
    ctx.obj["json"] = json

    # Initialize logger with verbosity level
    from langsmith_cli.cli_logging import CLILogger, Verbosity

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
cli_main.add_command(annotation_queues)
cli_main.add_command(datasets)
cli_main.add_command(examples)
cli_main.add_command(experiments)
cli_main.add_command(feedback)
cli_main.add_command(projects)
cli_main.add_command(prompts)
cli_main.add_command(runs)
cli_main.add_command(self_group, "self")

# Backwards compatibility alias
cli = cli_main

if __name__ == "__main__":
    cli_main()
