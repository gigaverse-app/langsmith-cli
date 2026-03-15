"""Cache subcommands for runs (download, list, clear, grep)."""

from typing import Any
import json

import click
from rich.table import Table

from langsmith_cli.commands.runs._group import console, runs
from langsmith_cli.utils import (
    add_metadata_filter_options,
    add_project_filter_options,
    add_time_filter_options,
    apply_grep_filter,
    apply_metadata_filter,
    build_metadata_fql_filters,
    build_runs_table,
    build_time_fql_filters,
    combine_fql_filters,
    count_option,
    fields_option,
    filter_fields,
    get_or_create_client,
    output_option,
    parse_fields_option,
    partition_metadata_filters,
    render_output,
    resolve_project_filters,
    write_output_to_file,
)


@runs.group("cache")
def cache_group():
    """Manage local JSONL cache of runs for fast offline analysis."""
    pass


@cache_group.command("download")
@add_project_filter_options
@add_time_filter_options
@click.option(
    "--filter",
    "additional_filter",
    help="Additional FQL filter to apply.",
)
@click.option(
    "--run-type",
    help="Filter by run type (llm, chain, tool, etc).",
)
@click.option(
    "--name-pattern",
    help="Only cache runs whose run name matches this pattern (e.g. 'Gigaverse_Daily_Standup' or '*standup*'). "
    "Exact names use server-side FQL (fast). Wildcard patterns (* ?) filter client-side. "
    "Filters by the run's name field — not by metadata, tags, or content.",
)
@click.option(
    "--full",
    is_flag=True,
    help="Re-fetch all runs in the time range (ignores incremental state, deduplicates safely).",
)
@click.option(
    "--workers",
    type=int,
    default=None,
    help="Number of parallel workers (default: min(4, num_projects)).",
)
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
    """Download runs to local JSONL cache for fast offline analysis.

    By default, incrementally fetches only new runs since last download.
    Uses parallel workers to fetch multiple projects simultaneously.
    Use --full to re-download everything.

    Examples:
        # Cache all runs from prd/* for last 7 days
        langsmith-cli runs cache download --project-name-pattern "prd/*" --last 7d

        # Incremental update (only new runs since last download)
        langsmith-cli runs cache download --project-name-pattern "prd/*"

        # Full re-download with 4 workers
        langsmith-cli runs cache download --project prd/video_moderation_service --full --workers 4

        # Cache only LLM runs
        langsmith-cli runs cache download --project-name-pattern "prd/*" --run-type llm

        # Cache only runs with a specific run name (exact → server-side FQL)
        langsmith-cli runs cache download --project dev/namedrop_service --name-pattern "FACTCHECK" --since 2026-01-15 --before 2026-01-29

        # Cache runs whose name matches a wildcard (client-side, downloads all then filters)
        langsmith-cli runs cache download --project dev/namedrop_service --name-pattern "*CHECK*"

        # Download only runs from a specific channel (metadata, exact → server-side FQL)
        langsmith-cli runs cache download --project dev/namedrop_service --metadata channel_id=Gigaverse_Daily_Standup

        # Download runs from channels matching a wildcard (metadata wildcard → client-side)
        langsmith-cli runs cache download --project dev/namedrop_service --metadata "channel_id=Gigaverse*"
    """
    import time
    from concurrent.futures import ThreadPoolExecutor, as_completed

    from langsmith_cli.cache import (
        append_runs_streaming,
        get_cache_path,
        get_existing_run_ids,
        read_cache_metadata,
    )

    logger = ctx.obj["logger"]
    is_json = ctx.obj.get("json", False)
    logger.use_stderr = is_json

    client = get_or_create_client(ctx)

    # Resolve projects
    pq = resolve_project_filters(
        client,
        project=project,
        project_id=project_id,
        name=project_name,
        name_exact=project_name_exact,
        name_pattern=project_name_pattern,
        name_regex=project_name_regex,
    )

    project_names = pq.names if not pq.use_id else [f"id:{pq.project_id}"]
    num_projects = len(project_names)
    num_workers = workers if workers else min(4, num_projects)

    # Build filters
    time_filters = build_time_fql_filters(since=since, last=last, before=before)
    base_filters = time_filters.copy()
    if additional_filter:
        base_filters.append(additional_filter)
    if run_type:
        base_filters.append(f'eq(run_type, "{run_type}")')

    # Run name filtering: exact name → FQL (server-side); wildcards → client-side
    # LangSmith FQL has no like()/regex operator for names, only eq() exact match.
    name_pattern_client: str | None = None
    if name_pattern:
        if "*" in name_pattern or "?" in name_pattern:
            name_pattern_client = name_pattern  # must filter client-side
        else:
            base_filters.append(f'eq(name, "{name_pattern}")')  # server-side

    # Metadata filtering: exact values → FQL (server-side); wildcards → client-side
    server_meta, client_meta = partition_metadata_filters(metadata_filters)
    if server_meta:
        base_filters.extend(build_metadata_fql_filters(server_meta))

    # Results tracking
    results: list[dict[str, Any]] = []
    overall_start = time.monotonic()

    def download_project(proj_name: str) -> dict[str, Any]:
        """Download runs for a single project. Runs in thread pool."""
        proj_start = time.monotonic()
        result: dict[str, Any] = {
            "project": proj_name,
            "status": "success",
            "new_runs": 0,
            "total_runs": 0,
            "size_mb": 0.0,
            "mode": "full",
            "elapsed_s": 0.0,
        }

        try:
            # Check for incremental update (--full skips incremental filter
            # but keeps existing cache data; dedup via existing_ids prevents
            # duplicates — this is safe even if the download fails mid-way)
            existing_meta = read_cache_metadata(proj_name)
            incremental_filters = base_filters.copy()
            if existing_meta and existing_meta.newest_run_start_time and not full:
                newest = existing_meta.newest_run_start_time.isoformat()
                incremental_filters.append(f'gt(start_time, "{newest}")')
                result["mode"] = "incremental"
                result["incremental_from"] = (
                    existing_meta.newest_run_start_time.isoformat()
                )

            combined_filter = combine_fql_filters(incremental_filters)

            # Pre-load existing IDs for dedup
            existing_ids = get_existing_run_ids(proj_name)

            # Build SDK kwargs
            proj_kwargs: dict[str, Any] = {}
            if proj_name.startswith("id:"):
                proj_kwargs["project_id"] = proj_name[3:]
            else:
                proj_kwargs["project_name"] = proj_name

            # Stream runs from SDK into cache with progress callback
            def on_batch(cumulative_count: int) -> None:
                result["new_runs"] = cumulative_count
                if progress_task_ids and proj_name in progress_task_ids:
                    progress.update(
                        progress_task_ids[proj_name],
                        completed=cumulative_count,
                    )
                if is_json:
                    import json as _json
                    import sys

                    print(
                        _json.dumps(
                            {
                                "event": "progress",
                                "project": proj_name,
                                "new_runs": cumulative_count,
                            }
                        ),
                        file=sys.stderr,
                        flush=True,
                    )

            runs_iter = client.list_runs(
                **proj_kwargs,
                filter=combined_filter,
                limit=None,
            )

            # Apply client-side name wildcard filter (only when pattern has * or ?)
            if name_pattern_client:
                import fnmatch

                runs_iter = (
                    r
                    for r in runs_iter
                    if fnmatch.fnmatch(r.name or "", name_pattern_client)
                )

            # Apply client-side metadata wildcard filter if needed
            if client_meta:
                runs_iter = apply_metadata_filter(runs_iter, client_meta)

            meta, new_count = append_runs_streaming(
                proj_name,
                runs_iter,
                existing_ids=existing_ids,
                on_progress=on_batch,
            )

            result["new_runs"] = new_count
            result["total_runs"] = meta.run_count
            cache_path = get_cache_path(proj_name)
            if cache_path.exists():
                result["size_mb"] = round(cache_path.stat().st_size / (1024 * 1024), 2)

            if new_count == 0:
                result["status"] = "no_new_runs"

        except Exception as e:
            result["status"] = "error"
            result["error"] = str(e)[:200]

        result["elapsed_s"] = round(time.monotonic() - proj_start, 1)
        return result

    # Choose progress display based on mode
    progress_task_ids: dict[str, Any] = {}

    if is_json:
        # Agent mode: emit structured progress to stderr
        import json as json_mod
        import sys

        logger.info(
            json_mod.dumps(
                {
                    "event": "download_start",
                    "projects": num_projects,
                    "workers": num_workers,
                }
            )
        )

        # No Rich Progress in JSON mode - use simple callbacks
        with ThreadPoolExecutor(max_workers=num_workers) as executor:
            futures = {
                executor.submit(download_project, name): name for name in project_names
            }
            for future in as_completed(futures):
                r = future.result()
                results.append(r)
                # Emit per-project completion to stderr
                print(
                    json_mod.dumps({"event": "project_done", **r}),
                    file=sys.stderr,
                    flush=True,
                )

        elapsed = round(time.monotonic() - overall_start, 1)
        total_new = sum(r["new_runs"] for r in results)
        total_cached = sum(r["total_runs"] for r in results)
        errors = [r for r in results if r["status"] == "error"]

        summary = {
            "event": "download_complete",
            "projects": num_projects,
            "total_new_runs": total_new,
            "total_cached_runs": total_cached,
            "errors": len(errors),
            "elapsed_s": elapsed,
            "results": results,
        }
        click.echo(json_mod.dumps(summary, default=str))

    else:
        # Human mode: Rich live progress
        from rich.progress import (
            BarColumn,
            Progress,
            SpinnerColumn,
            TextColumn,
            TimeElapsedColumn,
        )

        progress = Progress(
            SpinnerColumn(),
            TextColumn("[bold]{task.description}"),
            BarColumn(bar_width=30),
            TextColumn("{task.completed} runs"),
            TimeElapsedColumn(),
            console=logger.diagnostic_console,
        )

        overall_task = progress.add_task(
            f"[cyan]Downloading from {num_projects} project(s) ({num_workers} workers)",
            total=None,
        )

        # Create per-project tasks
        for name in project_names:
            task_id = progress.add_task(f"  {name}", total=None)
            progress_task_ids[name] = task_id

        with progress:
            with ThreadPoolExecutor(max_workers=num_workers) as executor:
                futures = {
                    executor.submit(download_project, name): name
                    for name in project_names
                }
                for future in as_completed(futures):
                    r = future.result()
                    results.append(r)
                    name = r["project"]
                    task_id = progress_task_ids[name]

                    if r["status"] == "error":
                        progress.update(
                            task_id,
                            description=f"  [red]{name}: FAILED[/red]",
                        )
                    elif r["new_runs"] == 0:
                        progress.update(
                            task_id,
                            description=f"  [dim]{name}: up to date[/dim]",
                        )
                    else:
                        progress.update(
                            task_id,
                            description=(
                                f"  [green]{name}: "
                                f"{r['new_runs']} new runs "
                                f"(total: {r['total_runs']}, "
                                f"{r['size_mb']:.1f}MB)[/green]"
                            ),
                        )
                    progress.update(task_id, completed=r["new_runs"])

            progress.update(overall_task, completed=num_projects, total=num_projects)

        # Print summary
        elapsed = round(time.monotonic() - overall_start, 1)
        total_new = sum(r["new_runs"] for r in results)
        errors = [r for r in results if r["status"] == "error"]

        logger.success(
            f"Done: {total_new} new runs cached from "
            f"{num_projects} project(s) in {elapsed}s"
        )
        if errors:
            for e in errors:
                logger.warning(
                    f"  Failed: {e['project']} - {e.get('error', 'unknown')}"
                )


