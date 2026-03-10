"""Runs get, get-latest, and view-file commands."""

from typing import Any
import json

import click
from langsmith.schemas import Run

from langsmith_cli.commands.runs._group import runs, console
from langsmith_cli.utils import (
    add_project_filter_options,
    build_runs_list_filter,
    build_runs_table,
    fields_option,
    filter_fields,
    get_or_create_client,
    get_project_suggestions,
    output_formatted_data,
    output_option,
    output_single_item,
    render_run_details,
    resolve_project_filters,
)


@runs.command("get")
@click.argument("run_id")
@fields_option(
    "Comma-separated field names to include (e.g., 'id,name,inputs,error'). Reduces context usage."
)
@output_option()
@click.pass_context
def get_run(ctx, run_id, fields, output):
    """Fetch details of a single run."""
    client = get_or_create_client(ctx)
    run = client.read_run(run_id)

    data = filter_fields(run, fields)
    output_single_item(ctx, data, console, output=output, render_fn=render_run_details)


@runs.command("get-latest")
@add_project_filter_options
@click.option(
    "--status", type=click.Choice(["success", "error"]), help="Filter by status."
)
@click.option(
    "--failed",
    is_flag=True,
    help="Show only failed runs (shorthand for --status error).",
)
@click.option(
    "--succeeded",
    is_flag=True,
    help="Show only successful runs (shorthand for --status success).",
)
@click.option("--roots", is_flag=True, help="Get latest root trace only.")
@click.option("--tag", multiple=True, help="Filter by tag (can specify multiple).")
@click.option("--model", help="Filter by model name (e.g. 'gpt-4', 'claude-3').")
@click.option("--slow", is_flag=True, help="Filter to slow runs (latency > 5s).")
@click.option("--recent", is_flag=True, help="Filter to recent runs (last hour).")
@click.option("--today", is_flag=True, help="Filter to today's runs.")
@click.option("--min-latency", help="Minimum latency (e.g., '2s', '500ms').")
@click.option("--max-latency", help="Maximum latency (e.g., '10s', '2000ms').")
@click.option(
    "--since", help="Show runs since time (ISO or relative like '1 hour ago')."
)
@click.option(
    "--before",
    help="Show runs before time (ISO format, '3d', or '3 days ago'). Upper bound for time window.",
)
@click.option("--last", help="Show runs from last duration (e.g., '24h', '7d', '30m').")
@click.option("--filter", "filter_", help="Custom FQL filter string.")
@fields_option(
    "Comma-separated field names (e.g., 'id,name,inputs,outputs'). Reduces context."
)
@output_option()
@click.pass_context
def get_latest_run(
    ctx,
    project,
    project_id,
    project_name,
    project_name_exact,
    project_name_pattern,
    project_name_regex,
    status,
    failed,
    succeeded,
    roots,
    tag,
    model,
    slow,
    recent,
    today,
    min_latency,
    max_latency,
    since,
    before,
    last,
    filter_,
    fields,
    output,
):
    """Get the most recent run from a project.

    This is a convenience command that fetches the latest run matching your filters,
    eliminating the need for piping `runs list` into `jq` and then `runs get`.

    Examples:
        # Get latest run with just inputs/outputs
        langsmith-cli --json runs get-latest --project my-project --fields inputs,outputs

        # Get latest successful run
        langsmith-cli --json runs get-latest --project my-project --succeeded

        # Get latest error from production projects
        langsmith-cli --json runs get-latest --project-name-pattern "prd/*" --failed --fields id,name,error

        # Get latest slow run from last hour
        langsmith-cli --json runs get-latest --project my-project --slow --recent --fields name,latency
    """
    logger = ctx.obj["logger"]

    # Determine if output is machine-readable (use stderr for diagnostics)
    is_machine_readable = ctx.obj.get("json") or fields or output
    logger.use_stderr = is_machine_readable

    client = get_or_create_client(ctx)
    logger.debug(f"Getting latest run with filters: project={project}, status={status}")

    # Get matching projects
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

    # Build filter using shared helper
    combined_filter, error_filter = build_runs_list_filter(
        filter_=filter_,
        status=status,
        failed=failed,
        succeeded=succeeded,
        tag=tag,
        model=model,
        slow=slow,
        recent=recent,
        today=today,
        min_latency=min_latency,
        max_latency=max_latency,
        since=since,
        before=before,
        last=last,
    )

    # Search projects in order until we find a run
    latest_run = None
    failed_projects = []
    run_kwargs: dict[str, Any] = dict(
        limit=1,
        error=error_filter,
        filter=combined_filter,
        is_root=roots,
    )

    if pq.use_id:
        # Direct project ID lookup - no iteration needed
        try:
            runs_iter = client.list_runs(project_id=pq.project_id, **run_kwargs)
            latest_run = next(runs_iter, None)
        except Exception as e:
            failed_projects.append((f"id:{pq.project_id}", str(e)))
    else:
        for proj_name in projects_to_query:
            try:
                runs_iter = client.list_runs(project_name=proj_name, **run_kwargs)
                latest_run = next(runs_iter, None)
                if latest_run:
                    break  # Found a run, stop searching
            except Exception as e:
                failed_projects.append((proj_name, str(e)))
                continue

    if not latest_run:
        logger.warning("No runs found matching the specified filters")

        # Show failed projects if any
        if failed_projects:
            logger.warning("Some projects failed to fetch:")
            for proj, error_msg in failed_projects[:3]:
                short_error = (
                    error_msg[:100] + "..." if len(error_msg) > 100 else error_msg
                )
                logger.warning(f"  • {proj}: {short_error}")
            if len(failed_projects) > 3:
                logger.warning(f"  • ... and {len(failed_projects) - 3} more")

            # Suggest similar project names for single-project failures
            failed_names = [
                name for name, _ in failed_projects if not name.startswith("id:")
            ]
            if len(failed_names) == 1:
                suggestions = get_project_suggestions(client, failed_names[0])
                if suggestions:
                    suggestion_list = ", ".join(f"'{s}'" for s in suggestions[:5])
                    logger.info(f"Did you mean: {suggestion_list}?")

        raise click.Abort()

    data = filter_fields(latest_run, fields)

    def render_latest(data: dict, console: Any) -> None:
        render_run_details(data, console, title="Latest Run")

    output_single_item(ctx, data, console, output=output, render_fn=render_latest)


