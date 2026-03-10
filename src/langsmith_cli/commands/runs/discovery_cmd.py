"""Discovery commands: tags, metadata-keys, fields, describe."""

from dataclasses import dataclass
from typing import Any

import click
from langsmith.schemas import Run

from langsmith_cli.commands.runs._group import _make_fetch_runs, console, runs
from langsmith_cli.utils import (
    add_project_filter_options,
    add_time_filter_options,
    build_time_fql_filters,
    combine_fql_filters,
    fetch_from_projects,
    get_or_create_client,
    json_dumps,
    resolve_project_filters,
)


@dataclass
class DiscoveryContext:
    """Context returned by _fetch_runs_for_discovery."""

    runs: list[Run]
    projects: list[str]
    logger: Any  # CLILogger


def _fetch_runs_for_discovery(
    ctx,
    project: str | None,
    project_name: str | None,
    project_name_exact: str | None,
    project_name_pattern: str | None,
    project_name_regex: str | None,
    since: str | None,
    before: str | None,
    last: str | None,
    sample_size: int,
    select: list[str] | None = None,
    cmd_name: str = "discovery",
    project_id: str | None = None,
) -> DiscoveryContext:
    """Shared setup for discovery commands (tags, metadata-keys, fields, describe).

    Handles the common pattern of:
    - Setting up logger with stderr mode
    - Building and combining time filters
    - Getting matching projects
    - Fetching runs with optional field selection

    Args:
        ctx: Click context
        project: Project ID or name
        project_name: Substring filter for project name
        project_name_exact: Exact project name filter
        project_name_pattern: Glob pattern for project name
        project_name_regex: Regex pattern for project name
        since: Time filter (since)
        last: Time filter (last duration)
        sample_size: Number of runs to sample
        select: Optional list of fields to fetch (for performance)
        cmd_name: Command name for debug logging

    Returns:
        DiscoveryContext with runs, projects list, and logger
    """
    logger = ctx.obj["logger"]

    # Determine if output is machine-readable (use stderr for diagnostics)
    is_machine_readable = ctx.obj.get("json")
    logger.use_stderr = is_machine_readable

    client = get_or_create_client(ctx)
    logger.debug(f"Running {cmd_name} with sample_size={sample_size}")

    # Build and combine time filters
    time_filters = build_time_fql_filters(since=since, last=last, before=before)
    combined_filter = combine_fql_filters(time_filters)

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

    # Fetch runs
    logger.debug(f"Fetching {sample_size} runs for {cmd_name}...")

    result = fetch_from_projects(
        client,
        pq.names,
        _make_fetch_runs(),
        project_query=pq,
        limit=sample_size,
        select=select,
        filter=combined_filter,
        console=console,
    )

    return DiscoveryContext(
        runs=result.items,
        projects=pq.names if not pq.use_id else [f"id:{pq.project_id}"],
        logger=logger,
    )


