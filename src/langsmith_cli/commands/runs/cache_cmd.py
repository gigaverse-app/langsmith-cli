"""Compatibility lifecycle commands for the shared local trace inventory."""

from __future__ import annotations

from datetime import datetime, timezone

import click

from langsmith_cli.commands.runs._group import console, runs
from langsmith_cli.utils import (
    add_grep_options,
    add_metadata_filter_options,
    add_project_filter_options,
    add_time_filter_options,
    apply_metadata_filter,
    build_metadata_fql_filters,
    combine_fql_filters,
    configure_logger_streams,
    confirm_option,
    count_option,
    fields_option,
    filter_fields,
    get_matching_items,
    get_or_create_client,
    is_json_context,
    output_formatted_data,
    output_option,
    partition_metadata_filters,
    render_output,
    require_confirmation,
    resolve_project_filters,
    write_output_to_file,
)


@runs.group("cache")
def cache_group() -> None:
    """Manage the DuckDB-over-Parquet local trace working cache."""


@cache_group.command("download")
@add_project_filter_options
@add_time_filter_options
@click.option("--filter", "additional_filter", help="Additional LangSmith FQL filter.")
@click.option("--run-type", help="Filter by run type.")
@click.option("--name-pattern", help="Filter selected run names; wildcards supported.")
@click.option("--full", is_flag=True, hidden=True)
@click.option("--workers", type=int, default=None, hidden=True)
@add_metadata_filter_options
@click.pass_context
def cache_download(
    ctx: click.Context,
    project: str | None,
    project_id: str | None,
    project_name: str | None,
    project_name_exact: str | None,
    project_name_pattern: str | None,
    project_name_regex: str | None,
    since: str | None,
    before: str | None,
    last: str | None,
    additional_filter: str | None,
    run_type: str | None,
    name_pattern: str | None,
    full: bool,
    workers: int | None,
    metadata_filters: tuple[str, ...],
) -> None:
    """Compatibility alias for explicit cloud-to-local trace materialization.

    New workflows should use ``runs pull --source cloud --to local``. This command
    adapts its historical project/filter options to that same transfer service; it
    never writes JSONL.
    """
    del full, workers
    from langsmith_cli.local_traces.models import (
        ProjectTraceCacheWriteResult,
        TraceSelection,
        TraceSource,
    )
    from langsmith_cli.local_traces.service import local_trace_repository
    from langsmith_cli.local_traces.transfer import (
        complete_cloud_traces,
        publish_selected_traces,
        select_cloud_runs,
    )
    from langsmith_cli.time_parsing import parse_time_range

    logger = ctx.obj["logger"]
    configure_logger_streams(ctx, logger)
    client = get_or_create_client(ctx)
    resolved = resolve_project_filters(
        client,
        project=project,
        project_id=project_id,
        name=project_name,
        name_exact=project_name_exact,
        name_pattern=project_name_pattern,
        name_regex=project_name_regex,
    )
    if resolved.use_id:
        raise click.ClickException(
            "runs cache download requires a project name; use --project instead of --project-id"
        )
    since_dt, before_dt = parse_time_range(since=since, before=before, last=last)
    exact_metadata, client_metadata = partition_metadata_filters(metadata_filters)
    fql_filters = list(build_metadata_fql_filters(exact_metadata))
    if additional_filter is not None:
        fql_filters.append(additional_filter)
    if run_type is not None:
        fql_filters.append(f'eq(run_type, "{run_type}")')
    exact_name = name_pattern is not None and not any(
        character in name_pattern for character in "*?["
    )
    if exact_name:
        fql_filters.append(f'eq(name, "{name_pattern}")')

    results = []
    repository = local_trace_repository()
    for resolved_name in resolved.names:
        selection = TraceSelection(
            source=TraceSource.CLOUD,
            project_name=resolved_name,
            requested_at=datetime.now(timezone.utc),
            since=since_dt,
            before=before_dt,
            filter=combine_fql_filters(fql_filters),
            limit=None,
        )
        selected = select_cloud_runs(client, selection)
        if name_pattern is not None and not exact_name:
            selected = get_matching_items(
                selected,
                name_pattern=name_pattern,
                name_getter=lambda run: run.name or "",
            )
        if client_metadata:
            selected = apply_metadata_filter(selected, client_metadata)
        complete = complete_cloud_traces(client, selected)
        try:
            result = publish_selected_traces(selection, selected, complete, repository)
        except ValueError as exc:
            raise click.ClickException(str(exc)) from exc
        results.append(
            ProjectTraceCacheWriteResult(project=resolved_name, result=result)
        )
    if is_json_context(ctx):
        output_formatted_data(
            [result.model_dump(mode="json") for result in results], "json"
        )
        return
    for result in results:
        console.print(
            f"{result.project}: added {result.result.added_run_count} run(s), "
            f"{result.result.total_run_count} total"
        )


