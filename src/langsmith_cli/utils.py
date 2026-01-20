"""Utility functions shared across commands."""

from typing import TYPE_CHECKING, Any, Callable, Generic, Protocol, TypeVar, overload
import click
import json
import langsmith
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from langsmith.schemas import Run

T = TypeVar("T")
ModelT = TypeVar("ModelT", bound=BaseModel)


class FetchResult(BaseModel, Generic[T]):
    """Result of fetching items from multiple projects/sources.

    Tracks both successful items and failed sources for proper error reporting.
    """

    model_config = {"arbitrary_types_allowed": True}

    items: list[T]
    successful_sources: list[str]
    failed_sources: list[tuple[str, str]] = Field(
        default_factory=list, description="(source_name, error_message)"
    )

    @property
    def has_failures(self) -> bool:
        """Check if any sources failed."""
        return len(self.failed_sources) > 0

    @property
    def all_failed(self) -> bool:
        """Check if ALL sources failed (no successful sources).

        This indicates a complete failure - the user should be notified
        and the CLI should return a non-zero exit code.
        """
        return len(self.successful_sources) == 0 and len(self.failed_sources) > 0

    @property
    def total_sources(self) -> int:
        """Total number of sources attempted."""
        return len(self.successful_sources) + len(self.failed_sources)

    def report_failures(self, console: Any, max_show: int = 3) -> None:
        """Report failures to console.

        Args:
            console: Console object (Rich Console or ConsoleProtocol)
            max_show: Maximum number of failures to show (default 3)
        """
        if not self.has_failures:
            return

        console.print("[yellow]Warning: Some sources failed to fetch:[/yellow]")
        for source, error_msg in self.failed_sources[:max_show]:
            # Truncate long error messages
            short_error = error_msg[:100] + "..." if len(error_msg) > 100 else error_msg
            console.print(f"  • {source}: {short_error}")

        if len(self.failed_sources) > max_show:
            remaining = len(self.failed_sources) - max_show
            console.print(f"  ... and {remaining} more")

    def report_failures_to_logger(self, logger: Any, max_show: int = 3) -> None:
        """Report failures using the CLI logger.

        Use this instead of report_failures() when you need proper
        stdout/stderr separation (e.g., in JSON mode).

        Args:
            logger: CLILogger instance
            max_show: Maximum number of failures to show (default 3)
        """
        if not self.has_failures:
            return

        if self.all_failed:
            logger.error("All sources failed to fetch:")
        else:
            logger.warning("Some sources failed to fetch:")

        for source, error_msg in self.failed_sources[:max_show]:
            # Truncate long error messages
            short_error = error_msg[:100] + "..." if len(error_msg) > 100 else error_msg
            logger.warning(f"  • {source}: {short_error}")

        if len(self.failed_sources) > max_show:
            remaining = len(self.failed_sources) - max_show
            logger.warning(f"  ... and {remaining} more")

    def raise_if_all_failed(
        self,
        logger: Any | None = None,
        entity_name: str = "runs",
    ) -> None:
        """Raise ClickException if all sources failed.

        Use this for consistent error handling across commands. This method:
        1. Reports failures to logger (if provided)
        2. Raises ClickException with clear error message

        Args:
            logger: Optional CLILogger for reporting (uses proper stderr in JSON mode)
            entity_name: What we were trying to fetch (e.g., "runs", "datasets")

        Raises:
            click.ClickException: If all sources failed to fetch

        Example:
            result = fetch_from_projects(client, projects, fetch_func)
            result.raise_if_all_failed(logger, "runs")  # Raises if all failed
            # ... continue processing result.items ...
        """
        if not self.all_failed:
            return

        # Report failures if logger provided
        if logger:
            self.report_failures_to_logger(logger)

        raise click.ClickException(
            f"Failed to fetch {entity_name} from all {self.total_sources} source(s). "
            "Check the error messages above."
        )


def fetch_from_projects(
    client: Any,
    project_names: list[str],
    fetch_func: Callable[..., Any],
    *,
    limit: int | None = None,
    console: Any | None = None,
    show_warnings: bool = True,
    **fetch_kwargs: Any,
) -> FetchResult[Any]:
    """Universal helper to fetch items from multiple projects with error tracking.

    Args:
        client: LangSmith client instance
        project_names: List of project names to fetch from
        fetch_func: Function that takes (client, project_name, **kwargs) and returns items
        limit: Optional limit on number of items to fetch per project
        console: Optional console for warnings
        show_warnings: Whether to automatically show warnings (default True)
        **fetch_kwargs: Additional kwargs passed to fetch_func

    Returns:
        FetchResult containing items, successful projects, and failed projects

    Example:
        >>> result = fetch_from_projects(
        ...     client,
        ...     ["proj1", "proj2"],
        ...     lambda c, proj, **kw: c.list_runs(project_name=proj, **kw),
        ...     limit=10,
        ...     console=console
        ... )
        >>> if result.has_failures:
        ...     result.report_failures(console)
    """
    all_items: list[Any] = []
    successful: list[str] = []
    failed: list[tuple[str, str]] = []

    for proj_name in project_names:
        try:
            # Call fetch function with project name and all kwargs
            items = fetch_func(client, proj_name, limit=limit, **fetch_kwargs)

            # Handle iterators (like client.list_runs returns)
            if hasattr(items, "__iter__") and not isinstance(items, (list, tuple)):
                items = list(items)

            all_items.extend(items)
            successful.append(proj_name)
        except Exception as e:
            failed.append((proj_name, str(e)))

    result = FetchResult(
        items=all_items, successful_sources=successful, failed_sources=failed
    )

    # Automatically show warnings if requested
    if show_warnings and result.has_failures and console:
        result.report_failures(console)

    return result


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


