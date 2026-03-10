"""Export command for runs."""

from typing import Any

import click
from langsmith.schemas import Run

from langsmith_cli.commands.runs._group import runs
from langsmith_cli.utils import (
    add_project_filter_options,
    add_time_filter_options,
    build_runs_list_filter,
    fetch_from_projects,
    fields_option,
    get_or_create_client,
    json_dumps,
    parse_fields_option,
    raise_if_all_failed_with_suggestions,
    resolve_project_filters,
)


@runs.command("export")
@click.argument("directory", type=click.Path())
@add_project_filter_options
@click.option("--limit", default=50, help="Max runs to export (default 50).")
@click.option(
    "--status", type=click.Choice(["success", "error"]), help="Filter by status."
)
@click.option("--filter", "filter_", help="LangSmith FQL filter.")
@click.option("--is-root", type=bool, help="Filter root traces only (true/false).")
@click.option(
    "--roots",
    is_flag=True,
    help="Export only root traces.",
)
@click.option("--run-type", help="Filter by run type (llm, chain, tool, etc).")
@click.option(
    "--tag",
    multiple=True,
    help="Filter by tag (can specify multiple).",
)
@add_time_filter_options
@click.option(
    "--filename-pattern",
    default="{run_id}.json",
    help="Filename pattern. Placeholders: {run_id}, {trace_id}, {index}, {name}. Default: {run_id}.json",
)
@fields_option()
@click.pass_context
def export_runs(
    ctx,
    directory,
    project,
    project_id,
    project_name,
    project_name_exact,
    project_name_pattern,
    project_name_regex,
    limit,
    status,
    filter_,
    is_root,
    roots,
    run_type,
    tag,
    since,
    before,
    last,
    filename_pattern,
    fields,
):
    """Export runs as individual JSON files to a directory.

    Each run is saved as a separate JSON file, enabling offline analysis
    and integration with AI coding agents.

    \b
    Examples:
        # Export last 50 root traces
        langsmith-cli runs export ./traces --project my-project --roots

        # Export error traces from last 24h
        langsmith-cli runs export ./errors --project my-project --status error --last 24h

        # Export with custom filenames
        langsmith-cli runs export ./traces --project my-project --filename-pattern "{name}_{run_id}.json"

        # Export with field pruning for smaller files
        langsmith-cli runs export ./traces --project my-project --fields name,inputs,outputs,status,latency
    """
    import re
    import pathlib

    logger = ctx.obj["logger"]
    logger.use_stderr = True  # Always use stderr for progress

    logger.debug(f"Exporting runs to: {directory}, limit={limit}")

    client = get_or_create_client(ctx)

    # Create output directory
    out_dir = pathlib.Path(directory)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Resolve project filters
    pq = resolve_project_filters(
        client,
        project=project,
        project_id=project_id,
        name=project_name,
        name_exact=project_name_exact,
        name_pattern=project_name_pattern,
        name_regex=project_name_regex,
    )
    projects_to_query = pq.names

    # Handle --roots flag
    if roots:
        is_root = True

    # Build filter using shared helper (reuse canonical filter builder)
    combined_filter, error_filter = build_runs_list_filter(
        filter_=filter_,
        status=status,
        tag=tag,
        since=since,
        before=before,
        last=last,
    )

    logger.info(f"Fetching up to {limit} runs from project(s)...")

    # Fetch runs using the shared fetch_from_projects helper
    def _fetch_runs(c: Any, proj: str | None, **kw: Any) -> Any:
        if proj is not None:
            return c.list_runs(project_name=proj, **kw)
        return c.list_runs(**kw)

    result = fetch_from_projects(
        client,
        projects_to_query,
        _fetch_runs,
        project_query=pq,
        limit=limit,
        error=error_filter,
        filter=combined_filter,
        run_type=run_type,
        is_root=is_root,
        console=None,
        show_warnings=False,
    )
    all_runs: list[Run] = result.items

    # If all sources failed, raise with suggestions (reports failures internally).
    # Otherwise, report partial failures.
    raise_if_all_failed_with_suggestions(result, client, pq, logger, "runs")
    if result.has_failures:
        result.report_failures_to_logger(logger)

    if not all_runs:
        if ctx.obj.get("json"):
            click.echo(
                json_dumps(
                    {"status": "success", "exported": 0, "directory": str(out_dir)}
                )
            )
        else:
            logger.warning("No runs found matching filters.")
        return

    # Apply limit across all projects
    if len(all_runs) > limit:
        all_runs = all_runs[:limit]

    include_fields = parse_fields_option(fields)

    def _sanitize_filename(name: str) -> str:
        """Sanitize a string for use as a filename."""
        safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
        safe = safe.strip(". ")
        if len(safe) > 200:
            safe = safe[:200]
        return safe or "unnamed"

    # Validate filename pattern before the loop
    _valid_pattern_vars = {"run_id", "trace_id", "index", "name"}
    try:
        filename_pattern.format(run_id="test", trace_id="test", index=0, name="test")
    except KeyError as e:
        raise click.ClickException(
            f"Invalid filename pattern variable {e}. "
            f"Valid variables: {{{', '.join(sorted(_valid_pattern_vars))}}}"
        )

    # Export runs sequentially (local file I/O is fast, no need for threads)
    exported_files: list[str] = []
    errors: list[dict[str, str]] = []

    for index, run in enumerate(all_runs):
        try:
            # Build filename from pattern
            safe_name = _sanitize_filename(run.name or "unnamed")
            fname = filename_pattern.format(
                run_id=run.id,
                trace_id=run.trace_id or run.id,
                index=index,
                name=safe_name,
            )

            # Dump run data
            if include_fields:
                data = run.model_dump(include=include_fields, mode="json")
            else:
                data = run.model_dump(mode="json")

            file_path = out_dir / fname
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(json_dumps(data, indent=2))

            exported_files.append(fname)
        except OSError as e:
            errors.append({"run_id": str(run.id), "error": str(e)})

    if ctx.obj.get("json"):
        click.echo(
            json_dumps(
                {
                    "status": "success",
                    "exported": len(exported_files),
                    "directory": str(out_dir),
                    "files": sorted(exported_files),
                    "errors": errors,
                }
            )
        )
    else:
        logger.success(f"Exported {len(exported_files)} run(s) to {out_dir}/")
        if errors:
            for err in errors:
                logger.warning(f"Failed to export {err['run_id']}: {err['error']}")