@cache_group.command("dir")
@click.pass_context
def cache_dir(ctx: click.Context) -> None:
    """Print the cache directory path.

    Useful for piping to other tools:

    Examples:
        langsmith-cli runs cache dir
        duckdb -c "SELECT * FROM read_ndjson_auto('$(langsmith-cli runs cache dir)/*.jsonl')"
        cat "$(langsmith-cli runs cache dir)/my-project.jsonl" | jq '.name'
    """
    from langsmith_cli.cache import get_cache_dir

    click.echo(get_cache_dir())


@cache_group.command("list")
@click.pass_context
def cache_list(ctx: click.Context) -> None:
    """Show cached projects and their stats.

    Examples:
        langsmith-cli runs cache list
        langsmith-cli --json runs cache list
    """
    from langsmith_cli.cache import (
        find_orphaned_cache_files,
        get_cache_path,
        list_cached_projects,
    )

    logger = ctx.obj["logger"]

    projects = list_cached_projects()
    orphaned = find_orphaned_cache_files()

    if not projects and not orphaned:
        logger.info("No cached projects. Use 'runs cache download' to cache runs.")
        return

    if ctx.obj.get("json"):
        data = []
        for p in projects:
            entry = p.model_dump(mode="json")
            entry["path"] = str(get_cache_path(p.project_name))
            data.append(entry)
        click.echo(json.dumps(data, default=str))
        if orphaned:
            logger.warning(
                f"{len(orphaned)} orphaned JSONL file(s) have no metadata "
                f"(run 'runs cache repair' to fix): {', '.join(orphaned)}"
            )
        return

    table = Table(title="Cached Projects")
    table.add_column("Project", style="cyan")
    table.add_column("Runs", justify="right")
    table.add_column("Time Range", style="green")
    table.add_column("Size", justify="right")
    table.add_column("Last Updated", style="yellow")

    for p in projects:
        cache_path = get_cache_path(p.project_name)
        size_mb = (
            cache_path.stat().st_size / (1024 * 1024) if cache_path.exists() else 0
        )

        time_range = ""
        if p.oldest_run_start_time and p.newest_run_start_time:
            oldest = p.oldest_run_start_time.strftime("%m-%d %H:%M")
            newest = p.newest_run_start_time.strftime("%m-%d %H:%M")
            time_range = f"{oldest} → {newest}"

        updated = p.last_updated.strftime("%Y-%m-%d %H:%M") if p.last_updated else ""

        table.add_row(
            p.project_name,
            str(p.run_count),
            time_range,
            f"{size_mb:.1f}MB",
            updated,
        )

    console.print(table)

    if orphaned:
        logger.warning(
            f"\n⚠️  {len(orphaned)} orphaned cache file(s) found (JSONL without metadata):"
        )
        for name in orphaned:
            logger.warning(f"  • {name}")
        logger.warning("Run 'langsmith-cli runs cache repair' to regenerate metadata.")


