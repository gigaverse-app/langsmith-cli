"""Output formatting and rendering utilities."""

import json
from typing import Any, Callable, Protocol

import click


def json_dumps(obj: Any, **kwargs: Any) -> str:
    """Dump object to JSON string with Unicode preservation.

    By default, Python's json.dumps() escapes non-ASCII characters (Hebrew, Chinese, etc.)
    as Unicode escape sequences (\u05ea). This function ensures all characters are
    preserved in their original form.

    Args:
        obj: Object to serialize to JSON
        **kwargs: Additional arguments passed to json.dumps()

    Returns:
        JSON string with Unicode characters preserved
    """
    # Set ensure_ascii=False to preserve Unicode characters
    # Set default to allow datetime and other non-serializable types
    return json.dumps(obj, ensure_ascii=False, default=str, **kwargs)


def is_machine_readable_output(
    ctx: click.Context,
    *,
    output: str | None = None,
    output_format: str | None = None,
    count: bool = False,
    fields: str | None = None,
) -> bool:
    """Return True when the command should route diagnostics to stderr.

    Any of these signals means stdout is being consumed by a machine:
    - global --json flag
    - explicit non-table --format (json/csv/yaml)
    - --output writing data to a file (diagnostics must not corrupt stdout)
    - --count returning a single integer
    - --fields filtering JSON output (caller is post-processing)

    Centralizing the rule prevents drift between commands that compute it inline.
    """
    if ctx.obj.get("json"):
        return True
    if output:
        return True
    if count:
        return True
    if fields:
        return True
    if output_format and output_format != "table":
        return True
    return False


def configure_logger_streams(
    ctx: click.Context,
    logger: Any,
    *,
    output: str | None = None,
    output_format: str | None = None,
    count: bool = False,
    fields: str | None = None,
) -> bool:
    """Set logger.use_stderr when output is machine-readable.

    See :func:`is_machine_readable_output` for the full set of signals.
    """
    use_stderr = is_machine_readable_output(
        ctx,
        output=output,
        output_format=output_format,
        count=count,
        fields=fields,
    )
    logger.use_stderr = use_stderr
    return use_stderr


class ConsoleProtocol(Protocol):
    """Protocol for Rich Console interface - avoids heavy import."""

    def print(self, *args: Any, **kwargs: Any) -> None:
        """Print to console."""
        ...


def output_formatted_data(
    data: list[dict[str, Any]],
    format_type: str,
    *,
    fields: list[str] | None = None,
) -> None:
    """Output data in the specified format (json, csv, yaml).

    Args:
        data: List of dictionaries to output.
              Any is acceptable - JSON values can be str, int, bool, datetime, nested dicts, etc.
        format_type: Output format ("json", "csv", "yaml")
        fields: Optional list of fields to include (for field filtering)
    """
    if not data:
        # Handle empty data case
        if format_type == "csv":
            # CSV with no data - just output empty
            return
        elif format_type == "yaml":
            import yaml

            click.echo(yaml.dump([], default_flow_style=False))
            return
        elif format_type == "json":
            click.echo(json_dumps([]))
            return

    # Apply field filtering if requested
    if fields:
        data = [{k: v for k, v in item.items() if k in fields} for item in data]

    if format_type == "json":
        click.echo(json_dumps(data))
    elif format_type == "csv":
        import csv
        import sys

        writer = csv.DictWriter(sys.stdout, fieldnames=data[0].keys())
        writer.writeheader()
        writer.writerows(data)
    elif format_type == "yaml":
        import yaml

        click.echo(yaml.dump(data, default_flow_style=False, sort_keys=False))
    else:
        raise ValueError(f"Unsupported format: {format_type}")


def determine_output_format(
    output_format: str | None,
    json_flag: bool,
) -> str:
    """Determine the output format to use.

    Args:
        output_format: Explicitly requested format (None if not specified)
        json_flag: Whether --json global flag was used

    Returns:
        Format to use ("json", "csv", "yaml", or "table")
    """
    if output_format:
        return output_format
    return "json" if json_flag else "table"


def print_empty_result_message(console: ConsoleProtocol, item_type: str) -> None:
    """Print a standardized message when no results are found.

    Args:
        console: Rich console for printing
        item_type: Type of item (e.g., "runs", "projects", "datasets")
    """
    console.print(f"[yellow]No {item_type} found.[/yellow]")


