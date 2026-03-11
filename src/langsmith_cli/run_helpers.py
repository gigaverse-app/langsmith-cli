"""Run-specific helpers for table building and filter construction."""

from __future__ import annotations

import datetime
from typing import Any, Protocol

from langsmith.schemas import Run

from langsmith_cli.output import json_dumps
from langsmith_cli.time_parsing import build_time_fql_filters, combine_fql_filters
from langsmith_cli.utils import build_tag_fql_filters, parse_duration_to_seconds


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
