"""Utility functions shared across commands."""

from typing import Any, Callable, Protocol, TypeVar, overload
import click
import json
import langsmith
from pydantic import BaseModel

T = TypeVar("T")
ModelT = TypeVar("ModelT", bound=BaseModel)


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
def filter_fields(data: ModelT, fields: str | None) -> dict[str, Any]: ...


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
            import json

            click.echo(json.dumps([], default=str))
            return

    # Apply field filtering if requested
    if fields:
        data = [{k: v for k, v in item.items() if k in fields} for item in data]

    if format_type == "json":
        import json

        click.echo(json.dumps(data, default=str))
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