def safe_model_dump(
    obj: Any, include: set[str] | None = None, mode: str = "json"
) -> dict[str, Any]:
    """Safely serialize Pydantic models to dict (handles v1 and v2).

    Args:
        obj: Pydantic model instance or dict
        include: Optional set of fields to include
        mode: Serialization mode ("json" for JSON-compatible output)

    Returns:
        Dictionary representation suitable for JSON serialization
    """
    # Pydantic v2
    if hasattr(obj, "model_dump"):
        return obj.model_dump(include=include, mode=mode)
    # Pydantic v1
    elif hasattr(obj, "dict"):
        result = obj.dict()
        if include:
            return {k: v for k, v in result.items() if k in include}
        return result
    # Already a dict
    elif isinstance(obj, dict):
        if include:
            return {k: v for k, v in obj.items() if k in include}
        return obj
    # Fallback
    return dict(obj)


def render_output(
    data: list[Any] | Any,
    table_builder: Callable[[list[Any]], Any] | None,
    ctx: Any,
    *,
    include_fields: set[str] | None = None,
    empty_message: str = "No results found",
    output_format: str | None = None,
    count_flag: bool = False,
    output_path: str | None = None,
) -> None:
    """Unified output renderer for all output formats (JSON, CSV, YAML, Table).

    This function standardizes output across all commands, eliminating
    the repetitive "if json else table" pattern.

    Args:
        data: List of items or single item to render
        table_builder: Function that takes data and returns a Rich Table
                      (None if data is already a table or for JSON-only)
        ctx: Click context (contains json flag)
        include_fields: Optional set of fields to include in output
        empty_message: Message to show when data is empty
        output_format: Explicit format override ("json", "csv", "yaml", "table")
        count_flag: If True, output only the count (integer)

    Example:
        def build_table(projects):
            table = Table(title="Projects")
            table.add_column("Name")
            for p in projects:
                table.add_row(p.name)
            return table

        render_output(projects_list, build_table, ctx,
                     include_fields={"name", "id"},
                     empty_message="No projects found")
    """
    from rich.console import Console

    # Normalize to list
    items = data if isinstance(data, list) else [data] if data else []

    # Handle count mode - short circuit all other output
    if count_flag:
        click.echo(str(len(items)))
        return

    # Determine output format
    format_type = determine_output_format(output_format, ctx.obj.get("json"))

    # File output respects --format. Tables aren't sensible in files, so fall
    # back to JSONL (the historical default) when --format is table or unset.
    if output_path:
        serialized = [safe_model_dump(item, include=include_fields) for item in items]
        file_format = "jsonl" if format_type == "table" else format_type
        write_output_to_file(
            serialized, output_path, Console(), format_type=file_format
        )
        return

    # Handle non-table formats (JSON, CSV, YAML)
    if format_type != "table":
        serialized = [safe_model_dump(item, include=include_fields) for item in items]
        output_formatted_data(
            serialized,
            format_type,
            fields=list(include_fields) if include_fields else None,
        )
        return

    # Table output mode
    console = Console()
    if not items:
        console.print(f"[yellow]{empty_message}[/yellow]")
        return

    # Build and print table
    if table_builder:
        console.print(table_builder(items))
    else:
        # Data is already a table or printable object
        console.print(data)


def write_output_to_file(
    data: list[dict[str, Any]] | dict[str, Any],
    output_path: str,
    console: ConsoleProtocol,
    *,
    format_type: str = "jsonl",
) -> None:
    """Write data to a file with error handling and user feedback.

    Universal helper for --output flag across all commands.
    Supports both list of dicts (for list commands) and single dicts (for get commands).

    Args:
        data: Dictionary or list of dictionaries to write.
              Any is acceptable - JSON values can be str, int, bool, datetime, nested dicts, etc.
        output_path: Path to write file to
        console: Rich console for user feedback
        format_type: Output format ("jsonl" for newline-delimited JSON, "json" for JSON array/object)

    Raises:
        click.ClickException: If file writing fails

    Example:
        # List output
        write_output_to_file(
            [{"id": "123", "name": "test"}],
            "output.jsonl",
            console,
            format_type="jsonl"
        )
        # Single item output
        write_output_to_file(
            {"id": "123", "name": "test"},
            "output.json",
            console,
            format_type="json"
        )
    """
    is_single = isinstance(data, dict)
    try:
        with open(output_path, "w", encoding="utf-8") as f:
            if is_single:
                # Single item - always write as JSON object
                f.write(json_dumps(data))
            elif format_type == "jsonl":
                # Write as newline-delimited JSON (one object per line)
                for item in data:
                    f.write(json_dumps(item) + "\n")
            elif format_type == "json":
                # Write as JSON array
                f.write(json_dumps(data))
            elif format_type == "yaml":
                import yaml

                yaml.dump(data, f, default_flow_style=False, sort_keys=False)
            elif format_type == "csv":
                import csv

                if data:
                    writer = csv.DictWriter(f, fieldnames=list(data[0].keys()))
                    writer.writeheader()
                    writer.writerows(data)
            else:
                raise ValueError(f"Unsupported format_type: {format_type}")

        # Diagnostic messages go to stderr to avoid corrupting piped stdout in JSON mode
        from rich.console import Console as RichConsole

        stderr_console = RichConsole(stderr=True)
        if is_single:
            stderr_console.print(f"[green]Wrote item to {output_path}[/green]")
        else:
            stderr_console.print(
                f"[green]Wrote {len(data)} items to {output_path}[/green]"
            )
    except (OSError, TypeError, ValueError) as e:
        from rich.console import Console as RichConsole

        stderr_console = RichConsole(stderr=True)
        stderr_console.print(f"[red]Error writing to file {output_path}: {e}[/red]")
        raise click.ClickException(f"Error writing to file {output_path}: {e}") from e