def get_or_create_client(ctx: Any) -> Any:
    """Get LangSmith client from context, or create if not exists.

    Note: langsmith module is imported at module level for testability,
    but Client instantiation is still lazy (only created when first needed).

    Args:
        ctx: Click context object

    Returns:
        LangSmith Client instance
    """
    if "client" not in ctx.obj:
        ctx.obj["client"] = langsmith.Client()
    return ctx.obj["client"]


@overload
def filter_fields(data: list[ModelT], fields: str | None) -> list[dict[str, Any]]: ...


@overload
def filter_fields(
    data: ModelT,  # pyright: ignore[reportInvalidTypeVarUse]
    fields: str | None,
) -> dict[str, Any]: ...


def filter_fields(
    data: ModelT | list[ModelT], fields: str | None
) -> dict[str, Any] | list[dict[str, Any]]:
    """Filter Pydantic model fields based on a comma-separated field list.

    Provides universal field filtering for all list/get commands to reduce context usage.

    Args:
        data: Single Pydantic model instance or list of instances
        fields: Comma-separated field names (e.g., "id,name,tags") or None for all fields

    Returns:
        Filtered dict or list of dicts with only the specified fields.
        If fields is None, returns full model dump in JSON-compatible mode.

    Examples:
        >>> from langsmith.schemas import Dataset
        >>> dataset = Dataset(id=uuid4(), name="test", ...)
        >>> filter_fields(dataset, "id,name")
        {"id": "...", "name": "test"}

        >>> datasets = [Dataset(...), Dataset(...)]
        >>> filter_fields(datasets, "id,name")
        [{"id": "...", "name": "test"}, {"id": "...", "name": "test2"}]

        >>> filter_fields(datasets, None)  # Return all fields
        [{"id": "...", "name": "...", "description": "...", ...}, ...]
    """
    if fields is None:
        # Return full model dump
        if isinstance(data, list):
            return [item.model_dump(mode="json") for item in data]
        return data.model_dump(mode="json")

    # Parse field names
    field_set = {f.strip() for f in fields.split(",") if f.strip()}

    if isinstance(data, list):
        return [item.model_dump(include=field_set, mode="json") for item in data]
    return data.model_dump(include=field_set, mode="json")


def fields_option(
    help_text: str = "Comma-separated field names to include in output (e.g., 'id,name,created_at'). Reduces context usage by omitting unnecessary fields.",
) -> Any:
    """Reusable Click option decorator for --fields flag.

    Use this decorator on all list/get commands to provide consistent field filtering.

    Args:
        help_text: Custom help text for the option

    Returns:
        Click option decorator

    Example:
        @click.command()
        @fields_option()
        @click.pass_context
        def list_items(ctx, fields):
            client = get_or_create_client(ctx)
            items = list(client.list_items())
            data = filter_fields(items, fields)
            click.echo(json.dumps(data, default=str))
    """
    return click.option(
        "--fields",
        type=str,
        default=None,
        help=help_text,
    )


def count_option(
    help_text: str = "Output only the count of results (integer). Useful for scripting and quick checks.",
) -> Any:
    """Reusable Click option decorator for --count flag.

    Use this decorator on all list commands to provide consistent count output.
    When enabled, outputs only an integer count instead of full results.

    Args:
        help_text: Custom help text for the option

    Returns:
        Click option decorator

    Example:
        @click.command()
        @count_option()
        @click.pass_context
        def list_items(ctx, count):
            client = get_or_create_client(ctx)
            items = list(client.list_items())
            if count:
                click.echo(str(len(items)))
                return
            # ... normal output
    """
    return click.option(
        "--count",
        is_flag=True,
        default=False,
        help=help_text,
    )


def exclude_option(
    help_text: str = "Exclude items containing this substring (can be specified multiple times). Case-sensitive.",
) -> Any:
    """Reusable Click option decorator for --exclude flag.

    Use this decorator on all list commands with name filtering to provide
    consistent exclusion filtering. Can be specified multiple times.

    Args:
        help_text: Custom help text for the option

    Returns:
        Click option decorator

    Example:
        @click.command()
        @exclude_option()
        @click.pass_context
        def list_items(ctx, exclude):
            client = get_or_create_client(ctx)
            items = list(client.list_items())
            items = apply_exclude_filter(items, exclude, lambda i: i.name)
            # ... render output
    """
    return click.option(
        "--exclude",
        multiple=True,
        default=(),
        help=help_text,
    )


def apply_exclude_filter(
    items: list[T],
    exclude_patterns: tuple[str, ...],
    name_getter: Callable[[T], str],
) -> list[T]:
    """Apply exclusion filters to a list of items.

    Filters out items whose names contain any of the exclude patterns.
    Uses simple substring matching (case-sensitive).

    Args:
        items: List of items to filter
        exclude_patterns: Tuple of patterns to exclude
        name_getter: Function to get name from item

    Returns:
        Filtered list of items

    Example:
        projects = apply_exclude_filter(
            projects,
            ("smoke-test", "temp"),
            lambda p: p.name
        )
    """
    if not exclude_patterns:
        return items

    filtered_items = []
    for item in items:
        name = name_getter(item)
        # Exclude if name contains any of the exclude patterns
        if not any(pattern in name for pattern in exclude_patterns):
            filtered_items.append(item)

    return filtered_items


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


