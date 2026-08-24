"""Pure transfer workflow shared by `runs pull` and compatibility commands."""

from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING, Any, Protocol

from langsmith_cli.local_traces.models import (
    TraceCacheWriteResult,
    TracePullRequest,
    TraceSelection,
    TraceSource,
)

if TYPE_CHECKING:
    from langsmith.schemas import Run
    from langsmith_cli.local_traces.repository import LocalTraceRepository


class RunsClient(Protocol):
    def list_runs(self, **kwargs: Any) -> Iterator[Run]: ...


def materialize_traces(
    selection: TraceSelection,
    repository: LocalTraceRepository,
    *,
    client: RunsClient | None = None,
) -> TraceCacheWriteResult:
    """Select complete trace bundles and publish one explicit local addition."""
    if selection.source is TraceSource.CLOUD:
        if client is None:
            raise ValueError("Cloud trace materialization requires a LangSmith client")
        selected = select_cloud_runs(client, selection)
        complete = complete_cloud_traces(client, selected)
    else:
        if selection.filter is not None:
            raise ValueError("Archive pulls do not support raw FQL filters")
        selected = select_archive_runs(selection)
        complete = complete_archive_traces(selection, selected)
    return publish_selected_traces(selection, selected, complete, repository)


def publish_selected_traces(
    selection: TraceSelection,
    selected: list[Run],
    complete: list[Run],
    repository: LocalTraceRepository,
) -> TraceCacheWriteResult:
    """Publish an already selected and trace-expanded remote working set."""
    if not selected and not complete:
        # An empty remote selection is a successful no-op. There is no SDK Run
        # from which to derive the stable project UUID, so no coverage-ledger
        # record can be published without weakening the identity invariant.
        catalog = repository.read_catalog()
        from langsmith_cli.trace_query import RunQuery

        return TraceCacheWriteResult(
            added_run_count=0,
            selected_run_count=0,
            total_run_count=repository.count(RunQuery(limit=None)),
            fragment_count=len(catalog.fragments),
            content_digest=None,
        )
    project_id = one_project_id(selection.project_name, complete or selected)
    request = TracePullRequest(
        source=selection.source,
        project_id=project_id,
        project_name=selection.project_name,
        requested_at=selection.requested_at,
        since=selection.since,
        before=selection.before,
        filter=selection.filter,
        trace_ids=tuple(sorted({str(run.trace_id or run.id) for run in selected})),
    )
    return repository.add_runs(request, complete)


def select_cloud_runs(client: RunsClient, selection: TraceSelection) -> list[Run]:
    filters = [selection.filter] if selection.filter is not None else []
    if selection.before is not None:
        filters.append(f'lt(start_time, "{selection.before.isoformat()}")')
    combined_filter = None
    if len(filters) == 1:
        combined_filter = filters[0]
    elif filters:
        combined_filter = "and(" + ", ".join(filters) + ")"
    return list(
        client.list_runs(
            project_name=selection.project_name,
            start_time=selection.since,
            filter=combined_filter,
            limit=selection.limit,
        )
    )


def complete_cloud_traces(client: RunsClient, selected: list[Run]) -> list[Run]:
    # The selection is already known-good source data. Seed with it so an
    # eventually-consistent trace expansion can add members but never erase the
    # rows that caused the user to select the trace.
    by_id: dict[str, Run] = {str(run.id): run for run in selected}
    trace_ids = {str(run.trace_id or run.id) for run in selected}
    for trace_id in trace_ids:
        for member in client.list_runs(trace_id=trace_id, limit=None):
            by_id[str(member.id)] = member
    return list(by_id.values())


def select_archive_runs(selection: TraceSelection) -> list[Run]:
    from langsmith_cli.archive.query import query_archive_runs
    from langsmith_cli.trace_query import RunQuery

    return query_archive_runs(
        RunQuery(
            project=selection.project_name,
            since=selection.since,
            before=selection.before,
            limit=selection.limit,
        )
    )


def complete_archive_traces(
    selection: TraceSelection, selected: list[Run]
) -> list[Run]:
    from langsmith_cli.archive.query import query_archive_runs
    from langsmith_cli.trace_query import RunQuery

    by_id: dict[str, Run] = {str(run.id): run for run in selected}
    trace_ids = tuple(sorted({str(run.trace_id or run.id) for run in selected}))
    if not trace_ids:
        return list(by_id.values())
    for member in query_archive_runs(
        RunQuery(
            project=selection.project_name,
            trace_ids=trace_ids,
            limit=None,
        )
    ):
        by_id[str(member.id)] = member
    return list(by_id.values())


def one_project_id(project_name: str, runs: list[Run]) -> str:
    project_ids = {str(run.session_id) for run in runs if run.session_id is not None}
    if len(project_ids) != 1:
        raise ValueError(
            f"Selected runs for {project_name!r} do not identify exactly one project"
        )
    return project_ids.pop()