def emit_action_result(
    ctx: click.Context,
    logger: Any,
    *,
    model: Any = None,
    payload: dict[str, Any] | None = None,
    success_message: str,
) -> None:
    """Emit the result of a create/update/delete-style command.

    Every mutating command in the CLI has the same two-branch shape: in
    machine mode (``--json``) it dumps a structured payload; in human mode it
    writes a single rich success line. Routing both branches through this
    helper prevents future commands from forgetting one half (the most
    common drift mode — usually the JSON branch).

    Args:
        ctx: Click context (inspected for the ``--json`` flag).
        logger: CLILogger used for the human-mode success message.
        model: Optional Pydantic model to dump in JSON mode. If provided,
            takes precedence over ``payload`` and is serialized via
            :func:`safe_model_dump`. Use this for create/update commands
            that return an SDK entity.
        payload: Optional plain dict to emit in JSON mode. Use this for
            delete commands or other operations without a model return,
            e.g. ``{"status": "success", "deleted": id}``.
        success_message: Human-readable confirmation line written via
            ``logger.success()`` when ``--json`` is not active.

    Raises:
        ValueError: If neither ``model`` nor ``payload`` is provided.
    """
    if model is None and payload is None:
        raise ValueError("emit_action_result requires either model= or payload=.")

    if ctx.obj.get("json"):
        data = safe_model_dump(model) if model is not None else payload
        click.echo(json_dumps(data))
        return
    logger.success(success_message)


def output_single_item(
    ctx: click.Context,
    data: dict[str, Any],
    console: ConsoleProtocol,
    *,
    output: str | None = None,
    render_fn: Callable[[dict[str, Any], ConsoleProtocol], None] | None = None,
) -> None:
    """Output a single item (dict) in the appropriate format.

    Shared helper for all get/get-latest commands. Handles:
    - --output FILE: write JSON to file
    - --json: echo JSON to stdout
    - Rich rendering: call render_fn for human-readable output

    Args:
        ctx: Click context (checked for json flag)
        data: Filtered dict from filter_fields() or manual dict construction
        console: Rich console for output
        output: Optional file path for --output flag
        render_fn: Optional function to render rich output. If None, falls back to
                   printing the JSON-formatted dict.
    """
    if output:
        write_output_to_file(data, output, console, format_type="json")
        return

    if ctx.obj.get("json"):
        click.echo(json_dumps(data))
        return

    # Human-readable output
    if render_fn:
        render_fn(data, console)
    else:
        # Default: pretty-print the JSON
        from rich.syntax import Syntax

        console.print(Syntax(json_dumps(data, indent=2), "json"))


def output_option(
    help_text: str = "Write output to file instead of stdout. List commands default to JSONL; combine with --format json/csv/yaml to choose another file format.",
) -> Any:
    """Reusable Click option decorator for --output flag.

    Use this decorator on all bulk commands to provide consistent file output.

    Args:
        help_text: Custom help text for the option

    Returns:
        Click option decorator

    Example:
        @click.command()
        @output_option()
        @click.pass_context
        def list_items(ctx, output):
            client = get_or_create_client(ctx)
            items = list(client.list_items())
            data = filter_fields(items, fields)

            if output:
                from rich.console import Console
                console = Console()
                write_output_to_file(data, output, console, format_type="jsonl")
            else:
                click.echo(json_dumps(data))
    """
    return click.option(
        "--output",
        type=str,
        default=None,
        help=help_text,
    )