def sort_items(
    items: list[T],
    sort_by: str | None,
    sort_key_map: dict[str, Callable[[T], Any]],
    console: ConsoleProtocol,
) -> list[T]:
    """Sort items by a given field.

    Args:
        items: List of items to sort
        sort_by: Sort specification (e.g., "name" or "-name" for descending)
        sort_key_map: Dictionary mapping field names to key functions.
                      Any is acceptable for key return type - can be str, int, datetime, etc.
        console: Rich console for printing warnings

    Returns:
        Sorted list of items
    """
    if not sort_by:
        return items

    reverse = sort_by.startswith("-")
    sort_field = sort_by.lstrip("-")

    if sort_field not in sort_key_map:
        console.print(
            f"[yellow]Warning: Unknown sort field '{sort_field}'. "
            f"Available: {', '.join(sort_key_map.keys())}[/yellow]"
        )
        return items

    try:
        return sorted(items, key=sort_key_map[sort_field], reverse=reverse)
    except Exception as e:
        console.print(f"[yellow]Warning: Could not sort by {sort_field}: {e}[/yellow]")
        return items


def apply_regex_filter(
    items: list[T],
    regex_pattern: str | None,
    field_getter: Callable[[T], str | None],
) -> list[T]:
    """Apply regex filtering to a list of items.

    Args:
        items: List of items to filter
        regex_pattern: Regex pattern to match (None to skip filtering)
        field_getter: Function to extract the field value from an item

    Returns:
        Filtered list of items

    Raises:
        click.BadParameter: If regex pattern is invalid
    """
    if not regex_pattern:
        return items

    import re

    try:
        compiled_pattern = re.compile(regex_pattern)
    except re.error as e:
        raise click.BadParameter(f"Invalid regex pattern: {regex_pattern}. Error: {e}")

    filtered = []
    for item in items:
        field_value = field_getter(item)
        if field_value and compiled_pattern.search(field_value):
            filtered.append(item)
    return filtered


def apply_wildcard_filter(
    items: list[T],
    wildcard_pattern: str | None,
    field_getter: Callable[[T], str | None],
) -> list[T]:
    """Apply wildcard pattern filtering to a list of items.

    Args:
        items: List of items to filter
        wildcard_pattern: Wildcard pattern (e.g., "*prod*")
        field_getter: Function to extract the field value from an item

    Returns:
        Filtered list of items
    """
    if not wildcard_pattern:
        return items

    import re

    # Convert wildcards to regex
    pattern = wildcard_pattern.replace("*", ".*").replace("?", ".")

    # Add anchors if pattern doesn't use wildcards at edges
    if not wildcard_pattern.startswith("*"):
        pattern = "^" + pattern
    if not wildcard_pattern.endswith("*"):
        pattern = pattern + "$"

    regex_pattern = re.compile(pattern)

    filtered = []
    for item in items:
        field_value = field_getter(item)
        if field_value and regex_pattern.search(field_value):
            filtered.append(item)
    return filtered


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


def parse_json_string(
    json_str: str | None, field_name: str = "input"
) -> dict[str, Any] | None:
    """Parse a JSON string with error handling.

    Args:
        json_str: JSON string to parse (None returns None)
        field_name: Name of the field being parsed (for error messages)

    Returns:
        Parsed dictionary or None if input is None.
        Any is acceptable - JSON values can be str, int, bool, nested dicts, etc.

    Raises:
        click.BadParameter: If JSON parsing fails
    """
    if not json_str:
        return None

    try:
        return json.loads(json_str)
    except json.JSONDecodeError as e:
        raise click.BadParameter(f"Invalid JSON in {field_name}: {e}")


def parse_comma_separated_list(input_str: str | None) -> list[str] | None:
    """Parse a comma-separated string into a list.

    Args:
        input_str: Comma-separated string (None returns None)

    Returns:
        list of stripped strings or None if input is None
    """
    if not input_str:
        return None

    return [item.strip() for item in input_str.split(",")]


def extract_model_name(run: "Run", max_length: int = 20) -> str:
    """Extract model name from a LangSmith Run object.

    Looks for model name in the following order:
    1. extra.invocation_params.model_name
    2. extra.metadata.ls_model_name

    Args:
        run: LangSmith Run object
        max_length: Maximum length before truncating (default 20)

    Returns:
        Model name string, truncated with "..." if too long, or "-" if not found
    """
    model_name = "-"

    if run.extra and isinstance(run.extra, dict):
        # Try invocation_params first
        if "invocation_params" in run.extra:
            inv_params = run.extra["invocation_params"]
            if isinstance(inv_params, dict) and "model_name" in inv_params:
                model_name = inv_params["model_name"]

        # Try metadata as fallback
        if model_name == "-" and "metadata" in run.extra:
            metadata = run.extra["metadata"]
            if isinstance(metadata, dict) and "ls_model_name" in metadata:
                model_name = metadata["ls_model_name"]

    # Truncate long model names
    if len(model_name) > max_length:
        model_name = model_name[: max_length - 3] + "..."

    return model_name


