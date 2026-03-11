"""Filtering, sorting, and search utilities."""

import json
import re
from typing import Any, Callable, TypeVar, overload

import click
from langsmith.schemas import Run
from pydantic import BaseModel

from langsmith_cli.output import ConsoleProtocol, json_dumps

T = TypeVar("T")
ModelT = TypeVar("ModelT", bound=BaseModel)


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

    field_set = parse_fields_option(fields)

    if isinstance(data, list):
        return [item.model_dump(include=field_set, mode="json") for item in data]
    return data.model_dump(include=field_set, mode="json")


def parse_fields_option(fields: str | None) -> set[str] | None:
    """Parse comma-separated fields string into a set, or None if not provided.

    Args:
        fields: Comma-separated field names (e.g., "id,name,created_at") or None

    Returns:
        Set of field names, or None if fields is None/empty
    """
    if not fields:
        return None
    return {f.strip() for f in fields.split(",") if f.strip()}


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


def add_grep_options(func: Callable[..., Any]) -> Callable[..., Any]:
    """Reusable decorator that adds --grep, --grep-ignore-case, --grep-regex, --grep-in options.

    Adds parameters: grep, grep_ignore_case, grep_regex, grep_in
    """
    func = click.option(
        "--grep-in",
        help="Comma-separated fields to search in (e.g., 'inputs,outputs,error'). "
        "Searches all fields if not specified.",
    )(func)
    func = click.option(
        "--grep-regex",
        is_flag=True,
        help="Treat --grep pattern as regex.",
    )(func)
    func = click.option(
        "--grep-ignore-case",
        is_flag=True,
        help="Make --grep search case-insensitive.",
    )(func)
    func = click.option(
        "--grep",
        help="Client-side pattern search in run content (inputs, outputs, error). "
        "Searches ALL content, parses nested JSON.",
    )(func)
    return func


def add_metadata_filter_options(func: Callable[..., Any]) -> Callable[..., Any]:
    """Reusable decorator that adds --metadata key=value filter option.

    Adds parameter: metadata_filters (tuple of "key=value" strings)
    """
    func = click.option(
        "--metadata",
        "metadata_filters",
        multiple=True,
        help="Filter by metadata key=value (server-side, fast). "
        "Can specify multiple: --metadata key1=val1 --metadata key2=val2",
    )(func)
    return func


def build_metadata_fql_filters(metadata_filters: tuple[str, ...]) -> list[str]:
    """Build FQL filter clauses for metadata key=value filtering.

    Args:
        metadata_filters: Tuple of "key=value" strings

    Returns:
        List of FQL filter strings

    Raises:
        click.BadParameter: If a filter doesn't contain '='
    """
    filters: list[str] = []
    for mf in metadata_filters:
        if "=" not in mf:
            raise click.BadParameter(
                f"Invalid metadata filter: {mf}. Use key=value format."
            )
        key, value = mf.split("=", 1)
        filters.append(
            f'and(in(metadata_key, ["{key}"]), eq(metadata_value, "{value}"))'
        )
    return filters


def add_name_filter_options(func: Callable[..., Any]) -> Callable[..., Any]:
    """Reusable decorator that adds --name-pattern and --name-regex options.

    Adds parameters: name_pattern, name_regex
    Use with get_matching_items() or apply_name_filters() for client-side filtering.
    """
    func = click.option(
        "--name-regex",
        help="Filter names with regex (e.g., '^test-.*-v[0-9]+$'). Client-side filtering.",
    )(func)
    func = click.option(
        "--name-pattern",
        help="Filter names with wildcards (e.g., '*auth*'). Client-side filtering.",
    )(func)
    return func


def apply_name_filters(
    items: list[T],
    name_getter: Callable[[T], str],
    name_pattern: str | None = None,
    name_regex: str | None = None,
) -> list[T]:
    """Apply name-pattern and name-regex filters to a list of items.

    Args:
        items: List of items to filter
        name_getter: Function to extract name from each item
        name_pattern: Wildcard pattern (e.g., '*auth*')
        name_regex: Regex pattern (e.g., '^test-.*')

    Returns:
        Filtered list of items
    """
    import fnmatch

    if not name_pattern and not name_regex:
        return items

    filtered = items
    if name_pattern:
        filtered = [
            item
            for item in filtered
            if fnmatch.fnmatch(name_getter(item), name_pattern)
        ]
    if name_regex:
        compiled = re.compile(name_regex)
        filtered = [item for item in filtered if compiled.search(name_getter(item))]

    return filtered


def sort_by_option(
    fields: str = "name",
    help_text: str | None = None,
) -> Any:
    """Reusable Click option decorator for --sort-by flag.

    Args:
        fields: Comma-separated default sort field names for help text
        help_text: Custom help text

    Returns:
        Click option decorator
    """
    default_help = (
        f"Sort by field ({fields}). Prefix with - for descending (e.g., '-name')."
    )
    return click.option(
        "--sort-by",
        help=help_text or default_help,
    )


def sort_items(
    items: list[T],
    sort_by: str | None,
    sort_key_map: dict[str, Callable[[T], Any]] | None = None,
    console: ConsoleProtocol | None = None,
) -> list[T]:
    """Sort items by a given field.

    Args:
        items: List of items to sort
        sort_by: Sort specification (e.g., "name" or "-name" for descending)
        sort_key_map: Dictionary mapping field names to key functions.
                      If None, uses getattr to look up the field directly.
                      Any is acceptable for key return type - can be str, int, datetime, etc.
        console: Rich console for printing warnings. If None, warnings are skipped.

    Returns:
        Sorted list of items
    """
    if not sort_by:
        return items

    reverse = sort_by.startswith("-")
    sort_field = sort_by.lstrip("-")

    if sort_key_map is not None:
        if sort_field not in sort_key_map:
            if console is not None:
                console.print(
                    f"[yellow]Warning: Unknown sort field '{sort_field}'. "
                    f"Available: {', '.join(sort_key_map.keys())}[/yellow]"
                )
            return items
        key_func: Callable[[T], Any] = sort_key_map[sort_field]
    else:

        def _attr_key(item: T) -> Any:
            return getattr(item, sort_field, None)

        key_func = _attr_key

    try:
        return sorted(items, key=key_func, reverse=reverse)
    except Exception as e:
        if console is not None:
            console.print(
                f"[yellow]Warning: Could not sort by {sort_field}: {e}[/yellow]"
            )
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

    # Remove common regex metacharacters to find literal substring
    search_term = re.sub(r"[.*+?^${}()\[\]\\|]", "", regex)
    return search_term if search_term and len(search_term) >= min_length else None


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


def build_tag_fql_filters(tags: tuple[str, ...] | list[str]) -> list[str]:
    """Build FQL filter clauses for tag filtering (AND logic).

    Args:
        tags: Tag values to filter by (all must be present on a run)

    Returns:
        List of FQL filter strings, one per tag
    """
    return [f'has(tags, "{t}")' for t in tags]


def filter_runs_by_tags(
    runs: list[Run], tags: tuple[str, ...] | list[str]
) -> list[Run]:
    """Client-side tag filtering for cached runs (AND logic).

    Args:
        runs: List of Run instances to filter
        tags: Tag values that must all be present on each run

    Returns:
        Filtered list of runs where all specified tags are present
    """
    if not tags:
        return runs
    tag_set = set(tags)
    return [r for r in runs if r.tags and tag_set.issubset(set(r.tags))]
