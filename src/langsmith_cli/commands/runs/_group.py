"""Runs command group and shared helpers."""

from typing import Any

import click

from rich.console import Console

console = Console()


@click.group()
def runs():
    """Inspect and filter application traces."""
    pass


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