def format_token_count(tokens: int | None) -> str:
    """Format token count with comma separators.

    Args:
        tokens: Token count (None for missing data)

    Returns:
        Formatted string like "1,234" or "-" if None
    """
    return f"{tokens:,}" if tokens else "-"


def render_run_details(
    data: dict[str, Any],
    console: ConsoleProtocol,
    *,
    title: str | None = None,
) -> None:
    """Render run details in human-readable format.

    Reusable formatter for get_run and get_latest_run commands.

    Args:
        data: Run data dictionary (filtered fields from filter_fields())
        console: Rich console for output
        title: Optional title to print before details (e.g., "Latest Run")

    Example:
        >>> render_run_details(
        ...     {"id": "123", "name": "test", "status": "success"},
        ...     console,
        ...     title="Latest Run"
        ... )
    """
    from rich.syntax import Syntax

    if title:
        console.print(f"[bold]{title}[/bold]")

    console.print(f"[bold]ID:[/bold] {data.get('id')}")
    console.print(f"[bold]Name:[/bold] {data.get('name')}")

    # Print other fields
    for k, v in data.items():
        if k in ["id", "name"]:
            continue
        console.print(f"\n[bold]{k}:[/bold]")
        if isinstance(v, (dict, list)):
            formatted = json_dumps(v, indent=2)
            console.print(Syntax(formatted, "json"))
        else:
            console.print(str(v))


def build_runs_table(runs: list["Run"], title: str, no_truncate: bool = False) -> Any:
    """Build a Rich table for displaying runs.

    Reusable table builder for runs list and view-file commands.

    Args:
        runs: List of Run objects
        title: Table title
        no_truncate: If True, disable column width limits

    Returns:
        Rich Table object populated with run data
    """
    from rich.table import Table

    table = Table(title=title)
    table.add_column("ID", style="dim", no_wrap=True)
    # Conditionally apply max_width based on no_truncate flag
    table.add_column("Name", max_width=None if no_truncate else 30)
    table.add_column("Status", justify="center")
    table.add_column("Latency", justify="right")
    table.add_column("Tokens", justify="right")
    table.add_column("Model", style="cyan", max_width=None if no_truncate else 20)

    for r in runs:
        # Access SDK model fields directly (type-safe)
        r_id = str(r.id)
        r_name = r.name or "Unknown"
        r_status = r.status

        # Colorize status
        status_style = (
            "green"
            if r_status == "success"
            else "red"
            if r_status == "error"
            else "yellow"
        )

        latency = f"{r.latency:.2f}s" if r.latency is not None else "-"

        # Format tokens and extract model name using utility functions
        tokens = format_token_count(r.total_tokens)
        # Disable model name truncation if no_truncate is set
        model_name = extract_model_name(r, max_length=999 if no_truncate else 20)

        table.add_row(
            r_id,
            r_name,
            f"[{status_style}]{r_status}[/{status_style}]",
            latency,
            tokens,
            model_name,
        )

    return table


def should_use_client_side_limit(has_client_filters: bool) -> bool:
    """Determine if limit should be applied client-side after filtering.

    Args:
        has_client_filters: Whether any client-side filtering is being used

    Returns:
        True if limit should be applied after client-side filtering
    """
    return has_client_filters


def apply_client_side_limit(
    items: list[T], limit: int | None, has_client_filters: bool
) -> list[T]:
    """Apply limit after client-side filtering if needed.

    Args:
        items: List of items to limit
        limit: Maximum number of items to return (None for no limit)
        has_client_filters: Whether client-side filtering was used

    Returns:
        Limited list of items
    """
    if has_client_filters and limit:
        return items[:limit]
    return items


def extract_wildcard_search_term(pattern: str | None) -> tuple[str | None, bool]:
    """Extract search term from wildcard pattern for API optimization.

    Args:
        pattern: Wildcard pattern (e.g., "*moments*", "*moments", "moments*")

    Returns:
        Tuple of (search_term, is_unanchored)
        - ("moments", True) for "*moments*" (can use API optimization)
        - ("moments", False) for "*moments" or "moments*" (needs client-side filtering)
        - (None, False) if pattern is None or empty
    """
    if not pattern:
        return None, False

    is_unanchored = pattern.startswith("*") and pattern.endswith("*")
    search_term = pattern.replace("*", "").replace("?", "")
    return search_term if search_term else None, is_unanchored


def extract_regex_search_term(regex: str | None, min_length: int = 2) -> str | None:
    """Extract literal substring from regex for API optimization.

    Args:
        regex: Regular expression pattern
        min_length: Minimum length for extracted term to be useful

    Returns:
        Literal substring suitable for API filtering, or None
    """
    if not regex:
        return None

    import re

    # Remove common regex metacharacters to find literal substring
    search_term = re.sub(r"[.*+?^${}()\[\]\\|]", "", regex)
    return search_term if search_term and len(search_term) >= min_length else None


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
    # Normalize to list
    items = data if isinstance(data, list) else [data] if data else []

    # Handle count mode - short circuit all other output
    if count_flag:
        click.echo(str(len(items)))
        return

    # Determine output format
    format_type = determine_output_format(output_format, ctx.obj.get("json"))

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
    if not items:
        from rich.console import Console

        console = Console()
        console.print(f"[yellow]{empty_message}[/yellow]")
        return

    # Build and print table
    if table_builder:
        table = table_builder(items)
        from rich.console import Console

        console = Console()
        console.print(table)
    else:
        # Data is already a table or printable object
        from rich.console import Console

        console = Console()
        console.print(data)


