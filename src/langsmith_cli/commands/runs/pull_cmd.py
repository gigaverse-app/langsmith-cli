"""Explicit trace transfers between readable sources and the local inventory."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

import click

from langsmith_cli.commands.runs._group import console, runs
from langsmith_cli.utils import (
    add_time_filter_options,
    configure_logger_streams,
    get_or_create_client,
    is_json_context,
)

if TYPE_CHECKING:
    from langsmith.schemas import Run


@runs.command("pull")
@click.option(
    "--source",
    type=click.Choice(["cloud", "archive"]),
    required=True,
    help="Explicit source to read; pulls never happen during ordinary queries.",
)
@click.option(
    "--to",
    "destination",
    type=click.Choice(["local"]),
    required=True,
    help="Destination working cache.",
)
@click.option("--project", required=True, help="Exact project name to transfer.")
@add_time_filter_options
@click.option("--filter", "filter_", help="Source-native filter for selected runs.")
@click.option("--limit", type=int, default=100, show_default=True)
@click.pass_context
def pull_runs(
    ctx: click.Context,
    source: str,
    destination: str,
    project: str,
    since: str | None,
    before: str | None,
    last: str | None,
    filter_: str | None,
    limit: int,
) -> None:
    """Explicitly add selected complete traces to the local working cache.

    Reading cloud, archive, or local never invokes this command implicitly.

    \b
    Examples:
      langsmith-cli runs pull --source cloud --to local --project my-agent --last 7d
      langsmith-cli runs pull --source archive --to local --project my-agent --last 90d
    """
    from langsmith_cli.local_traces.models import TracePullRequest, TraceSource
    from langsmith_cli.local_traces.service import local_trace_repository
    from langsmith_cli.time_parsing import parse_time_range

    if destination != "local":
        raise click.ClickException(f"Unsupported trace destination: {destination}")
    if limit < 0:
        raise click.ClickException("--limit must be non-negative")
    configure_logger_streams(ctx, ctx.obj["logger"])
    since_dt, before_dt = parse_time_range(since=since, before=before, last=last)
    origin = TraceSource(source)
    if origin is TraceSource.CLOUD:
        selected = _select_cloud_runs(
            ctx,
            project=project,
            since=since_dt,
            before=before_dt,
            filter_=filter_,
            limit=limit,
        )
        complete = _complete_cloud_traces(ctx, selected)
    else:
        if filter_ is not None:
            raise click.ClickException(
                "Archive pulls do not support raw --filter; use typed run flags"
            )
        selected = _select_archive_runs(
            project=project,
            since=since_dt,
            before=before_dt,
            limit=limit,
        )
        complete = _complete_archive_traces(
            selected,
            project=project,
            since=since_dt,
            before=before_dt,
        )
    project_id = _one_project_id(project, complete or selected)
    request = TracePullRequest(
        source=origin,
        project_id=project_id,
        project_name=project,
        requested_at=datetime.now(timezone.utc),
        since=since_dt,
        before=before_dt,
        filter=filter_,
        trace_ids=tuple(sorted({str(run.trace_id or run.id) for run in selected})),
    )
    result = local_trace_repository().add_runs(request, complete)
    if is_json_context(ctx):
        click.echo(result.model_dump_json())
        return
    console.print(
        f"Added {result.added_run_count} run(s) from {source}; "
        f"local inventory now contains {result.total_run_count} run(s)."
    )


def _select_cloud_runs(
    ctx: click.Context,
    *,
    project: str,
    since: datetime | None,
    before: datetime | None,
    filter_: str | None,
    limit: int,
) -> list[Run]:
    client = get_or_create_client(ctx)
    filters = [filter_] if filter_ is not None else []
    if before is not None:
        filters.append(f'lt(start_time, "{before.isoformat()}")')
    combined_filter = None
    if len(filters) == 1:
        combined_filter = filters[0]
    elif filters:
        combined_filter = "and(" + ", ".join(filters) + ")"
    return list(
        client.list_runs(
            project_name=project,
            start_time=since,
            filter=combined_filter,
            limit=None if limit == 0 else limit,
        )
    )


def _complete_cloud_traces(ctx: click.Context, selected: list[Run]) -> list[Run]:
    client = get_or_create_client(ctx)
    by_id: dict[str, Run] = {}
    for run in selected:
        trace_id = str(run.trace_id or run.id)
        for member in client.list_runs(trace_id=trace_id, limit=None):
            by_id[str(member.id)] = member
    return list(by_id.values())


def _select_archive_runs(
    *,
    project: str,
    since: datetime | None,
    before: datetime | None,
    limit: int,
) -> list[Run]:
    from langsmith_cli.archive.query import query_archive_runs
    from langsmith_cli.trace_query import RunQuery

    return query_archive_runs(
        RunQuery(
            project=project,
            since=since,
            before=before,
            limit=None if limit == 0 else limit,
        )
    )


def _complete_archive_traces(
    selected: list[Run],
    *,
    project: str,
    since: datetime | None,
    before: datetime | None,
) -> list[Run]:
    from langsmith_cli.archive.query import query_archive_runs
    from langsmith_cli.trace_query import RunQuery

    by_id: dict[str, Run] = {}
    for trace_id in {str(run.trace_id or run.id) for run in selected}:
        for member in query_archive_runs(
            RunQuery(
                project=project,
                since=since,
                before=before,
                trace_id=trace_id,
                limit=None,
            )
        ):
            by_id[str(member.id)] = member
    return list(by_id.values())


def _one_project_id(project_name: str, runs: list[Run]) -> str:
    project_ids = {str(run.session_id) for run in runs if run.session_id is not None}
    if len(project_ids) != 1:
        raise click.ClickException(
            f"Selected runs for {project_name!r} do not identify exactly one project"
        )
    return project_ids.pop()