@cache_group.command("clear")
@click.option("--project", help="Clear cache for a specific project only.")
@click.option("--yes", is_flag=True, help="Skip confirmation prompt.")
@click.pass_context
def cache_clear(ctx: click.Context, project: str | None, yes: bool) -> None:
    """Clear cached run data.

    Examples:
        # Clear specific project
        langsmith-cli runs cache clear --project prd/video_moderation_service

        # Clear all cached data
        langsmith-cli runs cache clear --yes
    """
    from langsmith_cli.cache import clear_cache as do_clear

    logger = ctx.obj["logger"]

    if not project and not yes:
        click.confirm("Clear ALL cached run data?", abort=True)

    deleted = do_clear(project)
    if deleted:
        target = project or "all projects"
        logger.success(f"Cleared cache for {target} ({deleted} files)")
    else:
        logger.info("No cache files to clear.")


@cache_group.command("repair")
@click.option(
    "--project", help="Repair only this project (default: all orphaned files)."
)
@click.pass_context
def cache_repair(ctx: click.Context, project: str | None) -> None:
    """Regenerate missing metadata sidecars from JSONL cache files.

    A metadata sidecar (.meta.json) can go missing when a download is interrupted
    before the final write. This command scans each orphaned JSONL file and
    rebuilds the metadata (run count, time range) from its contents.

    Examples:
        # Repair all orphaned cache files
        langsmith-cli runs cache repair

        # Repair a specific project
        langsmith-cli runs cache repair --project dev/namedrop_service
    """
    from langsmith_cli.cache import (
        find_orphaned_cache_files,
        get_cache_path,
        repair_cache_metadata,
        sanitize_project_name,
    )

    logger = ctx.obj["logger"]

    if project:
        # Check if the project actually has an orphaned file or just needs repair
        cache_path = get_cache_path(project)
        if not cache_path.exists():
            raise click.ClickException(f"No cache file found for '{project}'.")
        targets = [sanitize_project_name(project)]
    else:
        targets = find_orphaned_cache_files()
        if not targets:
            logger.info("No orphaned cache files found. Everything looks healthy.")
            return

    for stem in targets:
        try:
            meta = repair_cache_metadata(stem)
            oldest = (
                meta.oldest_run_start_time.strftime("%Y-%m-%d")
                if meta.oldest_run_start_time
                else "?"
            )
            newest = (
                meta.newest_run_start_time.strftime("%Y-%m-%d")
                if meta.newest_run_start_time
                else "?"
            )
            logger.success(
                f"Repaired '{stem}': {meta.run_count} runs, {oldest} → {newest}"
            )
        except FileNotFoundError as e:
            logger.warning(f"Skipping '{stem}': {e}")
        except Exception as e:
            logger.warning(f"Failed to repair '{stem}': {e}")