def get_matching_items(
    items: list[Any],
    *,
    default_item: str | None = None,
    name: str | None = None,
    name_exact: str | None = None,
    name_pattern: str | None = None,
    name_regex: str | None = None,
    name_getter: Callable[[Any], str],
) -> list[Any]:
    """Get list of items matching the given filters.

    Universal helper for pattern matching across any item type.

    Filter precedence (most specific to least specific):
    1. name_exact - Exact match (highest priority)
    2. name_regex - Regular expression
    3. name_pattern - Wildcard pattern (*, ?)
    4. name - Substring/contains match
    5. default_item - Single item (default/fallback)

    Args:
        items: List of items to filter
        default_item: Single item (default fallback)
        name: Substring/contains match (convenience filter)
        name_exact: Exact name match
        name_pattern: Wildcard pattern (e.g., "dev/*", "*production*")
        name_regex: Regular expression pattern
        name_getter: Function to extract name from an item

    Returns:
        List of matching items

    Examples:
        # Single item (default)
        get_matching_items(projects, default_item="my-project", name_getter=lambda p: p.name)
        # -> [project_with_name_my_project]

        # Exact match
        get_matching_items(projects, name_exact="production-api", name_getter=lambda p: p.name)
        # -> [project_with_name_production_api] or []

        # Substring contains
        get_matching_items(projects, name="prod", name_getter=lambda p: p.name)
        # -> [production-api, production-web, dev-prod-test]

        # Wildcard pattern
        get_matching_items(projects, name_pattern="dev/*", name_getter=lambda p: p.name)
        # -> [dev/api, dev/web, dev/worker]

        # Regex pattern
        get_matching_items(projects, name_regex="^prod-.*-v[0-9]+$", name_getter=lambda p: p.name)
        # -> [prod-api-v1, prod-web-v2]
    """
    # Exact match has highest priority - return immediately if found
    if name_exact:
        matching = [item for item in items if name_getter(item) == name_exact]
        return matching

    # If a default item is given and no other filters, find and return just that item
    if default_item and not name and not name_pattern and not name_regex:
        # Try to find item with matching name
        matching = [item for item in items if name_getter(item) == default_item]
        if matching:
            return matching
        # If not found, assume default_item might be used elsewhere (e.g., for API calls)
        # Return empty list - caller will handle
        return []

    # Apply filters in order
    filtered_items = items

    # Apply regex filter (higher priority than wildcard)
    if name_regex:
        filtered_items = apply_regex_filter(filtered_items, name_regex, name_getter)

    # Apply wildcard pattern filter
    if name_pattern:
        filtered_items = apply_wildcard_filter(
            filtered_items, name_pattern, name_getter
        )

    # Apply substring/contains filter (lowest priority)
    if name:
        filtered_items = [item for item in filtered_items if name in name_getter(item)]

    return filtered_items


def get_matching_projects(
    client: Any,
    *,
    project: str | None = None,
    name: str | None = None,
    name_exact: str | None = None,
    name_pattern: str | None = None,
    name_regex: str | None = None,
) -> list[str]:
    """Get list of project names matching the given filters.

    Universal helper for project pattern matching across all commands.

    Filter precedence (most specific to least specific):
    1. name_exact - Exact match (highest priority)
    2. name_regex - Regular expression
    3. name_pattern - Wildcard pattern (*, ?)
    4. name - Substring/contains match
    5. project - Single project (default/fallback)

    Args:
        client: LangSmith Client instance
        project: Single project name (default fallback)
        name: Substring/contains match (convenience filter)
        name_exact: Exact project name match
        name_pattern: Wildcard pattern (e.g., "dev/*", "*production*")
        name_regex: Regular expression pattern

    Returns:
        List of matching project names

    Examples:
        # Single project (default)
        get_matching_projects(client, project="my-project")
        # -> ["my-project"]

        # Exact match
        get_matching_projects(client, name_exact="production-api")
        # -> ["production-api"] or []

        # Substring contains
        get_matching_projects(client, name="prod")
        # -> ["production-api", "production-web", "dev-prod-test"]

        # Wildcard pattern
        get_matching_projects(client, name_pattern="dev/*")
        # -> ["dev/api", "dev/web", "dev/worker"]

        # Regex pattern
        get_matching_projects(client, name_regex="^prod-.*-v[0-9]+$")
        # -> ["prod-api-v1", "prod-web-v2"]
    """
    # If a specific project is given and no other filters, return just that project
    # (don't need to call API)
    if project and not name and not name_exact and not name_pattern and not name_regex:
        return [project]

    # Otherwise, list all projects and use universal filter
    all_projects = list(client.list_projects())

    matching = get_matching_items(
        all_projects,
        default_item=project,
        name=name,
        name_exact=name_exact,
        name_pattern=name_pattern,
        name_regex=name_regex,
        name_getter=lambda p: p.name,
    )

    # If we found matching projects, return their names
    if matching:
        return [p.name for p in matching]

    # If no matches and we have a default project, return it
    # (it might be a valid project that just isn't in the list yet)
    if project:
        return [project]

    return []


