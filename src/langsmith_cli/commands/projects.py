import click
from rich.console import Console
from rich.table import Table
from langsmith_cli.utils import (
    sort_items,
    apply_regex_filter,
    apply_wildcard_filter,
    apply_client_side_limit,
    extract_wildcard_search_term,
    extract_regex_search_term,
    fields_option,
    render_output,
    get_or_create_client,
)

console = Console()


@click.group()
def projects():
    """Manage LangSmith projects."""
    pass


@projects.command("list")
@click.option("--limit", default=100, help="Limit number of projects (default 100).")
@click.option("--name", "name_", help="Filter by project name substring.")
@click.option("--name-pattern", help="Filter by name with wildcards (e.g. '*prod*').")
@click.option(
    "--name-regex", help="Filter by name with regex (e.g. '^prod-.*-v[0-9]+$')."
)
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
@click.pass_context
def list_projects(
    ctx,
    limit,
    name_,
    name_pattern,
    name_regex,
    reference_dataset_id,
    reference_dataset_name,
    has_runs,
    sort_by,
    output_format,
    fields,
):
    """List all projects."""

    client = get_or_create_client(ctx)

    # Determine if client-side filtering is needed
    needs_client_filtering = False
    api_name_filter = name_

    if name_pattern and not name_:
        # Extract search term and check if pattern is unanchored
        search_term, is_unanchored = extract_wildcard_search_term(name_pattern)
        if is_unanchored and search_term:
            # Unanchored pattern - can use API optimization
            api_name_filter = search_term
        else:
            # Anchored pattern - needs client-side filtering
            needs_client_filtering = True
    elif name_regex and not name_ and not name_pattern:
        # Regex always needs client-side filtering
        needs_client_filtering = True
        # Try to extract search term for API optimization
        search_term = extract_regex_search_term(name_regex)
        if search_term:
            api_name_filter = search_term

    # If has_runs filter is used, we need client-side filtering
    if has_runs:
        needs_client_filtering = True

    # Determine API limit: if client-side filtering needed, fetch more results
    # Otherwise use the user's limit directly
    api_limit = None if needs_client_filtering else limit

    # list_projects returns a generator
    projects_gen = client.list_projects(
        limit=api_limit,
        name_contains=api_name_filter,
        reference_dataset_id=reference_dataset_id,
        reference_dataset_name=reference_dataset_name,
    )

    # Materialize the list to count and process
    projects_list = list(projects_gen)

    # Client-side pattern matching (wildcards)
    projects_list = apply_wildcard_filter(projects_list, name_pattern, lambda p: p.name)

    # Client-side regex filtering
    projects_list = apply_regex_filter(projects_list, name_regex, lambda p: p.name)

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

    # Apply user's limit AFTER all client-side filtering/sorting
    projects_list = apply_client_side_limit(
        projects_list, limit, needs_client_filtering
    )

    # Define table builder function
    def build_projects_table(projects):
        table = Table(title="Projects")
        table.add_column("Name", style="cyan")
        table.add_column("ID", style="dim")
        for p in projects:
            table.add_row(p.name, str(p.id))
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
    )


@projects.command("create")
@click.argument("name")
@click.option("--description", help="Project description.")
@click.pass_context
def create_project(ctx, name, description):
    """Create a new project."""
    import json
    from langsmith.utils import LangSmithConflictError

    client = get_or_create_client(ctx)
    try:
        project = client.create_project(project_name=name, description=description)
        if ctx.obj.get("json"):
            # Use SDK's Pydantic model directly
            data = project.model_dump(mode="json")
            click.echo(json.dumps(data, default=str))
            return

        console.print(
            f"[green]Created project {project.name}[/green] (ID: {project.id})"
        )
    except LangSmithConflictError:
        # Project already exists - handle gracefully for idempotency
        console.print(f"[yellow]Project {name} already exists.[/yellow]")
