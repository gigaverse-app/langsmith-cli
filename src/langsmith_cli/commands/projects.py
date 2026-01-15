import click
from rich.console import Console
from rich.table import Table
from langsmith_cli.utils import (
    sort_items,
    apply_regex_filter,
    apply_wildcard_filter,
    apply_exclude_filter,
    apply_client_side_limit,
    extract_wildcard_search_term,
    extract_regex_search_term,
    fields_option,
    filter_fields,
    count_option,
    exclude_option,
    output_option,
    render_output,
    get_or_create_client,
    write_output_to_file,
    json_dumps,
)

console = Console()


@click.group()
def projects():
    """Manage LangSmith projects."""
    pass


@projects.command("list")
@click.option(
    "--limit",
    default=100,
    help="Limit number of projects (default 100, use 0 for no limit).",
)
@click.option("--name", "name_", help="Filter by project name substring.")
@click.option("--name-pattern", help="Filter by name with wildcards (e.g. '*prod*').")
@click.option(
    "--name-regex", help="Filter by name with regex (e.g. '^prod-.*-v[0-9]+$')."
)
@exclude_option()
@click.option(
    "--reference-dataset-id", help="Filter experiments for a dataset (by ID)."
)
@click.option(
    "--reference-dataset-name", help="Filter experiments for a dataset (by name)."
)
@click.option(
    "--has-runs", is_flag=True, help="Show only projects with runs (run_count > 0)."
)
@click.option(
    "--sort-by", help="Sort by field (name, run_count). Prefix with - for descending."
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["table", "json", "csv", "yaml"]),
    help="Output format (default: table, or json if --json flag used).",
)
@fields_option()
@count_option()
@output_option()
@click.pass_context
def list_projects(
    ctx,
    limit,
    name_,
    name_pattern,
    name_regex,
    exclude,
    reference_dataset_id,
    reference_dataset_name,
    has_runs,
    sort_by,
    output_format,
    fields,
    count,
    output,
):
    """List all projects."""
    logger = ctx.obj["logger"]
    is_machine_readable = ctx.obj.get("json") or bool(output) or bool(fields)
    logger.use_stderr = is_machine_readable

    # When --count is used, default to unlimited (0) unless user explicitly set limit
    # Check if limit was explicitly provided by checking if it's not the default
    if count and limit == 100:
        # User didn't explicitly set limit, so use 0 (unlimited) for counting
        limit = 0

    logger.debug(
        f"Listing projects: limit={limit}, name={name_}, "
        f"pattern={name_pattern}, regex={name_regex}"
    )

    client = get_or_create_client(ctx)

    # Determine API name filter for optimization
    api_name_filter = name_

    if name_pattern and not name_:
        # Extract search term and check if pattern is unanchored
        search_term, is_unanchored = extract_wildcard_search_term(name_pattern)
        if is_unanchored and search_term:
            # Unanchored pattern - can use API optimization
            api_name_filter = search_term
    elif name_regex and not name_ and not name_pattern:
        # Try to extract search term for API optimization
        search_term = extract_regex_search_term(name_regex)
        if search_term:
            api_name_filter = search_term

    # Always fetch all projects (no API limit) to ensure pagination works correctly
    # The SDK's iterator handles pagination automatically via offset
    # We'll apply the user's limit client-side after filtering/sorting
    api_limit = None

    # list_projects returns a generator
    # include_stats=True to get run_count, error_rate, total_cost, etc.
    projects_gen = client.list_projects(
        limit=api_limit,
        name_contains=api_name_filter,
        reference_dataset_id=reference_dataset_id,
        reference_dataset_name=reference_dataset_name,
        include_stats=True,
    )

    # Materialize the list to count and process
    projects_list = list(projects_gen)

    # Client-side pattern matching (wildcards)
    projects_list = apply_wildcard_filter(projects_list, name_pattern, lambda p: p.name)

    # Client-side regex filtering
    projects_list = apply_regex_filter(projects_list, name_regex, lambda p: p.name)

    # Client-side exclude filtering
    projects_list = apply_exclude_filter(projects_list, exclude, lambda p: p.name)

    # Filter by projects with runs
    if has_runs:
        projects_list = [
            p
            for p in projects_list
            if hasattr(p, "run_count") and p.run_count and p.run_count > 0
        ]

    # Client-side sorting for table output
    if sort_by and not ctx.obj.get("json"):
        # Map sort field to project attribute
        sort_key_map = {
            "name": lambda p: (p.name or "").lower(),
            "run_count": lambda p: p.run_count
            if hasattr(p, "run_count") and p.run_count
            else 0,
        }
        projects_list = sort_items(projects_list, sort_by, sort_key_map, console)

    # Track total count before applying limit (for showing "more may exist" message)
    total_count = len(projects_list)

    # Apply user's limit AFTER all client-side filtering/sorting
    # Always apply client-side limit since we fetch all projects from API
    # Special case: limit=0 means "no limit" (fetch all)
    effective_limit = None if limit == 0 else limit
    projects_list = apply_client_side_limit(projects_list, effective_limit, True)

    # Track if we hit the limit
    hit_limit = effective_limit is not None and total_count > effective_limit

    # Handle file output - short circuit if writing to file
    if output:
        data = filter_fields(projects_list, fields)
        write_output_to_file(data, output, console, format_type="jsonl")
        return

    # Define table builder function
    def build_projects_table(projects):
        from datetime import datetime, timezone

        table = Table(title="Projects")
        table.add_column("Name", style="cyan")
        table.add_column("Runs", justify="right")
        table.add_column("Last Run", style="dim")
        table.add_column("Error Rate", justify="right")
        table.add_column("Cost", justify="right", style="green")

        for p in projects:
            # Run count
            run_count_str = str(p.run_count) if p.run_count else "0"

            # Last run time (human-readable relative time)
            if p.last_run_start_time:
                now = datetime.now(timezone.utc)
                # Handle timezone-aware and timezone-naive datetimes
                last_run = p.last_run_start_time
                if last_run.tzinfo is None:
                    # Make naive datetime timezone-aware (assume UTC)
                    last_run = last_run.replace(tzinfo=timezone.utc)
                delta = now - last_run
                if delta.days > 0:
                    last_run_str = f"{delta.days}d ago"
                elif delta.seconds >= 3600:
                    hours = delta.seconds // 3600
                    last_run_str = f"{hours}h ago"
                elif delta.seconds >= 60:
                    minutes = delta.seconds // 60
                    last_run_str = f"{minutes}m ago"
                else:
                    last_run_str = "just now"
            else:
                last_run_str = "-"

            # Error rate (percentage)
            if p.error_rate is not None:
                error_rate_str = f"{p.error_rate * 100:.1f}%"
                if p.error_rate > 0.1:  # More than 10% errors
                    error_rate_str = f"[red]{error_rate_str}[/red]"
                elif p.error_rate > 0:
                    error_rate_str = f"[yellow]{error_rate_str}[/yellow]"
            else:
                error_rate_str = "-"

            # Total cost
            if p.total_cost is not None and p.total_cost > 0:
                cost_str = f"${p.total_cost:.4f}"
            else:
                cost_str = "-"

            table.add_row(p.name, run_count_str, last_run_str, error_rate_str, cost_str)

        return table

    # Determine which fields to include
    if fields:
        include_fields = {f.strip() for f in fields.split(",") if f.strip()}
    else:
        # Default fields for output
        include_fields = None

    # Unified output rendering
    render_output(
        projects_list,
        build_projects_table,
        ctx,
        include_fields=include_fields,
        empty_message="No projects found",
        output_format=output_format,
        count_flag=count,
    )

    # Show message if we hit the limit (not in count mode or JSON mode)
    if hit_limit and not count and not ctx.obj.get("json"):
        # Show the exact number we know
        logger.info(
            f"Showing {len(projects_list)} of {total_count} projects. "
            f"Use --limit 0 to see all {total_count} projects."
        )


@projects.command("create")
@click.argument("name")
@click.option("--description", help="Project description.")
@click.pass_context
def create_project(ctx, name, description):
    """Create a new project."""
    from langsmith.utils import LangSmithConflictError

    logger = ctx.obj["logger"]
    is_machine_readable = ctx.obj.get("json")
    logger.use_stderr = is_machine_readable

    logger.debug(f"Creating project: name={name}")

    client = get_or_create_client(ctx)
    try:
        project = client.create_project(project_name=name, description=description)
        if ctx.obj.get("json"):
            # Use SDK's Pydantic model directly
            data = project.model_dump(mode="json")
            click.echo(json_dumps(data))
            return

        logger.success(f"Created project {project.name} (ID: {project.id})")
    except LangSmithConflictError:
        # Project already exists - handle gracefully for idempotency
        logger.warning(f"Project {name} already exists.")
