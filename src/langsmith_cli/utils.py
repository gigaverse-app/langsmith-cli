"""Utility functions shared across commands."""

from typing import Any, Callable, Protocol, TypeVar
import click

T = TypeVar("T")


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

    import json

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
