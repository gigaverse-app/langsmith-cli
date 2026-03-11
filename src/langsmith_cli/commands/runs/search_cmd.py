"""Search and sample commands for runs."""

import click

from langsmith_cli.commands.runs._group import (
    _make_fetch_runs,
    console,
    runs,
)
from langsmith_cli.utils import (
    add_project_filter_options,
    add_time_filter_options,
    build_time_fql_filters,
    combine_fql_filters,
    fetch_from_projects,
    fields_option,
    filter_fields,
    get_or_create_client,
    json_dumps,
    resolve_project_filters,
)


@runs.command("search")
@click.argument("query")
@add_project_filter_options
@add_time_filter_options
@click.option("--limit", default=10, help="Max results.")
@click.option(
    "--roots",
    is_flag=True,
    help="Show only root traces (cleaner output).",
)
@click.option(
    "--in",
    "search_in",
    type=click.Choice(["all", "inputs", "outputs", "error"]),
    default="all",
    help="Where to search (default: all fields).",
)
@click.option(
    "--input-contains", help="Filter by content in inputs (JSON path or text)."
)
@click.option(
    "--output-contains", help="Filter by content in outputs (JSON path or text)."
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["table", "json", "csv", "yaml"]),
    help="Output format.",
)
@click.pass_context
def search_runs(
    ctx,
    query,
    project,
    project_id,
    project_name,
    project_name_exact,
    project_name_pattern,
    project_name_regex,
    since,
    before,
    last,
    limit,
    roots,
    search_in,
    input_contains,
    output_contains,
    output_format,
):
    """Search runs using full-text search across one or more projects.

    QUERY is the text to search for across runs.

    Use project filters to search across multiple projects.

    Examples:
      langsmith-cli runs search "authentication failed"
      langsmith-cli runs search "timeout" --in error
      langsmith-cli runs search "user_123" --in inputs
      langsmith-cli runs search "error" --project-name-pattern "prod-*"
    """
    from langsmith_cli.commands.runs import list_runs

    # Build FQL filter for full-text search
    filter_expr = f'search("{query}")'

    # Add field-specific filters if provided
    filters = [filter_expr]

    if input_contains:
        filters.append(f'search("{input_contains}")')

    if output_contains:
        filters.append(f'search("{output_contains}")')

    # Combine filters with AND (filters always has at least one element from query)
    combined_filter = combine_fql_filters(filters) or filters[0]

    # Invoke list_runs with the filter and project filters
    return ctx.invoke(
        list_runs,
        project=project,
        project_id=project_id,
        project_name=project_name,
        project_name_exact=project_name_exact,
        project_name_pattern=project_name_pattern,
        project_name_regex=project_name_regex,
        limit=limit,
        filter_=combined_filter,
        output_format=output_format,
        # Pass through other required args with defaults
        status=None,
        trace_id=None,
        run_type=None,
        is_root=None,
        roots=roots,  # Pass through --roots flag
        trace_filter=None,
        tree_filter=None,
        reference_example_id=None,
        tag=(),
        name_pattern=None,
        name_regex=None,
        model=None,
        failed=False,
        succeeded=False,
        slow=False,
        recent=False,
        today=False,
        min_latency=None,
        max_latency=None,
        since=since,  # Pass through time filters
        before=before,  # Pass through time filters
        last=last,  # Pass through time filters
        sort_by=None,
        fields=None,  # Pass through fields parameter
    )