@runs.command("view-file")
@click.argument("pattern")
@click.option(
    "--no-truncate",
    is_flag=True,
    help="Don't truncate long fields in table output (shows full content in all columns).",
)
@fields_option()
@click.pass_context
def view_file(ctx, pattern, no_truncate, fields):
    """View runs from JSONL files with table display.

    Supports glob patterns to read multiple files.

    Examples:
        langsmith-cli runs view-file samples.jsonl
        langsmith-cli runs view-file "data/*.jsonl"
        langsmith-cli runs view-file samples.jsonl --no-truncate
        langsmith-cli runs view-file samples.jsonl --fields id,name,status
        langsmith-cli --json runs view-file samples.jsonl
    """
    logger = ctx.obj["logger"]

    # Determine if output is machine-readable
    is_machine_readable = ctx.obj.get("json") or fields
    logger.use_stderr = is_machine_readable

    import glob

    # Find matching files using glob
    file_paths = glob.glob(pattern)

    if not file_paths:
        logger.error(f"No files match pattern: {pattern}")
        raise click.Abort()

    # Read all runs from matching files
    all_runs: list[Run] = []
    for file_path in sorted(file_paths):
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                for line_num, line in enumerate(f, 1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        # Convert dict to Run object using Pydantic validation
                        run = Run.model_validate(data)
                        all_runs.append(run)
                    except json.JSONDecodeError as e:
                        logger.warning(f"Invalid JSON at {file_path}:{line_num} - {e}")
                    except Exception as e:
                        logger.warning(
                            f"Failed to parse run at {file_path}:{line_num} - {e}"
                        )
        except Exception as e:
            logger.error(f"Error reading {file_path}: {e}")
            continue

    if not all_runs:
        logger.warning("No valid runs found in files.")
        if ctx.obj.get("json"):
            click.echo(json.dumps([]))
        return

    # Handle JSON output
    if ctx.obj.get("json"):
        data = filter_fields(all_runs, fields)
        output_formatted_data(data, "json")
        return

    # Build descriptive title
    if len(file_paths) == 1:
        table_title = f"Runs from {file_paths[0]}"
    else:
        table_title = f"Runs from {len(file_paths)} files"

    # Use shared table builder utility
    table = build_runs_table(all_runs, table_title, no_truncate)

    if len(all_runs) == 0:
        logger.warning("No runs found.")
    else:
        console.print(table)
        logger.info(f"Loaded {len(all_runs)} runs from {len(file_paths)} file(s)")