@runs.command("tags")
@add_project_filter_options
@add_time_filter_options
@click.option(
    "--sample-size",
    default=1000,
    type=int,
    help="Number of recent runs to sample for discovery (default: 1000)",
)
@click.pass_context
def discover_tags(
    ctx,
    project,
    project_id,
    project_name,
    project_name_exact,
    project_name_pattern,
    project_name_regex,
    since,
    before,
    last,
    sample_size,
):
    """Discover tag patterns in a project.

    Analyzes recent runs to extract structured tag patterns (key:value format).
    Useful for understanding available stratification dimensions.

    Examples:
        # Discover tags in default project
        langsmith-cli runs tags

        # Discover tags in specific project with larger sample
        langsmith-cli --json runs tags --project my-project --sample-size 5000

        # Discover tags with pattern filtering
        langsmith-cli runs tags --project-name-pattern "prod/*"
    """
    from collections import defaultdict

    # Fetch runs using shared discovery helper
    discovery = _fetch_runs_for_discovery(
        ctx=ctx,
        project=project,
        project_id=project_id,
        project_name=project_name,
        project_name_exact=project_name_exact,
        project_name_pattern=project_name_pattern,
        project_name_regex=project_name_regex,
        since=since,
        before=before,
        last=last,
        sample_size=sample_size,
        select=["tags"],
        cmd_name="tags",
    )

    # Parse tags to extract key:value patterns
    tag_patterns: dict[str, set[str]] = defaultdict(set)

    for run in discovery.runs:
        if run.tags:
            for tag in run.tags:
                if ":" in tag:
                    key, value = tag.split(":", 1)
                    tag_patterns[key].add(value)

    # Convert sets to sorted lists
    result = {
        "tag_patterns": {
            key: sorted(values) for key, values in sorted(tag_patterns.items())
        }
    }

    # Output
    if ctx.obj.get("json"):
        click.echo(json_dumps(result))
    else:
        from rich.table import Table

        table = Table(title="Tag Patterns")
        table.add_column("Tag Key", style="cyan")
        table.add_column("Values", style="green")

        for key, values in result["tag_patterns"].items():
            value_str = ", ".join(values[:10])
            if len(values) > 10:
                value_str += f" ... (+{len(values) - 10} more)"
            table.add_row(key, value_str)

        if not result["tag_patterns"]:
            discovery.logger.warning("No structured tags found (key:value format).")
        else:
            console.print(table)
            discovery.logger.info(
                f"Analyzed {len(discovery.runs)} runs from {len(discovery.projects)} project(s)"
            )


@runs.command("metadata-keys")
@add_project_filter_options
@add_time_filter_options
@click.option(
    "--sample-size",
    default=1000,
    type=int,
    help="Number of recent runs to sample for discovery (default: 1000)",
)
@click.pass_context
def discover_metadata_keys(
    ctx,
    project,
    project_id,
    project_name,
    project_name_exact,
    project_name_pattern,
    project_name_regex,
    since,
    before,
    last,
    sample_size,
):
    """Discover metadata keys used in a project.

    Analyzes recent runs to extract all metadata keys.
    Useful for understanding available metadata-based stratification dimensions.

    Examples:
        # Discover metadata keys in default project
        langsmith-cli runs metadata-keys

        # Discover in specific project
        langsmith-cli --json runs metadata-keys --project my-project

        # Discover with pattern filtering
        langsmith-cli runs metadata-keys --project-name-pattern "prod/*"
    """
    # Fetch runs using shared discovery helper
    discovery = _fetch_runs_for_discovery(
        ctx=ctx,
        project=project,
        project_id=project_id,
        project_name=project_name,
        project_name_exact=project_name_exact,
        project_name_pattern=project_name_pattern,
        project_name_regex=project_name_regex,
        since=since,
        before=before,
        last=last,
        sample_size=sample_size,
        select=["extra"],  # Metadata is stored in extra field
        cmd_name="metadata-keys",
    )

    # Extract all metadata keys
    metadata_keys: set[str] = set()

    for run in discovery.runs:
        # Check run.metadata
        if run.metadata and isinstance(run.metadata, dict):
            metadata_keys.update(run.metadata.keys())

        # Check run.extra["metadata"]
        if run.extra and isinstance(run.extra, dict):
            extra_metadata = run.extra.get("metadata")
            if extra_metadata and isinstance(extra_metadata, dict):
                metadata_keys.update(extra_metadata.keys())

    result = {"metadata_keys": sorted(metadata_keys)}

    # Output
    if ctx.obj.get("json"):
        click.echo(json_dumps(result))
    else:
        from rich.table import Table

        table = Table(title="Metadata Keys")
        table.add_column("Key", style="cyan")
        table.add_column("Type", style="dim")

        for key in result["metadata_keys"]:
            table.add_row(key, "metadata")

        if not result["metadata_keys"]:
            discovery.logger.warning("No metadata keys found.")
        else:
            console.print(table)
            discovery.logger.info(
                f"Analyzed {len(discovery.runs)} runs from {len(discovery.projects)} project(s)"
            )


