"""Runs list command."""

import click

from langsmith_cli.commands.runs._group import _make_fetch_runs, console, runs
from langsmith_cli.utils import (
    add_grep_options,
    add_metadata_filter_options,
    add_project_filter_options,
    apply_client_side_limit,
    apply_exclude_filter,
    apply_grep_filter,
    build_metadata_fql_filters,
    build_runs_table,
    build_tag_fql_filters,
    build_time_fql_filters,
    combine_fql_filters,
    count_option,
    determine_output_format,
    exclude_option,
    fetch_from_projects,
    fields_option,
    filter_fields,
    get_matching_items,
    get_or_create_client,
    output_formatted_data,
    output_option,
    parse_duration_to_seconds,
    raise_if_all_failed_with_suggestions,
    resolve_project_filters,
    sort_items,
    write_output_to_file,
)


@runs.command("list")
@add_project_filter_options
@click.option("--limit", default=20, help="Max runs to fetch (per project).")
@click.option(
    "--status", type=click.Choice(["success", "error"]), help="Filter by status."
)
@click.option(
    "--filter",
    "filter_",
    help='LangSmith FQL filter. Examples: eq(name, "extractor"), gt(latency, "5s"), has(tags, "prod"). See --help for full examples.',
)
@click.option("--trace-id", help="Get all runs in a specific trace.")
@click.option(
    "--run-type", help="Filter by run type (llm, chain, tool, retriever, etc)."
)
@click.option("--is-root", type=bool, help="Filter root traces only (true/false).")
@click.option(
    "--roots",
    is_flag=True,
    help="Show only root traces (shorthand for --is-root true). Recommended for cleaner output.",
)
@click.option("--trace-filter", help="Filter applied to root trace.")
@click.option("--tree-filter", help="Filter if any run in trace tree matches.")
@click.option("--reference-example-id", help="Filter runs for a specific example.")
@click.option(
    "--tag",
    multiple=True,
    help="Filter by tag (can specify multiple times for AND logic).",
)
@click.option(
    "--name-pattern",
    help="Filter run names with wildcards (e.g. '*auth*'). "
    "Uses client-side filtering - searches recent runs only. "
    "Increase --limit to search more runs.",
)
@click.option(
    "--name-regex",
    help="Filter run names with regex (e.g. '^test-.*-v[0-9]+$'). "
    "Uses client-side filtering - searches recent runs only. "
    "Increase --limit to search more runs.",
)
@click.option("--model", help="Filter by model name (e.g. 'gpt-4', 'claude-3').")
@click.option(
    "--failed",
    is_flag=True,
    help="Show only failed/error runs (equivalent to --status error).",
)
@click.option(
    "--succeeded",
    is_flag=True,
    help="Show only successful runs (equivalent to --status success).",
)
@click.option("--slow", is_flag=True, help="Filter to slow runs (latency > 5s).")
@click.option("--recent", is_flag=True, help="Filter to recent runs (last hour).")
@click.option("--today", is_flag=True, help="Filter to today's runs.")
@click.option("--min-latency", help="Minimum latency (e.g., '2s', '500ms', '1.5s').")
@click.option("--max-latency", help="Maximum latency (e.g., '10s', '2000ms').")
@click.option(
    "--since",
    help="Show runs since time (ISO format, '3d', or '3 days ago').",
)
@click.option(
    "--before",
    help="Show runs before time (ISO format, '3d', or '3 days ago'). Upper bound for time window.",
)
@click.option(
    "--last",
    help="Show runs from last duration (e.g., '24h', '7d', '30m', '2w').",
)
@click.option(
    "--query",
    help="Server-side full-text search in inputs/outputs (fast, but searches only first ~250 chars). Use --grep for unlimited content search.",
)
@add_grep_options
@click.option(
    "--fetch",
    type=int,
    help="Number of runs to fetch when using client-side filters (--grep, --name-pattern, etc.). Overrides automatic 3x multiplier. Example: --limit 10 --fetch 500 fetches 500 runs and returns up to 10 matches.",
)
@add_metadata_filter_options
@click.option(
    "--sort-by",
    help="Sort by field (name, status, latency, start_time). Prefix with - for descending.",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["table", "json", "csv", "yaml"]),
    help="Output format (default: table, or json if --json flag used).",
)
@click.option(
    "--no-truncate",
    is_flag=True,
    help="Don't truncate long fields in table output (shows full content in all columns).",
)
@exclude_option()
@fields_option()
@count_option()
@output_option()
@click.pass_context
def list_runs(
    ctx,
    project,
    project_id,
    project_name,
    project_name_exact,
    project_name_pattern,
    project_name_regex,
    limit,
    status,
    filter_,
    trace_id,
    run_type,
    is_root,
    roots,
    trace_filter,
    tree_filter,
    reference_example_id,
    tag,
    name_pattern,
    name_regex,
    model,
    failed,
    succeeded,
    slow,
    recent,
    today,
    min_latency,
    max_latency,
    since,
    before,
    last,
    query,
    grep,
    grep_ignore_case,
    grep_regex,
    grep_in,
    fetch,
    metadata_filters,
    sort_by,
    output_format,
    no_truncate,
    exclude,
    fields,
    count,
    output,
):
    """Fetch recent runs from one or more projects.

    Use project filters (--project-name, --project-name-pattern, --project-name-regex, --project-name-exact) to match multiple projects.
    Use run name filters (--name-pattern, --name-regex) to filter specific run names.

    \b
    FQL Filter Examples:
      # Filter by name
      --filter 'eq(name, "extractor")'

      # Filter by latency
      --filter 'gt(latency, "5s")'

      # Filter by tags
      --filter 'has(tags, "production")'

      # Combine multiple conditions
      --filter 'and(eq(run_type, "chain"), gt(latency, "10s"))'

      # Complex example: chains that took >10s and had >5000 tokens
      --filter 'and(eq(run_type, "chain"), gt(latency, "10s"), gt(total_tokens, 5000))'

    \b
    Search Examples:
      # Server-side text search (fast, first ~250 chars)
      --query "error message"

      # Client-side grep (slower, unlimited, regex)
      --grep "druze" --grep-in inputs,outputs

      # Regex search for Hebrew characters
      --grep "[\\u0590-\\u05FF]" --grep-regex --grep-in inputs
    """
    logger = ctx.obj["logger"]

    # Determine if output is machine-readable (use stderr for diagnostics)
    is_machine_readable = (
        ctx.obj.get("json") or output_format in ["csv", "yaml"] or count or output
    )
    logger.use_stderr = is_machine_readable

    # When --count is used, default to unlimited (0) unless user explicitly set limit
    # Check if limit was explicitly provided by checking if it's not the default
    if count and limit == 20:
        # User didn't explicitly set limit, so use 0 (unlimited) for counting
        limit = 0

    import datetime

    logger.debug(
        f"Listing runs with filters: project={project}, status={status}, limit={limit}"
    )

    client = get_or_create_client(ctx)

    # Resolve project filters (--project-id bypasses name resolution)
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

    # Handle --roots flag (convenience for --is-root true)
    if roots:
        is_root = True

    # Handle status filtering with multiple options
    error_filter = None
    if status == "error" or failed:
        error_filter = True
    elif status == "success" or succeeded:
        error_filter = False

    # Build FQL filter from smart flags
    fql_filters = []

    # Add user's custom filter first
    if filter_:
        fql_filters.append(filter_)

    # Tag filtering (AND logic - all tags must be present)
    if tag:
        fql_filters.extend(build_tag_fql_filters(tag))

    # Metadata filtering (server-side, fast)
    if metadata_filters:
        fql_filters.extend(build_metadata_fql_filters(metadata_filters))

    # Run name pattern - skip FQL filtering, do client-side instead
    # (FQL search doesn't support proper wildcard matching)

    # Model filtering (search in model-related fields)
    if model:
        # Search for model name in the run data (works across different LLM providers)
        fql_filters.append(f'search("{model}")')

    # Smart filters (deprecated - use flexible filters below)
    if slow:
        fql_filters.append('gt(latency, "5s")')

    if recent:
        # Last hour
        one_hour_ago = datetime.datetime.now(
            datetime.timezone.utc
        ) - datetime.timedelta(hours=1)
        fql_filters.append(f'gt(start_time, "{one_hour_ago.isoformat()}")')

    if today:
        # Today's runs (midnight to now)
        today_start = datetime.datetime.now(datetime.timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        fql_filters.append(f'gt(start_time, "{today_start.isoformat()}")')

    # Flexible latency filters
    if min_latency:
        duration = parse_duration_to_seconds(min_latency)
        fql_filters.append(f'gt(latency, "{duration}")')

    if max_latency:
        duration = parse_duration_to_seconds(max_latency)
        fql_filters.append(f'lt(latency, "{duration}")')

    # Flexible time filters (supports ISO, relative shorthand, and natural language)
    time_filters = build_time_fql_filters(since=since, last=last, before=before)
    fql_filters.extend(time_filters)

    # Combine all filters with AND logic
    combined_filter = combine_fql_filters(fql_filters)

    # Determine if client-side filtering is needed
    # (for run name pattern/regex matching, exclude patterns, or grep content search)
    needs_client_filtering = bool(name_regex or name_pattern or exclude or grep)

    # Determine fetch limit (how many runs to fetch from API)
    if fetch is not None:
        # User explicitly specified --fetch, use that value
        api_limit = fetch
    elif needs_client_filtering:
        # Automatic 3x multiplier for client-side filtering
        # Fetch 3x the limit or at least 100 runs to find pattern matches
        # Cap at 500 to avoid API timeouts (10x multiplier caused 0 results for limit=20+)
        # If no limit specified, cap at 1000 to avoid downloading everything
        if limit:
            api_limit = min(max(limit * 3, 100), 500)
        else:
            api_limit = 1000
    else:
        # No client-side filtering, fetch exactly what was requested
        # Convert 0 to None for SDK (0 means "no limit" in CLI, but SDK expects None)
        api_limit = None if limit == 0 else limit

    # Inform user about fetch strategy for client-side filtering
    if needs_client_filtering and api_limit != limit:
        active_filters = []
        if name_pattern:
            active_filters.append(f"--name-pattern '{name_pattern}'")
        if name_regex:
            active_filters.append(f"--name-regex '{name_regex}'")
        if exclude:
            active_filters.append(f"--exclude '{exclude}'")
        if grep:
            active_filters.append(f"--grep '{grep}'")
        filters_str = ", ".join(active_filters)

        if fetch is not None:
            # User explicitly set --fetch
            logger.info(
                f"Fetching {api_limit} runs (--fetch {fetch}) to evaluate client-side filters ({filters_str})"
            )
        else:
            # Automatic 3x multiplier
            logger.info(
                f"Fetching {api_limit} runs to evaluate client-side filters ({filters_str})"
            )
        logger.info(
            f"Will return up to {limit or 'all'} matching results. "
            f"Use --fetch to control how many runs to evaluate."
        )

    # Fetch runs from all matching projects using universal helper
    result = fetch_from_projects(
        client,
        projects_to_query,
        _make_fetch_runs(),
        project_query=pq,
        limit=api_limit,
        query=query,
        error=error_filter,
        filter=combined_filter,
        trace_id=trace_id,
        run_type=run_type,
        is_root=is_root,
        trace_filter=trace_filter,
        tree_filter=tree_filter,
        reference_example_id=reference_example_id,
        console=None,  # Don't auto-report warnings (we have custom diagnostics below)
    )
    all_runs = result.items
    failed_projects = result.failed_sources

    # CRITICAL: Fail fast if ALL sources failed (prevents silent failures)
    # The global error handler in LangSmithCLIGroup outputs JSON errors in --json mode,
    # so we don't need to output [] here (which would cause double output on stdout).
    # Uses raise_if_all_failed_with_suggestions to suggest similar project names.
    raise_if_all_failed_with_suggestions(result, client, pq, logger, "runs")

    # Report partial failures (some succeeded, some failed)
    if result.has_failures:
        result.report_failures_to_logger(logger)

    # Apply universal filtering to run names (client-side filtering)
    # FQL doesn't support full regex or complex patterns for run names
    runs = get_matching_items(
        all_runs,
        name_pattern=name_pattern,
        name_regex=name_regex,
        name_getter=lambda r: r.name or "",
    )

    # Client-side exclude filtering
    runs = apply_exclude_filter(runs, exclude, lambda r: r.name or "")

    # Client-side grep/content filtering
    if grep:
        # Parse grep-in fields if specified
        grep_fields_tuple = ()
        if grep_in:
            grep_fields_tuple = tuple(
                f.strip() for f in grep_in.split(",") if f.strip()
            )

        runs = apply_grep_filter(
            runs,
            grep_pattern=grep,
            grep_fields=grep_fields_tuple,
            ignore_case=grep_ignore_case,
            use_regex=grep_regex,
        )

    # Client-side sorting for table output
    if sort_by and not ctx.obj.get("json"):
        # Map sort field to run attribute
        sort_key_map = {
            "name": lambda r: (r.name or "").lower(),
            "status": lambda r: r.status or "",
            "latency": lambda r: r.latency if r.latency is not None else 0,
            "start_time": lambda r: (
                r.start_time if hasattr(r, "start_time") else datetime.datetime.min
            ),
        }
        runs = sort_items(runs, sort_by, sort_key_map, console)

    # Track total count before applying limit (for showing "more may exist" message)
    total_count = len(runs)

    # Apply user's limit AFTER all client-side filtering/sorting
    runs = apply_client_side_limit(runs, limit, needs_client_filtering)

    # Track if we hit the limit
    hit_limit = limit is not None and limit > 0 and total_count > limit

    # Report filtering results if client-side filtering was used
    if needs_client_filtering and not ctx.obj.get("json"):
        matches_found = len(runs)

        if limit and matches_found < limit:
            # Under-fetched: didn't find enough matches
            logger.warning(
                f"Found {matches_found}/{limit} requested matches "
                f"after evaluating {api_limit} runs."
            )
            logger.warning(
                f"Tip: Increase --limit to fetch more runs and find more matches "
                f"(current fetch limit: {api_limit})."
            )
        elif matches_found > 0:
            # Success: found enough matches
            logger.info(
                f"Found {matches_found} matches after evaluating {len(all_runs)} runs."
            )

    # Handle count mode - short circuit all other output
    if count:
        click.echo(str(len(runs)))
        return

    # Handle file output - short circuit if writing to file
    if output:
        data = filter_fields(runs, fields)
        write_output_to_file(data, output, console, format_type="jsonl")
        return

    # Determine output format
    format_type = determine_output_format(output_format, ctx.obj.get("json"))

    # Handle non-table formats
    if format_type != "table":
        # Use filter_fields for field filtering (runs is always a list)
        data = filter_fields(runs, fields)
        output_formatted_data(data, format_type)
        return

    # Build descriptive table title
    if len(projects_to_query) == 1:
        table_title = f"Runs ({projects_to_query[0]})"
    else:
        table_title = f"Runs ({len(projects_to_query)} projects)"

    # Use shared table builder utility
    table = build_runs_table(runs, table_title, no_truncate)

    if len(runs) == 0:
        # Provide helpful diagnostic message
        logger.warning("No runs found matching your criteria.")

        # Build list of active filters
        active_filters = []
        if len(projects_to_query) == 1:
            active_filters.append(f"project: {projects_to_query[0]}")
        elif len(projects_to_query) > 1:
            active_filters.append(f"projects: {len(projects_to_query)} matched")
        if query:
            active_filters.append(f'--query "{query}"')
        if grep:
            active_filters.append(f'--grep "{grep}"')
        if status:
            active_filters.append(f"--status {status}")
        if failed:
            active_filters.append("--failed")
        if succeeded:
            active_filters.append("--succeeded")
        if roots or is_root:
            active_filters.append("--roots")
        if run_type:
            active_filters.append(f"--run-type {run_type}")
        if name_pattern:
            active_filters.append(f'--name-pattern "{name_pattern}"')
        if name_regex:
            active_filters.append(f'--name-regex "{name_regex}"')
        if filter_:
            active_filters.append("--filter (custom FQL)")
        if limit and limit < 100:
            active_filters.append(f"--limit {limit}")

        if active_filters:
            logger.info("Active filters:")
            for f in active_filters:
                logger.info(f"  • {f}")

        # Show failed projects if any
        if failed_projects:
            logger.warning("Some projects failed to fetch:")
            for proj, error_msg in failed_projects[:3]:  # Show first 3 errors
                # Truncate long error messages
                short_error = (
                    error_msg[:100] + "..." if len(error_msg) > 100 else error_msg
                )
                logger.warning(f"  • {proj}: {short_error}")
            if len(failed_projects) > 3:
                logger.warning(f"  • ... and {len(failed_projects) - 3} more")

        # Provide suggestions
        logger.info("Try:")
        if roots or is_root:
            logger.info("  • Remove --roots flag to see all runs (including nested)")
        if limit and limit < 100:
            logger.info(f"  • Increase --limit (current: {limit})")
        if grep or query or filter_:
            logger.info("  • Broaden search criteria or remove filters")
        if len(projects_to_query) > 0:
            logger.info(
                f"  • Verify project exists: langsmith-cli projects list --name-pattern {projects_to_query[0]}"
            )
        logger.info("  • Check project has runs: langsmith-cli runs list --limit 1")
    else:
        console.print(table)

        # Show message if we hit the limit (not in count mode or JSON mode)
        if hit_limit and not count and not ctx.obj.get("json"):
            # Show the exact number we know
            logger.info(
                f"Showing {len(runs)} of {total_count} runs. "
                f"Use --limit 0 to see all {total_count} runs."
            )