def parse_duration_to_seconds(duration_str: str) -> str:
    """Parse duration string like '2s', '500ms', '1.5s' to FQL format."""
    import re

    # LangSmith FQL accepts durations like "2s", "500ms", "1.5s"
    # Just validate format and return as-is
    if not re.match(r"^\d+(\.\d+)?(s|ms|m|h|d)$", duration_str):
        raise click.BadParameter(
            f"Invalid duration format: {duration_str}. Use format like '2s', '500ms', '1.5s', '5m', '2h', '7d'"
        )
    return duration_str


def parse_relative_time(time_str: str) -> Any:
    """Parse relative time like '24h', '7d', '30m' to datetime."""
    import re
    import datetime

    match = re.match(r"^(\d+)(m|h|d)$", time_str)
    if not match:
        raise click.BadParameter(
            f"Invalid time format: {time_str}. Use format like '30m', '24h', '7d'"
        )

    value, unit = int(match.group(1)), match.group(2)

    if unit == "m":
        delta = datetime.timedelta(minutes=value)
    elif unit == "h":
        delta = datetime.timedelta(hours=value)
    elif unit == "d":
        delta = datetime.timedelta(days=value)
    else:
        raise click.BadParameter(f"Unsupported time unit: {unit}")

    return datetime.datetime.now(datetime.timezone.utc) - delta


def parse_time_input(time_str: str) -> Any:
    """Parse time input in multiple formats to datetime.

    Supports:
    - ISO format: "2024-01-14T10:00:00Z", "2024-01-14"
    - Relative shorthand: "24h", "7d", "30m"
    - Natural language: "3 days ago", "1 hour ago", "2 weeks ago"

    Args:
        time_str: Time string in any supported format

    Returns:
        datetime object in UTC

    Raises:
        click.BadParameter: If format is not recognized
    """
    import datetime
    import re

    time_str = time_str.strip()

    # Try ISO format first
    try:
        return datetime.datetime.fromisoformat(time_str.replace("Z", "+00:00"))
    except ValueError:
        pass

    # Try relative shorthand (24h, 7d, 30m)
    match = re.match(r"^(\d+)(m|h|d|w)$", time_str, re.IGNORECASE)
    if match:
        value, unit = int(match.group(1)), match.group(2).lower()
        if unit == "m":
            delta = datetime.timedelta(minutes=value)
        elif unit == "h":
            delta = datetime.timedelta(hours=value)
        elif unit == "d":
            delta = datetime.timedelta(days=value)
        elif unit == "w":
            delta = datetime.timedelta(weeks=value)
        else:
            raise click.BadParameter(f"Unsupported time unit: {unit}")
        return datetime.datetime.now(datetime.timezone.utc) - delta

    # Try natural language ("3 days ago", "1 hour ago", "2 weeks ago")
    match = re.match(
        r"^(\d+)\s*(minute|min|hour|hr|day|week|wk)s?\s*ago$", time_str, re.IGNORECASE
    )
    if match:
        value, unit = int(match.group(1)), match.group(2).lower()
        if unit in ("minute", "min"):
            delta = datetime.timedelta(minutes=value)
        elif unit in ("hour", "hr"):
            delta = datetime.timedelta(hours=value)
        elif unit == "day":
            delta = datetime.timedelta(days=value)
        elif unit in ("week", "wk"):
            delta = datetime.timedelta(weeks=value)
        else:
            raise click.BadParameter(f"Unsupported time unit: {unit}")
        return datetime.datetime.now(datetime.timezone.utc) - delta

    raise click.BadParameter(
        f"Invalid time format: {time_str}. "
        "Use ISO format (2024-01-14T10:00:00Z), "
        "relative shorthand (24h, 7d, 30m), "
        "or natural language (3 days ago, 1 hour ago)"
    )


def build_time_fql_filters(
    since: str | None = None,
    last: str | None = None,
) -> list[str]:
    """Build FQL filter expressions for time-based filtering.

    Args:
        since: Show items since this time (ISO format, relative, or natural language)
        last: Show items from last duration (e.g., '24h', '7d', '30m')

    Returns:
        List of FQL filter expressions (may be empty)

    Raises:
        click.BadParameter: If time format is invalid

    Example:
        >>> filters = build_time_fql_filters(since="3 days ago")
        >>> filters
        ['gt(start_time, "2024-01-11T10:00:00+00:00")']
    """
    fql_filters: list[str] = []

    if since:
        timestamp = parse_time_input(since)
        fql_filters.append(f'gt(start_time, "{timestamp.isoformat()}")')

    if last:
        timestamp = parse_time_input(last)
        fql_filters.append(f'gt(start_time, "{timestamp.isoformat()}")')

    return fql_filters


def combine_fql_filters(filters: list[str]) -> str | None:
    """Combine multiple FQL filter expressions into a single filter.

    Args:
        filters: List of FQL filter expressions (e.g., ['gt(start_time, "...")', 'has(tags, "...")'])

    Returns:
        Combined filter string, or None if the list is empty.
        Single filter is returned as-is, multiple filters are wrapped in and(...).

    Example:
        >>> combine_fql_filters([])
        None
        >>> combine_fql_filters(['gt(start_time, "2024-01-01")'])
        'gt(start_time, "2024-01-01")'
        >>> combine_fql_filters(['gt(start_time, "2024-01-01")', 'has(tags, "prod")'])
        'and(gt(start_time, "2024-01-01"), has(tags, "prod"))'
    """
    if not filters:
        return None
    if len(filters) == 1:
        return filters[0]
    return f"and({', '.join(filters)})"