def _field_analysis_common(
    ctx,
    project: str | None,
    project_name: str | None,
    project_name_exact: str | None,
    project_name_pattern: str | None,
    project_name_regex: str | None,
    since: str | None,
    before: str | None,
    last: str | None,
    sample_size: int,
    include: str | None,
    exclude: str | None,
    no_language: bool,
    show_detailed_stats: bool,
    project_id: str | None = None,
) -> None:
    """Shared logic for runs fields and runs describe commands.

    Args:
        ctx: Click context
        project: Project ID or name
        project_name: Substring filter for project name
        project_name_exact: Exact project name filter
        project_name_pattern: Glob pattern for project name
        project_name_regex: Regex pattern for project name
        since: Time filter (since)
        last: Time filter (last duration)
        sample_size: Number of runs to sample
        include: Comma-separated paths to include
        exclude: Comma-separated paths to exclude
        no_language: Skip language detection
        show_detailed_stats: If True, show length/numeric stats (describe mode).
                            If False, show sample values (fields mode).
        project_id: Optional project UUID (bypasses name resolution)
    """
    from langsmith_cli.field_analysis import (
        FieldStats,
        analyze_runs_fields,
        filter_fields_by_path,
        format_languages_display,
        format_length_stats,
        format_numeric_stats,
    )

    cmd_name = "describe" if show_detailed_stats else "fields"

    # Fetch runs using shared discovery helper (no select - need full run data)
    discovery = _fetch_runs_for_discovery(
        ctx=ctx,
        project=project,
        project_id=project_id,
        project_name=project_name,
        project_name_exact=project_name_exact,
        project_name_pattern=project_name_pattern,
        project_name_regex=project_name_regex,
        since=since,
        before=before,
        last=last,
        sample_size=sample_size,
        select=None,  # Need full run data for field analysis
        cmd_name=cmd_name,
    )

    if not discovery.runs:
        if ctx.obj.get("json"):
            click.echo(json_dumps({"fields": [], "total_runs": 0}))
        else:
            discovery.logger.warning("No runs found.")
        return

    # Convert runs to dicts for analysis
    discovery.logger.debug(f"Analyzing fields across {len(discovery.runs)} runs...")
    runs_data = [run.model_dump(mode="json") for run in discovery.runs]

    # Analyze fields
    stats_list = analyze_runs_fields(runs_data, detect_languages=not no_language)

    # Apply path filters
    include_paths = [p.strip() for p in include.split(",")] if include else None
    exclude_paths = [p.strip() for p in exclude.split(",")] if exclude else None
    stats_list = filter_fields_by_path(stats_list, include_paths, exclude_paths)

    # Output JSON (same format for both commands)
    if ctx.obj.get("json"):
        output = {
            "fields": [s.to_dict() for s in stats_list],
            "total_runs": len(discovery.runs),
            "meta": {
                "lang_detect_enabled": not no_language,
                "lang_detect_sample_size": 500,
                "lang_detect_min_length": 30,
            },
        }
        click.echo(json_dumps(output))
        return

    # Table output - differs based on mode
    from rich.table import Table

    def render_fields_row(stats: FieldStats) -> tuple[str, str, str, str, str]:
        """Render row for 'fields' command: Path, Type, Present, Languages, Sample."""
        return (
            stats.path,
            stats.field_type,
            f"{stats.present_pct}%",
            format_languages_display(stats.languages),
            stats.sample or "-",
        )

    def render_describe_row(stats: FieldStats) -> tuple[str, str, str, str, str]:
        """Render row for 'describe' command: Path, Type, Present, Stats, Languages."""
        if stats.field_type in ("int", "float"):
            stats_str = format_numeric_stats(stats)
        else:
            stats_str = format_length_stats(stats)
        return (
            stats.path,
            stats.field_type,
            f"{stats.present_pct}%",
            stats_str,
            format_languages_display(stats.languages),
        )

    if show_detailed_stats:
        table = Table(title=f"Field Statistics ({len(discovery.runs)} runs analyzed)")
        table.add_column("Field Path", style="cyan")
        table.add_column("Type", style="dim")
        table.add_column("Present", justify="right")
        table.add_column("Length/Numeric Stats")
        table.add_column("Languages")
        for stats in stats_list:
            table.add_row(*render_describe_row(stats))
    else:
        table = Table(title=f"Fields ({len(discovery.runs)} runs analyzed)")
        table.add_column("Field Path", style="cyan")
        table.add_column("Type", style="dim")
        table.add_column("Present", justify="right")
        table.add_column("Languages")
        table.add_column("Sample", max_width=40, overflow="fold")
        for stats in stats_list:
            table.add_row(*render_fields_row(stats))

    console.print(table)
    discovery.logger.info(
        f"Analyzed {len(discovery.runs)} runs from {len(discovery.projects)} project(s)"
    )


