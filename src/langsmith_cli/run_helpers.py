"""Run-specific helpers for table building and filter construction."""

from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Protocol

import click

from langsmith_cli.filtering import build_tag_fql_filters
from langsmith_cli.output import json_dumps, render_detail_fields
from langsmith_cli.time_parsing import (
    build_time_fql_filters,
    combine_fql_filters,
    parse_duration_to_seconds,
)

if TYPE_CHECKING:
    from langsmith.schemas import Run


def _as_mapping(value: object, field_path: str) -> Mapping[str, object]:
    """Validate a dynamic LangSmith payload field before reading from it."""
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise TypeError(
            f"Expected {field_path} to be a mapping, got {type(value).__name__}"
        )
    return value


def _nested_mapping(
    parent: Mapping[str, object], key: str, field_path: str
) -> Mapping[str, object]:
    if key not in parent:
        return {}
    return _as_mapping(parent[key], f"{field_path}.{key}")


def run_extra_mapping(run: Run) -> Mapping[str, object]:
    """Return ``run.extra`` as a validated mapping."""
    return _as_mapping(run.extra, "run.extra")


def run_extra_metadata(run: Run) -> Mapping[str, object]:
    """Return ``run.extra.metadata`` as a validated mapping."""
    return _nested_mapping(run_extra_mapping(run), "metadata", "run.extra")


def run_invocation_params(run: Run) -> Mapping[str, object]:
    """Return ``run.extra.invocation_params`` as a validated mapping."""
    return _nested_mapping(run_extra_mapping(run), "invocation_params", "run.extra")


def run_metadata_mapping(run: Run) -> Mapping[str, object]:
    """Return the SDK-level ``run.metadata`` as a validated mapping."""
    return _as_mapping(run.metadata, "run.metadata")


def run_inputs_mapping(run: Run) -> Mapping[str, object]:
    """Return ``run.inputs`` as a validated mapping."""
    return _as_mapping(run.inputs, "run.inputs")


def mapping_string_value(mapping: Mapping[str, object], key: str) -> str | None:
    """Read a dynamic mapping value and normalize present values to strings."""
    if key not in mapping:
        return None
    value = mapping[key]
    if value is None:
        return None
    return str(value)


def resolve_root_scope(
    *, roots: bool, all_runs: bool, is_root: bool | None
) -> bool | None:
    """Resolve the ``--roots`` / ``--all-runs`` / ``--is-root`` triple to a
    single ``is_root: bool | None`` value, rejecting contradictory combinations.

    Multiple commands (``runs list``, ``runs export``) expose all three flags
    as ergonomic ways to express the same intent — keeping the resolution in
    one place stops them from drifting (e.g. one enforcing the conflict check
    and the other silently letting last-write-wins).

    Args:
        roots: True if ``--roots`` was passed (shorthand for ``--is-root true``).
        all_runs: True if ``--all-runs`` was passed (shorthand for ``--is-root false``).
        is_root: Explicit ``--is-root`` value, or ``None`` if not specified.

    Returns:
        The resolved value to forward to the SDK as ``is_root=``.

    Raises:
        click.UsageError: If the user passed contradictory flags.
    """
    if roots and all_runs:
        raise click.UsageError("Use only one of --roots or --all-runs.")
    if roots and is_root is False:
        raise click.UsageError("Use only one of --roots or --is-root false.")
    if all_runs and is_root is True:
        raise click.UsageError("Use only one of --all-runs or --is-root true.")

    if roots:
        return True
    if all_runs:
        return False
    return is_root


class ConsoleProtocol(Protocol):
    """Protocol for Rich Console interface - avoids heavy import."""

    def print(self, *args: Any, **kwargs: Any) -> None:
        """Print to console."""
        ...


def extract_model_name(run: Run, max_length: int = 20) -> str:
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

    invocation = run_invocation_params(run)
    if "model_name" in invocation:
        model_name = str(invocation["model_name"])

    if model_name == "-":
        metadata = run_extra_metadata(run)
        if "ls_model_name" in metadata:
            model_name = str(metadata["ls_model_name"])

    # Truncate long model names
    if len(model_name) > max_length:
        model_name = model_name[: max_length - 3] + "..."

    return model_name


def get_full_model_name(run: Run) -> str:
    """Extract the full (untruncated) model name from a run.

    Distinct from :func:`extract_model_name`, which truncates and returns
    ``"-"`` for missing values (table-rendering semantics). Aggregation
    callers (``runs usage``, ``runs pricing``) need the full name and the
    sentinel ``"unknown"`` so they can skip non-LLM wrappers cleanly.

    Lookup order:
        1. ``extra.metadata.ls_model_name`` — the LangSmith-canonical field.
        2. ``extra.invocation_params.model`` / ``model_name`` — provider raw.

    Returns ``"unknown"`` when no model name is present (e.g. chain/tool runs).
    """
    metadata = run_extra_metadata(run)
    model = metadata["ls_model_name"] if "ls_model_name" in metadata else None
    if model:
        return str(model)

    invocation = run_invocation_params(run)
    model = invocation["model"] if "model" in invocation else None
    if model is None and "model_name" in invocation:
        model = invocation["model_name"]
    if model:
        return str(model)
    return "unknown"


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

    render_detail_fields(data, console, [("id", "ID"), ("name", "Name")])

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


def build_runs_table(runs: list[Run], title: str, no_truncate: bool = False) -> Any:
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
    table.add_column("ID", style="dim")
    # Conditionally apply max_width based on no_truncate flag
    table.add_column("Name", max_width=None if no_truncate else 30, overflow="fold")
    table.add_column("Status", justify="center")
    table.add_column("Latency", justify="right")
    table.add_column("Tokens", justify="right")
    table.add_column(
        "Model", style="cyan", max_width=None if no_truncate else 20, overflow="fold"
    )

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
    before: str | None = None,
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
        since: Show runs since time (ISO or shorthand like '7d', '24h', '30m')
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
        fql_filters.extend(build_tag_fql_filters(tag))

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

    # Flexible time filters (supports ISO and relative shorthand: 30m, 2h, 7d, 2w)
    time_filters = build_time_fql_filters(since=since, last=last, before=before)
    fql_filters.extend(time_filters)

    # Combine all filters with AND logic
    combined_filter = combine_fql_filters(fql_filters)

    return combined_filter, error_filter