def add_time_filter_options(func: Callable[..., Any]) -> Callable[..., Any]:
    """Decorator to add universal time filtering options to a command.

    Adds the following Click options:
    - --since: Show items since time (ISO format, relative, or natural language)
    - --last: Show items from last duration (shorthand only)

    Usage:
        @runs.command("list")
        @add_project_filter_options
        @add_time_filter_options
        @click.pass_context
        def list_runs(ctx, project, ..., since, last, ...):
            time_filters = build_time_fql_filters(since=since, last=last)
            # Combine with other filters...

    Supported time formats:
        --since "2024-01-14T10:00:00Z"    # ISO format
        --since "3d"                       # 3 days ago (shorthand)
        --since "3 days ago"              # Natural language
        --last "24h"                       # Last 24 hours
        --last "7d"                        # Last 7 days
    """
    func = click.option(
        "--last",
        help="Show items from last duration (e.g., '24h', '7d', '30m', '2w').",
    )(func)
    func = click.option(
        "--since",
        help="Show items since time (ISO format, '3d', or '3 days ago').",
    )(func)
    return func


def add_project_filter_options(func: Callable[..., Any]) -> Callable[..., Any]:
    """Decorator to add universal project filtering options to a command.

    Adds the following Click options in consistent order:
    - --project: Single project name (default/fallback)
    - --project-name: Substring/contains match
    - --project-name-exact: Exact match
    - --project-name-pattern: Wildcard pattern (*, ?)
    - --project-name-regex: Regular expression

    Usage:
        @runs.command("list")
        @add_project_filter_options
        @click.pass_context
        def list_runs(ctx, project, project_name, project_name_exact, project_name_pattern, project_name_regex, ...):
            client = get_or_create_client(ctx)
            projects = get_matching_projects(
                client,
                project=project,
                name=project_name,
                name_exact=project_name_exact,
                name_pattern=project_name_pattern,
                name_regex=project_name_regex,
            )
            # Use projects list...
    """
    func = click.option(
        "--project-name-regex",
        help="Regular expression pattern for project names (e.g., '^prod-.*-v[0-9]+$').",
    )(func)
    func = click.option(
        "--project-name-pattern",
        help="Wildcard pattern for project names (e.g., 'dev/*', '*production*').",
    )(func)
    func = click.option(
        "--project-name-exact",
        help="Exact project name match.",
    )(func)
    func = click.option(
        "--project-name",
        help="Substring/contains match for project names (convenience filter).",
    )(func)
    func = click.option(
        "--project",
        default="default",
        help="Project name (default fallback if no other filters specified).",
    )(func)
    return func


def write_output_to_file(
    data: list[dict[str, Any]],
    output_path: str,
    console: ConsoleProtocol,
    *,
    format_type: str = "jsonl",
) -> None:
    """Write data to a file with error handling and user feedback.

    Universal helper for --output flag across all commands.

    Args:
        data: List of dictionaries to write.
              Any is acceptable - JSON values can be str, int, bool, datetime, nested dicts, etc.
        output_path: Path to write file to
        console: Rich console for user feedback
        format_type: Output format ("jsonl" for newline-delimited JSON, "json" for JSON array)

    Raises:
        click.Abort: If file writing fails

    Example:
        write_output_to_file(
            [{"id": "123", "name": "test"}],
            "output.jsonl",
            console,
            format_type="jsonl"
        )
    """
    try:
        with open(output_path, "w", encoding="utf-8") as f:
            if format_type == "jsonl":
                # Write as newline-delimited JSON (one object per line)
                for item in data:
                    f.write(json_dumps(item) + "\n")
            elif format_type == "json":
                # Write as JSON array
                f.write(json_dumps(data))
            else:
                raise ValueError(f"Unsupported format_type: {format_type}")

        console.print(f"[green]Wrote {len(data)} items to {output_path}[/green]")
    except Exception as e:
        console.print(f"[red]Error writing to file {output_path}: {e}[/red]")
        raise click.Abort()