@runs.command("sample")
@add_project_filter_options
@add_time_filter_options
@click.option(
    "--stratify-by",
    required=True,
    help="Grouping field(s). Single: 'tag:length', Multi: 'tag:length,tag:type'",
)
@click.option(
    "--values",
    help="Comma-separated stratum values (single dimension) or colon-separated combinations (multi-dimensional). Examples: 'short,medium,long' or 'short:news,medium:news,long:gaming'",
)
@click.option(
    "--dimension-values",
    help="Pipe-separated values per dimension for Cartesian product (multi-dimensional only). Example: 'short|medium|long,news|gaming' generates all 6 combinations",
)
@click.option(
    "--samples-per-stratum",
    default=10,
    help="Number of samples per stratum (default: 10)",
)
@click.option(
    "--samples-per-combination",
    type=int,
    help="Samples per combination (multi-dimensional). Overrides --samples-per-stratum if set",
)
@click.option(
    "--output",
    help="Output file path (JSONL format). If not specified, writes to stdout.",
)
@click.option(
    "--filter",
    "additional_filter",
    help="Additional FQL filter to apply before sampling",
)
@fields_option()
@click.pass_context
def sample_runs(
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
    stratify_by,
    values,
    dimension_values,
    samples_per_stratum,
    samples_per_combination,
    output,
    additional_filter,
    fields,
):
    """Sample runs using stratified sampling by tags or metadata.

    This command collects balanced samples from different groups (strata) to ensure
    representative coverage across categories.

    Supports both single-dimensional and multi-dimensional stratification.

    Examples:
        # Single dimension: Sample by tag-based length categories
        langsmith-cli runs sample \\
          --project my-project \\
          --stratify-by "tag:length_category" \\
          --values "short,medium,long" \\
          --samples-per-stratum 20 \\
          --output stratified_sample.jsonl

        # Multi-dimensional: Sample by length and content type (Cartesian product)
        langsmith-cli runs sample \\
          --project my-project \\
          --stratify-by "tag:length,tag:content_type" \\
          --dimension-values "short|medium|long,news|gaming" \\
          --samples-per-combination 5

        # Multi-dimensional: Manual combinations
        langsmith-cli runs sample \\
          --project my-project \\
          --stratify-by "tag:length,tag:content_type" \\
          --values "short:news,medium:gaming,long:news" \\
          --samples-per-stratum 10

        # With time filtering: Sample only recent runs
        langsmith-cli runs sample \\
          --project my-project \\
          --stratify-by "tag:length_category" \\
          --values "short,medium,long" \\
          --since "3d" \\
          --samples-per-stratum 100
    """
    from langsmith_cli.commands.runs import (
        build_grouping_fql_filter,
        build_multi_dimensional_fql_filter,
        parse_grouping_field,
    )

    logger = ctx.obj["logger"]

    # Determine if output is machine-readable
    is_machine_readable = output is not None or fields
    logger.use_stderr = is_machine_readable

    import itertools

    logger.debug(f"Sampling runs with stratify_by={stratify_by}, values={values}")

    # Build time filters and combine with additional_filter
    time_filters = build_time_fql_filters(since=since, last=last, before=before)
    base_filters = time_filters.copy()
    if additional_filter:
        base_filters.append(additional_filter)

    # Combine base filters into a single filter
    base_filter = combine_fql_filters(base_filters)

    client = get_or_create_client(ctx)

    # Parse stratify-by field (can be single or multi-dimensional)
    parsed = parse_grouping_field(stratify_by)
    is_multi_dimensional = isinstance(parsed, list)

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

    all_samples = []

    if is_multi_dimensional:
        # Multi-dimensional stratification
        dimensions = parsed

        # Determine sample limit
        sample_limit = (
            samples_per_combination if samples_per_combination else samples_per_stratum
        )

        # Generate combinations
        if dimension_values:
            # Cartesian product: parse pipe-separated values per dimension
            dimension_value_lists = [
                [v.strip() for v in dim_vals.split("|")]
                for dim_vals in dimension_values.split(",")
            ]
            if len(dimension_value_lists) != len(dimensions):
                raise click.BadParameter(
                    f"Number of dimension value groups ({len(dimension_value_lists)}) "
                    f"must match number of dimensions ({len(dimensions)})"
                )
            combinations = list(itertools.product(*dimension_value_lists))
        elif values:
            # Manual combinations: parse colon-separated values
            combinations = [
                tuple(v.strip() for v in combo.split(":"))
                for combo in values.split(",")
            ]
            # Validate each combination has correct number of dimensions
            for combo in combinations:
                if len(combo) != len(dimensions):
                    raise click.BadParameter(
                        f"Combination {combo} has {len(combo)} values but expected {len(dimensions)}"
                    )
        else:
            raise click.BadParameter(
                "Multi-dimensional stratification requires --values or --dimension-values"
            )

        # Fetch samples for each combination
        for combination_values in combinations:
            # Build FQL filter for this combination
            stratum_filter = build_multi_dimensional_fql_filter(
                dimensions, list(combination_values)
            )

            # Combine stratum filter with base filter (time + additional filters)
            filters_to_combine = [stratum_filter]
            if base_filter:
                filters_to_combine.append(base_filter)
            combined_filter = combine_fql_filters(filters_to_combine)

            # Fetch samples from all matching projects using universal helper
            result = fetch_from_projects(
                client,
                pq.names,
                _make_fetch_runs(),
                project_query=pq,
                limit=sample_limit,
                filter=combined_filter,
                console=console,
            )
            stratum_runs = result.items[:sample_limit]

            # Add stratum field and convert to dicts
            for run in stratum_runs:
                run_dict = filter_fields(run, fields)
                # Build stratum label with all dimensions
                stratum_label = ",".join(
                    f"{field_name}:{value}"
                    for (_, field_name), value in zip(dimensions, combination_values)
                )
                run_dict["stratum"] = stratum_label
                all_samples.append(run_dict)

    else:
        # Single-dimensional stratification (backward compatible)
        grouping_type, field_name = parsed

        if not values:
            raise click.BadParameter(
                "Single-dimensional stratification requires --values"
            )

        # Parse values
        stratum_values = [v.strip() for v in values.split(",")]

        # Collect samples for each stratum
        for stratum_value in stratum_values:
            # Build FQL filter for this stratum
            stratum_filter = build_grouping_fql_filter(
                grouping_type, field_name, stratum_value
            )

            # Combine stratum filter with base filter (time + additional filters)
            filters_to_combine = [stratum_filter]
            if base_filter:
                filters_to_combine.append(base_filter)
            combined_filter = combine_fql_filters(filters_to_combine)

            # Fetch samples from all matching projects using universal helper
            result = fetch_from_projects(
                client,
                pq.names,
                _make_fetch_runs(),
                project_query=pq,
                limit=samples_per_stratum,
                filter=combined_filter,
                console=console,
            )
            stratum_runs = result.items[:samples_per_stratum]

            # Add stratum field and convert to dicts
            for run in stratum_runs:
                run_dict = filter_fields(run, fields)
                run_dict["stratum"] = f"{field_name}:{stratum_value}"
                all_samples.append(run_dict)

    # Output as JSONL
    if output:
        # Write to file
        try:
            with open(output, "w", encoding="utf-8") as f:
                for sample in all_samples:
                    f.write(json_dumps(sample) + "\n")
            logger.success(f"Wrote {len(all_samples)} samples to {output}")
        except Exception as e:
            logger.error(f"Error writing to file {output}: {e}")
            raise click.Abort()
    else:
        # Write to stdout (JSONL format)
        for sample in all_samples:
            click.echo(json_dumps(sample))