@cache_group.command("schema")
@click.option("--project", required=True, help="Cached project name.")
@click.option(
    "--sample-size",
    default=20,
    type=int,
    help="Number of runs to sample (default: 20).",
)
@click.option(
    "--include",
    type=str,
    help="Only show fields starting with these paths (comma-separated, e.g., 'inputs,outputs').",
)
@click.option(
    "--max-depth",
    default=8,
    type=int,
    help="Maximum nesting depth to display (default: 8).",
)
@click.pass_context
def cache_schema(
    ctx: click.Context,
    project: str,
    sample_size: int,
    include: str | None,
    max_depth: int,
) -> None:
    """Discover the nested structure of cached run data.

    Samples N runs from the cache and infers the schema of all fields,
    showing types, presence counts, and sample values. Useful for
    understanding the structure of inputs/outputs before writing queries.

    Examples:
        # Show full schema
        langsmith-cli runs cache schema --project dev/namedrop_service

        # Show only inputs and outputs structure
        langsmith-cli runs cache schema --project dev/namedrop_service --include inputs,outputs

        # JSON output for agents
        langsmith-cli --json runs cache schema --project dev/namedrop_service --include outputs
    """
    from langsmith_cli.cache import sample_raw_json_lines
    from langsmith_cli.field_analysis import (
        SchemaNode,
        filter_schema_by_paths,
        infer_schema,
        schema_to_dict,
    )

    logger = ctx.obj["logger"]
    is_json = ctx.obj.get("json", False)
    logger.use_stderr = is_json

    try:
        samples = sample_raw_json_lines(project, n=sample_size)
    except FileNotFoundError:
        raise click.ClickException(
            f"No cache found for '{project}'. Run 'runs cache download' first."
        )

    if not samples:
        raise click.ClickException(
            f"Cache for '{project}' is empty. Run 'runs cache download' first."
        )

    schema = infer_schema(samples, max_depth=max_depth)

    if include:
        include_paths = [p.strip() for p in include.split(",") if p.strip()]
        schema = filter_schema_by_paths(schema, include_paths)

    actual_sample_size = len(samples)

    if is_json:
        data = {
            "project": project,
            "sample_size": actual_sample_size,
            "schema": schema_to_dict(schema),
        }
        click.echo(json.dumps(data, default=str))
        return

    # Human-readable: Rich Tree
    from rich.tree import Tree

    tree = Tree(
        f"[bold cyan]Schema for {project}[/bold cyan] "
        f"({actual_sample_size} runs sampled)"
    )

    def _add_node(parent: Tree, name: str, node: SchemaNode, total: int) -> None:
        """Recursively add schema nodes to the tree."""
        presence = f"{node.present}/{total}"
        sample_str = f'  "{node.sample}"' if node.sample else ""

        if node.field_type == "dict" and node.children:
            branch = parent.add(f"[cyan]{name}[/cyan]: [dim]dict[/dim] ({presence})")
            for child_name in sorted(node.children):
                _add_node(branch, child_name, node.children[child_name], total)
        elif node.field_type == "list" and node.element_children:
            elem_type = node.element_type or "?"
            branch = parent.add(
                f"[cyan]{name}[/cyan]: [dim]list[{elem_type}][/dim] ({presence})"
            )
            for child_name in sorted(node.element_children):
                _add_node(
                    branch,
                    f"[].{child_name}",
                    node.element_children[child_name],
                    total,
                )
        elif node.field_type == "list":
            elem_type = node.element_type or "?"
            parent.add(
                f"[cyan]{name}[/cyan]: [dim]list[{elem_type}][/dim] "
                f"({presence}){sample_str}"
            )
        else:
            parent.add(
                f"[cyan]{name}[/cyan]: [dim]{node.field_type}[/dim] "
                f"({presence}){sample_str}"
            )

    for field_name in sorted(schema):
        _add_node(tree, field_name, schema[field_name], actual_sample_size)

    console.print(tree)