# Decorator stack for field analysis commands (shared options)
def add_field_analysis_options(func):
    """Add common options for field analysis commands (fields, describe)."""
    func = click.option(
        "--no-language",
        is_flag=True,
        default=False,
        help="Skip language detection (faster)",
    )(func)
    func = click.option(
        "--exclude",
        type=str,
        help="Exclude fields starting with these paths (comma-separated, e.g., 'extra,events')",
    )(func)
    func = click.option(
        "--include",
        type=str,
        help="Only include fields starting with these paths (comma-separated, e.g., 'inputs,outputs')",
    )(func)
    func = click.option(
        "--sample-size",
        default=100,
        type=int,
        help="Number of recent runs to sample (default: 100)",
    )(func)
    return func


@runs.command("fields")
@add_project_filter_options
@add_time_filter_options
@add_field_analysis_options
@click.pass_context
def discover_fields(
    ctx,
    project,
    project_id,
    project_name,
    project_name_exact,
    project_name_pattern,
    project_name_regex,
    since,
    before,
    last,
    sample_size,
    include,
    exclude,
    no_language,
):
    """Discover fields and their types across runs.

    Analyzes recent runs to extract all field paths, types, presence rates,
    and language distribution for text fields.

    Examples:
        # Discover fields in default project
        langsmith-cli runs fields

        # Focus on inputs/outputs only
        langsmith-cli --json runs fields --include inputs,outputs

        # Skip language detection for speed
        langsmith-cli runs fields --no-language

        # Exclude verbose fields
        langsmith-cli runs fields --exclude extra,events,serialized
    """
    _field_analysis_common(
        ctx=ctx,
        project=project,
        project_id=project_id,
        project_name=project_name,
        project_name_exact=project_name_exact,
        project_name_pattern=project_name_pattern,
        project_name_regex=project_name_regex,
        since=since,
        before=before,
        last=last,
        sample_size=sample_size,
        include=include,
        exclude=exclude,
        no_language=no_language,
        show_detailed_stats=False,
    )


@runs.command("describe")
@add_project_filter_options
@add_time_filter_options
@add_field_analysis_options
@click.pass_context
def describe_fields(
    ctx,
    project,
    project_id,
    project_name,
    project_name_exact,
    project_name_pattern,
    project_name_regex,
    since,
    before,
    last,
    sample_size,
    include,
    exclude,
    no_language,
):
    """Detailed field statistics with length/numeric stats.

    Like 'runs fields' but includes detailed statistics:
    - String fields: min/max/avg/p50 length
    - Numeric fields: min/max/avg/p50/sum
    - List fields: min/max/avg/p50 element count

    Examples:
        # Full statistics for all fields
        langsmith-cli runs describe

        # Focus on inputs/outputs with language detection
        langsmith-cli --json runs describe --include inputs,outputs

        # Quick analysis without language detection
        langsmith-cli runs describe --no-language --sample-size 50
    """
    _field_analysis_common(
        ctx=ctx,
        project=project,
        project_id=project_id,
        project_name=project_name,
        project_name_exact=project_name_exact,
        project_name_pattern=project_name_pattern,
        project_name_regex=project_name_regex,
        since=since,
        before=before,
        last=last,
        sample_size=sample_size,
        include=include,
        exclude=exclude,
        no_language=no_language,
        show_detailed_stats=True,
    )