@cache_group.command("dir")
@click.pass_context
def cache_dir(ctx: click.Context) -> None:
    """Print the shared local trace inventory directory."""
    from langsmith_cli.cache import get_cache_dir

    configure_logger_streams(ctx, ctx.obj["logger"])
    click.echo(str(get_cache_dir()))


@cache_group.command("list")
@click.option(
    "--format",
    "format_type",
    type=click.Choice(["table", "json", "jsonl", "csv", "yaml"]),
    default=None,
)
@fields_option()
@count_option()
@output_option()
@click.pass_context
def cache_list(
    ctx: click.Context,
    format_type: str | None,
    fields: str | None,
    count: bool,
    output: str | None,
) -> None:
    """List projects represented in the shared local inventory."""
    from langsmith_cli.cache import list_cached_projects

    projects = list_cached_projects()
    rows = [metadata.model_dump(mode="json") for metadata in projects]
    if count:
        click.echo(str(len(rows)))
        return
    selected = filter_fields(projects, fields)
    machine_format = format_type or ("json" if is_json_context(ctx) else "table")
    if output is not None:
        write_output_to_file(selected, output, console, format_type=machine_format)
        return
    if machine_format != "table":
        output_formatted_data(selected, machine_format)
        return
    render_output(
        rows,
        columns=[
            ("Project", "project_name"),
            ("Runs", "run_count"),
            ("Fragments", "fragment_count"),
            ("Updated", "last_updated"),
        ],
        title="Local Trace Cache",
        empty_message="No local traces. Use 'runs pull --to local' first.",
        console=console,
    )


@cache_group.command("clear")
@click.option("--project", help="Evict one project's logical traces.")
@confirm_option()
@click.pass_context
def cache_clear(ctx: click.Context, project: str | None, confirm: bool) -> None:
    """Atomically evict local trace reachability."""
    from langsmith_cli.local_traces.service import local_trace_repository

    require_confirmation(
        confirm,
        "Evict the selected local trace working cache?",
    )
    result = local_trace_repository().evict(project)
    if is_json_context(ctx):
        click.echo(result.model_dump_json())
        return
    console.print(
        f"Evicted {result.removed_run_count} run(s) from "
        f"{result.removed_fragment_count} fragment(s)."
    )


@cache_group.command("repair")
@click.pass_context
def cache_repair(ctx: click.Context) -> None:
    """Validate the catalog and every reachable Parquet fragment."""
    from langsmith_cli.local_traces.models import TraceCacheHealth, TraceCacheStatus
    from langsmith_cli.local_traces.service import local_trace_repository
    from langsmith_cli.trace_query import RunQuery

    repository = local_trace_repository()
    catalog = repository.read_catalog()
    run_count = repository.count(RunQuery(limit=None))
    payload = TraceCacheHealth(
        status=TraceCacheStatus.HEALTHY,
        fragment_count=len(catalog.fragments),
        run_count=run_count,
    )
    if is_json_context(ctx):
        click.echo(payload.model_dump_json())
        return
    console.print(
        f"Healthy: {run_count} logical run(s) in {len(catalog.fragments)} fragment(s)."
    )


@cache_group.command("schema")
@click.option("--project", required=True)
@click.option("--sample", type=int, default=20, show_default=True)
@click.option("--include", multiple=True)
@click.pass_context
def cache_schema(
    ctx: click.Context,
    project: str,
    sample: int,
    include: tuple[str, ...],
) -> None:
    """Infer a human/JSON field schema from local Parquet rows."""
    from langsmith_cli.cache import sample_cached_rows
    from langsmith_cli.field_analysis import (
        filter_schema_by_paths,
        infer_schema,
        schema_to_dict,
    )

    try:
        rows = sample_cached_rows(project, sample)
    except FileNotFoundError as exc:
        raise click.ClickException(str(exc)) from exc
    schema = infer_schema(rows)
    if include:
        schema = filter_schema_by_paths(schema, include)
    payload = schema_to_dict(schema)
    if is_json_context(ctx):
        output_formatted_data(payload, "json")
        return
    output_formatted_data(payload, "yaml")


@cache_group.command("grep")
@click.argument("pattern")
@click.option("--project")
@add_grep_options
@click.option("--limit", default=20, show_default=True)
@add_metadata_filter_options
@count_option()
@fields_option()
@click.pass_context
def cache_grep(
    ctx: click.Context,
    pattern: str,
    project: str | None,
    grep: str | None,
    grep_ignore_case: bool,
    grep_regex: bool,
    grep_in: str | None,
    limit: int,
    metadata_filters: tuple[str, ...],
    count: bool,
    fields: str | None,
) -> None:
    """Compatibility alias for `runs list --source local --grep`."""
    del grep
    from langsmith_cli.commands.runs.list_cmd import list_runs

    ctx.invoke(
        list_runs,
        source="local",
        project=project,
        grep=pattern,
        grep_ignore_case=grep_ignore_case,
        grep_regex=grep_regex,
        grep_in=grep_in,
        limit=limit,
        metadata_filters=metadata_filters,
        count=count,
        fields=fields,
    )
