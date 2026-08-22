"""Runs command group and shared helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable

import click

if TYPE_CHECKING:
    from langsmith_cli.local_traces.models import TraceSource


class LazyConsole:
    """Defer Rich Console construction until a command actually renders text."""

    def __init__(self) -> None:
        self._console: Any | None = None

    def _get_console(self) -> Any:
        if self._console is None:
            from rich.console import Console

            self._console = Console()
        return self._console

    def print(self, *args: Any, **kwargs: Any) -> None:
        self._get_console().print(*args, **kwargs)


console = LazyConsole()


@click.group()
def runs():
    """Inspect and filter application traces."""
    pass


def trace_source_options(function: Callable[..., Any]) -> Callable[..., Any]:
    """Apply the uniform source selector and the legacy archive alias."""
    function = click.option(
        "--archive",
        is_flag=True,
        help="Compatibility alias for --source archive.",
    )(function)
    return click.option(
        "--source",
        type=click.Choice(["cloud", "archive", "local"]),
        default=None,
        help="Trace source to query (default: cloud).",
    )(function)


def resolve_trace_source_cli(source: str | None, archive: bool) -> TraceSource:
    """Resolve CLI source flags and render conflicts as uniform Click errors."""
    from langsmith_cli.local_traces.service import resolve_trace_source

    try:
        return resolve_trace_source(source, archive)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc


# LangSmith API rejects limit > 100 in /runs/query requests.
# When we need more items, omit the limit from the SDK call
# (letting cursor pagination handle paging) and use islice to cap.
_API_MAX_LIMIT = 100


def _make_fetch_runs() -> Any:
    """Create a fetch function for list_runs that respects the API's max limit of 100.

    Returns a function suitable for use with fetch_from_projects.
    """
    from itertools import islice

    def _fetch_runs(c: Any, proj: str | None, **kw: Any) -> Any:
        requested_limit = kw.pop("limit", None)
        sdk_limit = requested_limit
        if requested_limit is not None and requested_limit > _API_MAX_LIMIT:
            sdk_limit = None

        if proj is not None:
            it = c.list_runs(project_name=proj, limit=sdk_limit, **kw)
        else:
            it = c.list_runs(limit=sdk_limit, **kw)

        if requested_limit is not None and requested_limit > _API_MAX_LIMIT:
            return list(islice(it, requested_limit))
        return it

    return _fetch_runs
