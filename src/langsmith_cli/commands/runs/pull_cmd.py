"""Explicit trace transfers between readable sources and the local inventory."""

from __future__ import annotations

from datetime import datetime, timezone

import click

from langsmith_cli.commands.runs._group import console, runs
from langsmith_cli.utils import (
    add_time_filter_options,
    configure_logger_streams,
    get_or_create_client,
    is_json_context,
)


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
    from langsmith_cli.local_traces.models import TraceSelection, TraceSource
    from langsmith_cli.local_traces.service import local_trace_repository
    from langsmith_cli.local_traces.transfer import materialize_traces
    from langsmith_cli.time_parsing import parse_time_range

    configure_logger_streams(ctx, ctx.obj["logger"])
    since_dt, before_dt = parse_time_range(since=since, before=before, last=last)
    selection = TraceSelection(
        source=TraceSource(source),
        project_name=project,
        requested_at=datetime.now(timezone.utc),
        since=since_dt,
        before=before_dt,
        filter=filter_,
        limit=None if limit == 0 else limit,
    )
    client = (
        get_or_create_client(ctx) if selection.source is TraceSource.CLOUD else None
    )
    try:
        result = materialize_traces(
            selection,
            local_trace_repository(),
            client=client,
        )
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    if is_json_context(ctx):
        click.echo(result.model_dump_json())
        return
    console.print(
        f"Added {result.added_run_count} run(s) from {source}; "
        f"local inventory now contains {result.total_run_count} run(s)."
    )