def output_option(
    help_text: str = "Write output to file instead of stdout. For list commands, uses JSONL format (one item per line); for single items, uses JSON format.",
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


def apply_grep_filter(
    items: list[T],
    grep_pattern: str | None,
    grep_fields: tuple[str, ...] = (),
    ignore_case: bool = False,
    use_regex: bool = False,
) -> list[T]:
    """Apply grep-style content filtering to items.

    Searches through specified fields (or all fields if none specified) for pattern matches.
    Handles nested JSON strings by parsing them before searching.

    Args:
        items: List of items (typically Run objects) to filter
        grep_pattern: Pattern to search for (substring or regex)
        grep_fields: Tuple of field names to search in (e.g., ('inputs', 'outputs', 'error'))
                    If empty, searches all fields
        ignore_case: Whether to perform case-insensitive search
        use_regex: Whether to treat pattern as regex (otherwise substring match)

    Returns:
        Filtered list of items that match the pattern

    Example:
        # Search for "druze" in inputs field
        filtered = apply_grep_filter(runs, "druze", grep_fields=("inputs",))

        # Case-insensitive regex search for Hebrew characters
        filtered = apply_grep_filter(runs, r"[\u0590-\u05ff]", ignore_case=True, use_regex=True)
    """
    if not grep_pattern:
        return items

    import re

    # Compile regex pattern if needed
    if use_regex:
        try:
            flags = re.IGNORECASE if ignore_case else 0
            compiled_pattern = re.compile(grep_pattern, flags)
        except re.error as e:
            raise click.BadParameter(
                f"Invalid regex pattern: {grep_pattern}. Error: {e}"
            )
    else:
        # For substring search, create a simple regex
        escaped_pattern = re.escape(grep_pattern)
        flags = re.IGNORECASE if ignore_case else 0
        compiled_pattern = re.compile(escaped_pattern, flags)

    filtered_items = []
    for item in items:
        # Convert item to dict for searching
        if hasattr(item, "model_dump"):
            # Type-safe call: we verified the method exists
            model_dump_method = getattr(item, "model_dump")
            item_dict: dict[str, Any] = model_dump_method(mode="json")
        elif isinstance(item, dict):
            item_dict = item
        else:
            # Skip items we can't convert to dict
            continue

        # Determine which fields to search
        if grep_fields:
            # Search only specified fields
            fields_to_search = {
                field: item_dict.get(field)
                for field in grep_fields
                if field in item_dict
            }
        else:
            # Search all fields
            fields_to_search = item_dict

        # Convert to JSON string for searching (handles nested structures)
        # Use ensure_ascii=False to preserve Unicode characters (Hebrew, Chinese, etc.)
        # Note: Searches the serialized JSON, including any nested JSON strings as-is
        content = json_dumps(fields_to_search)

        # Search for pattern
        if compiled_pattern.search(content):
            filtered_items.append(item)

    return filtered_items


def build_runs_list_filter(
    filter_: str | None = None,
    status: str | None = None,
    failed: bool = False,
    succeeded: bool = False,
    tag: tuple[str, ...] = (),
    model: str | None = None,
    slow: bool = False,
    recent: bool = False,
    today: bool = False,
    min_latency: str | None = None,
    max_latency: str | None = None,
    since: str | None = None,
    last: str | None = None,
) -> tuple[str | None, bool | None]:
    """Build FQL filter string and error filter from command options.

    This is a canonical helper that consolidates all run filtering logic,
    shared between `runs list` and `runs get-latest` commands.

    Args:
        filter_: User's custom FQL filter string
        status: Status filter ("success" or "error")
        failed: Show only failed runs (equivalent to status="error")
        succeeded: Show only successful runs (equivalent to status="success")
        tag: Tuple of tags (AND logic - all must be present)
        model: Model name to search for
        slow: Filter to slow runs (latency > 5s)
        recent: Filter to recent runs (last hour)
        today: Filter to today's runs
        min_latency: Minimum latency (e.g., '2s', '500ms')
        max_latency: Maximum latency (e.g., '10s', '2000ms')
        since: Show runs since time (ISO or relative like '1 hour ago')
        last: Show runs from last duration (e.g., '24h', '7d')

    Returns:
        Tuple of (combined_filter, error_filter)
        - combined_filter: FQL filter string or None
        - error_filter: Boolean error filter or None

    Example:
        >>> filter_str, error_filter = build_runs_list_filter(
        ...     status="error",
        ...     tag=("prod", "critical"),
        ...     min_latency="5s"
        ... )
        >>> print(filter_str)
        and(has(tags, "prod"), has(tags, "critical"), gt(latency, "5s"))
        >>> print(error_filter)
        True
    """
    import datetime

    # Handle status filtering with multiple options
    error_filter = None
    if status == "error" or failed:
        error_filter = True
    elif status == "success" or succeeded:
        error_filter = False

    # Build FQL filter from smart flags
    fql_filters = []

    # Add user's custom filter first
    if filter_:
        fql_filters.append(filter_)

    # Tag filtering (AND logic - all tags must be present)
    if tag:
        for t in tag:
            fql_filters.append(f'has(tags, "{t}")')

    # Model filtering (search in model-related fields)
    if model:
        fql_filters.append(f'search("{model}")')

    # Smart filters
    if slow:
        fql_filters.append('gt(latency, "5s")')

    if recent:
        one_hour_ago = datetime.datetime.now(
            datetime.timezone.utc
        ) - datetime.timedelta(hours=1)
        fql_filters.append(f'gt(start_time, "{one_hour_ago.isoformat()}")')

    if today:
        today_start = datetime.datetime.now(datetime.timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        fql_filters.append(f'gt(start_time, "{today_start.isoformat()}")')

    # Flexible latency filters
    if min_latency:
        duration = parse_duration_to_seconds(min_latency)
        fql_filters.append(f'gt(latency, "{duration}")')

    if max_latency:
        duration = parse_duration_to_seconds(max_latency)
        fql_filters.append(f'lt(latency, "{duration}")')

    # Flexible time filters (supports ISO, relative shorthand, and natural language)
    time_filters = build_time_fql_filters(since=since, last=last)
    fql_filters.extend(time_filters)

    # Combine all filters with AND logic
    combined_filter = combine_fql_filters(fql_filters)

    return combined_filter, error_filter