@cache_group.command("grep")
@click.argument("pattern")
@click.option("--project", help="Search only a specific project's cache.")
@click.option(
    "--ignore-case",
    "-i",
    is_flag=True,
    help="Case-insensitive search.",
)
@click.option(
    "--regex",
    "-E",
    is_flag=True,
    help="Treat pattern as regex.",
)
@click.option(
    "--grep-in",
    help="Comma-separated fields to search in (e.g., 'inputs,outputs,error'). "
    "Searches all fields if not specified.",
)
@click.option("--limit", default=20, help="Max results to return (default 20).")
@add_time_filter_options
@fields_option()
@count_option()
@output_option()
@add_metadata_filter_options
@click.pass_context
def cache_grep(
    ctx: click.Context,
    pattern: str,
    project: str | None,
    ignore_case: bool,
    regex: bool,
    grep_in: str | None,
    limit: int,
    since: str | None,
    before: str | None,
    last: str | None,
    fields: str | None,
    count: bool,
    output: str | None,
    metadata_filters: tuple[str, ...],
) -> None:
    """Search cached runs for a text pattern in inputs/outputs/error.

    Examples:
        # Search all cached projects for "hello"
        langsmith-cli runs cache grep "hello"

        # Case-insensitive regex search in a specific project
        langsmith-cli runs cache grep -i -E "\\\\buser_id\\\\b" --project my-proj

        # Search only inputs field, output as JSON
        langsmith-cli --json runs cache grep "error" --grep-in inputs
    """
    from langsmith_cli.cache import (
        find_orphaned_cache_files,
        get_cache_path,
        list_cached_projects,
        load_runs_from_cache,
    )
    from langsmith_cli.filters import parse_time_filter

    logger = ctx.obj["logger"]
    is_machine_readable = ctx.obj.get("json") or bool(output) or bool(fields)
    logger.use_stderr = is_machine_readable

    # Determine which projects to search
    if project:
        # Check if this specific project has a JSONL but no meta — suggest repair
        cache_path = get_cache_path(project)
        if (
            cache_path.exists()
            and not (cache_path.parent / (cache_path.stem + ".meta.json")).exists()
        ):
            logger.warning(
                f"Cache for '{project}' has no metadata sidecar "
                f"(likely an interrupted download). "
                f"Run 'langsmith-cli runs cache repair --project {project}' to fix."
            )
        project_names = [project]
    else:
        cached = list_cached_projects()
        project_names = [p.project_name for p in cached]
        orphaned = find_orphaned_cache_files()
        if orphaned:
            logger.warning(
                f"{len(orphaned)} orphaned cache file(s) are not being searched "
                f"(missing metadata). Run 'langsmith-cli runs cache repair' to include them."
            )

    if not project_names:
        logger.warning("No cached projects. Run 'runs cache download' first.")
        if ctx.obj.get("json"):
            click.echo(json.dumps([]))
        return

    # Parse time filters
    since_dt, until_dt = parse_time_filter(since=since, last=last, before=before)

    logger.info(f"Searching {len(project_names)} cached project(s) for '{pattern}'...")
    result = load_runs_from_cache(project_names, since=since_dt, until=until_dt)

    if not result.items:
        logger.warning("No cached runs found.")
        if ctx.obj.get("json"):
            click.echo(json.dumps([]))
        return

    # Apply metadata filter (always client-side for cached data)
    filtered_items = list(apply_metadata_filter(result.items, metadata_filters))

    if not filtered_items:
        logger.warning("No cached runs matched the metadata filter.")
        if ctx.obj.get("json"):
            click.echo(json.dumps([]))
        return

    # Parse grep-in fields
    grep_fields_tuple: tuple[str, ...] = ()
    if grep_in:
        grep_fields_tuple = tuple(f.strip() for f in grep_in.split(",") if f.strip())

    # Apply grep filter
    matched = apply_grep_filter(
        filtered_items,
        grep_pattern=pattern,
        grep_fields=grep_fields_tuple,
        ignore_case=ignore_case,
        use_regex=regex,
    )

    # Apply limit
    if limit and len(matched) > limit:
        matched = matched[:limit]

    # Handle file output
    if output:
        data = filter_fields(matched, fields)
        write_output_to_file(data, output, console, format_type="jsonl")
        return

    include_fields = parse_fields_option(fields)

    render_output(
        matched,
        lambda runs: build_runs_table(runs, f"Runs matching '{pattern}'"),
        ctx,
        include_fields=include_fields,
        empty_message=f"No runs matching '{pattern}' found",
        count_flag=count,
    )
