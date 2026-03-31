"""Time parsing and FQL time filter utilities."""

import datetime
import re
from datetime import datetime as datetime_type
from typing import Any, Callable, overload

import click


@overload
def ensure_aware_datetime(dt: datetime_type) -> datetime_type: ...


@overload
def ensure_aware_datetime(dt: None) -> None: ...


@overload
def ensure_aware_datetime(dt: datetime_type | None) -> datetime_type | None: ...


def ensure_aware_datetime(dt: datetime_type | None) -> datetime_type | None:
    """Return dt with UTC tzinfo attached if naive, unchanged if already aware or None.

    Use this whenever comparing datetimes that may come from external sources
    (SDK, cached JSON) where timezone info is not guaranteed.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=datetime.timezone.utc)
    return dt


def parse_duration_to_seconds(duration_str: str) -> str:
    """Parse duration string like '2s', '500ms', '1.5s' to FQL format."""
    # LangSmith FQL accepts durations like "2s", "500ms", "1.5s"
    # Just validate format and return as-is
    if not re.match(r"^\d+(\.\d+)?(s|ms|m|h|d)$", duration_str):
        raise click.BadParameter(
            f"Invalid duration format: {duration_str}. Use format like '2s', '500ms', '1.5s', '5m', '2h', '7d'"
        )
    return duration_str


def parse_relative_time(time_str: str) -> Any:
    """Parse relative time like '24h', '7d', '30m' to datetime."""
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


def _parse_duration_str(time_str: str) -> Any:
    """Try to parse a shorthand duration string into a timedelta.

    Supports relative shorthand only: 30m, 2h, 7d, 2w (case-insensitive).

    Args:
        time_str: Pre-stripped duration string

    Returns:
        timedelta if parsed successfully, None otherwise
    """
    match = re.match(r"^(\d+)(m|h|d|w)$", time_str, re.IGNORECASE)
    if match:
        value, unit = int(match.group(1)), match.group(2).lower()
        if unit == "m":
            return datetime.timedelta(minutes=value)
        elif unit == "h":
            return datetime.timedelta(hours=value)
        elif unit == "d":
            return datetime.timedelta(days=value)
        elif unit == "w":
            return datetime.timedelta(weeks=value)

    return None


def parse_time_input(time_str: str) -> Any:
    """Parse time input in multiple formats to datetime.

    Supports:
    - ISO format: "2024-01-14T10:00:00Z", "2024-01-14"
    - Relative shorthand: "30m", "2h", "7d", "2w" (case-insensitive)

    Args:
        time_str: Time string in any supported format

    Returns:
        datetime object in UTC

    Raises:
        click.BadParameter: If format is not recognized
    """
    time_str = time_str.strip()

    # Try ISO format first
    try:
        return datetime.datetime.fromisoformat(time_str.replace("Z", "+00:00"))
    except ValueError:
        pass

    # Try duration shorthand (30m, 2h, 7d, 2w)
    delta = _parse_duration_str(time_str)
    if delta is not None:
        return datetime.datetime.now(datetime.timezone.utc) - delta

    raise click.BadParameter(
        f"Invalid time format: {time_str!r}. Valid formats:\n"
        "  Shorthand:    30m  2h  7d  2w\n"
        "  ISO datetime: 2024-01-14T10:00:00Z  or  2024-01-14\n"
        "Natural language ('3 days ago', '1 hour ago') is not supported."
    )


def parse_time_duration(time_str: str) -> Any:
    """Parse a duration string into a timedelta.

    Supports relative shorthand only: 30m, 2h, 7d, 2w (case-insensitive).
    For ISO timestamps, raises BadParameter since they are not durations.

    Args:
        time_str: Duration string (e.g., '24h', '7d', '2w')

    Returns:
        timedelta representing the duration

    Raises:
        click.BadParameter: If format is not a valid duration
    """
    delta = _parse_duration_str(time_str.strip())
    if delta is not None:
        return delta

    raise click.BadParameter(
        f"Invalid duration format: {time_str!r}. Use shorthand: 30m  2h  7d  2w"
    )


def parse_time_range(
    since: str | None = None,
    last: str | None = None,
    before: str | None = None,
) -> tuple[datetime_type | None, datetime_type | None]:
    """Parse time filter options into a (since_dt, until_dt) datetime range.

    This is the shared logic for time range parsing, used by both
    build_time_fql_filters() (server-side FQL) and parse_time_filter() (client-side).

    Supports flexible time windows:
    - --since alone: lower bound only
    - --before alone: upper bound only
    - --last alone: lower bound relative to now
    - --since + --last: window from since to since + duration
    - --before + --last: window from before - duration to before
    - --since + --before: explicit window
    - --since + --before + --last: error (ambiguous)

    Returns:
        Tuple of (since_dt, until_dt). Either or both may be None.

    Raises:
        click.BadParameter: If time format is invalid or conflicting options given
    """
    if since and before and last:
        raise click.BadParameter(
            "Cannot use --since, --before, and --last together (ambiguous). "
            "Use --since + --before for an explicit window, or "
            "--since + --last / --before + --last for a duration-based window."
        )

    since_dt: datetime_type | None = None
    until_dt: datetime_type | None = None

    if since and before:
        since_dt = parse_time_input(since)
        until_dt = parse_time_input(before)
    elif since and last:
        since_dt = parse_time_input(since)
        duration = parse_time_duration(last)
        until_dt = since_dt + duration
    elif before and last:
        until_dt = parse_time_input(before)
        duration = parse_time_duration(last)
        since_dt = until_dt - duration
    elif since:
        since_dt = parse_time_input(since)
    elif before:
        until_dt = parse_time_input(before)
    elif last:
        since_dt = parse_time_input(last)

    return since_dt, until_dt


def build_time_fql_filters(
    since: str | None = None,
    last: str | None = None,
    before: str | None = None,
) -> list[str]:
    """Build FQL filter expressions for time-based filtering.

    Delegates to parse_time_range() for time parsing, then converts
    the resulting datetime range to FQL gt/lt expressions.

    Args:
        since: Show items since this time (ISO format or shorthand like '7d', '30m').
        last: Show items from last duration (e.g., '24h', '7d', '30m').
        before: Show items before this time (ISO format or shorthand like '7d', '30m').

    Returns:
        List of FQL filter expressions (may be empty)

    Raises:
        click.BadParameter: If time format is invalid or conflicting options given
    """
    since_dt, until_dt = parse_time_range(since=since, last=last, before=before)

    fql_filters: list[str] = []
    if since_dt:
        fql_filters.append(f'gt(start_time, "{since_dt.isoformat()}")')
    if until_dt:
        fql_filters.append(f'lt(start_time, "{until_dt.isoformat()}")')

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
    - --since: Show items since time (ISO or shorthand: 30m, 2h, 7d, 2w)
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
        --since "3d"                       # shorthand: 3 days ago
        --last "24h"                       # Last 24 hours
        --last "7d"                        # Last 7 days
    """
    func = click.option(
        "--last",
        help="Show items from last duration (e.g., '24h', '7d', '30m', '2w').",
    )(func)
    func = click.option(
        "--before",
        help="Show items before time (ISO or shorthand: '3d', '24h'). Upper bound for time window.",
    )(func)
    func = click.option(
        "--since",
        help="Show items since time (ISO or shorthand: '3d', '7d', '30m', '2w').",
    )(func)
    return func
