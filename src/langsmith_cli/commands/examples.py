import click
from langsmith_cli.dataset_replica.models import ReplicaSource
from langsmith_cli.utils import (
    ConsoleProtocol,
    LazyConsole,
    apply_exclude_filter,
    configure_logger_streams,
    confirm_option,
    count_option,
    emit_action_result,
    exclude_option,
    fields_option,
    filter_fields,
    get_or_create_client,
    is_json_context,
    json_dumps,
    not_found_as_click_exception,
    output_option,
    output_single_item,
    parse_comma_separated_list,
    parse_fields_option,
    parse_json_string,
    render_detail_fields,
    render_output,
    require_confirmation,
    sort_by_option,
    sort_items,
)

console = LazyConsole()


def normalize_split(split: str | None) -> list[str] | None:
    """Normalize a split string to the list format expected by the SDK."""
    if not split:
        return None
    return [split] if isinstance(split, str) else split


@click.group()
def examples():
    """Manage dataset examples."""
    pass


@examples.command("list")
@click.option(
    "--source",
    type=click.Choice([item.value for item in ReplicaSource]),
    default=ReplicaSource.CLOUD.value,
    show_default=True,
)
@click.option("--archive-uri", envvar="LANGSMITH_ARCHIVE_URI")
@click.option("--local-dir", type=click.Path(file_okay=False))
@click.option("--dataset", help="Dataset ID or Name.")
@click.option("--example-ids", help="Specific example IDs (comma-separated).")
@click.option("--limit", default=20, help="Limit number of examples (default 20).")
@click.option("--offset", default=0, help="Number of examples to skip (pagination).")
@click.option("--filter", "filter_", help="LangSmith query filter.")
@click.option("--metadata", help="Filter by metadata (JSON string).")
@click.option("--splits", help="Filter by dataset splits (comma-separated).")
@click.option(
    "--inline-s3-urls/--no-inline-s3-urls",
    default=None,
    help="Include S3 URLs inline.",
)
@click.option(
    "--include-attachments/--no-include-attachments",
    default=None,
    help="Include attachments.",
)
@click.option("--as-of", help="Dataset version tag or ISO timestamp.")
@sort_by_option(fields="created_at, modified_at")
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["table", "json", "csv", "yaml"]),
    help="Output format (default: table, or json if --json flag used).",
)
@exclude_option()
@fields_option()
@count_option()
@output_option()
@click.pass_context
def list_examples(
    ctx,
    source,
    archive_uri,
    local_dir,
    dataset,
    example_ids,
    limit,
    offset,
    filter_,
    metadata,
    splits,
    inline_s3_urls,
    include_attachments,
    as_of,
    sort_by,
    output_format,
    exclude,
    fields,
    count,
    output,
):
    """List examples for a dataset."""
    source = ReplicaSource(source)
    logger = ctx.obj["logger"]
    configure_logger_streams(
        ctx, logger, output=output, output_format=output_format, fields=fields
    )

    logger.debug(
        f"Listing examples: dataset={dataset}, limit={limit}, "
        f"offset={offset}, filter={filter_}"
    )

    # Parse comma-separated values
    example_ids_list = parse_comma_separated_list(example_ids)
    splits_list = parse_comma_separated_list(splits)
    metadata_dict = parse_json_string(metadata, "metadata")

    if source is ReplicaSource.CLOUD:
        client = get_or_create_client(ctx)
        examples_list = list(
            client.list_examples(
                dataset_name=dataset,
                example_ids=example_ids_list,
                limit=limit,
                offset=offset,
                filter=filter_,
                metadata=metadata_dict,
                splits=splits_list,
                inline_s3_urls=inline_s3_urls,
                include_attachments=include_attachments,
                as_of=as_of,
            )
        )
    else:
        if filter_ is not None:
            raise click.ClickException(
                "--filter is currently available only for --source cloud"
            )
        repository = _replica_repository(source, archive_uri, local_dir)
        dataset_refs = (
            [dataset]
            if dataset is not None
            else [str(item.id) for item in repository.list_datasets()]
        )
        examples_list = []
        for dataset_ref in dataset_refs:
            examples_list.extend(
                repository.read_examples(
                    dataset_ref,
                    as_of=as_of,
                    include_attachments=bool(include_attachments),
                )
            )
        if example_ids_list is not None:
            selected_ids = set(example_ids_list)
            examples_list = [e for e in examples_list if str(e.id) in selected_ids]
        if metadata_dict is not None:
            examples_list = [
                e
                for e in examples_list
                if e.metadata is not None
                and all(
                    key in e.metadata and e.metadata[key] == value
                    for key, value in metadata_dict.items()
                )
            ]
        if splits_list is not None:
            selected_splits = set(splits_list)
            examples_list = [
                e
                for e in examples_list
                if e.metadata is not None
                and "dataset_split" in e.metadata
                and bool(selected_splits.intersection(e.metadata["dataset_split"]))
            ]
        examples_list = examples_list[offset : offset + limit]

    # Client-side exclude filtering (filter by ID string representation)
    examples_list = apply_exclude_filter(examples_list, exclude, lambda e: str(e.id))

    # Client-side sorting
    if sort_by:
        examples_list = sort_items(
            examples_list,
            sort_by,
            {
                "created_at": lambda e: e.created_at,
                "modified_at": lambda e: e.modified_at,
            },
        )

    # Define table builder function
    def build_examples_table(examples):
        from rich.table import Table

        table = Table(title=f"Examples: {dataset}")
        table.add_column("ID", style="dim")
        table.add_column("Inputs")
        table.add_column("Outputs")
        for e in examples:
            inputs_str = json_dumps(e.inputs)
            outputs_str = json_dumps(e.outputs)
            # Truncate for table
            if len(inputs_str) > 50:
                inputs_str = inputs_str[:47] + "..."
            if len(outputs_str) > 50:
                outputs_str = outputs_str[:47] + "..."
            table.add_row(str(e.id), inputs_str, outputs_str)
        return table

    include_fields = parse_fields_option(fields)

    # Unified output rendering (handles --json, --format, --output, --count uniformly)
    render_output(
        examples_list,
        build_examples_table,
        ctx,
        include_fields=include_fields,
        empty_message="No examples found",
        output_format=output_format,
        count_flag=count,
        output_path=output,
    )


@examples.command("get")
@click.argument("example_id")
@click.option("--as-of", help="Dataset version tag or ISO timestamp.")
@click.option(
    "--source",
    type=click.Choice([item.value for item in ReplicaSource]),
    default=ReplicaSource.CLOUD.value,
    show_default=True,
)
@click.option("--archive-uri", envvar="LANGSMITH_ARCHIVE_URI")
@click.option("--local-dir", type=click.Path(file_okay=False))
@click.option("--include-attachments", is_flag=True)
@fields_option()
@output_option()
@click.pass_context
def get_example(
    ctx,
    example_id,
    as_of,
    source,
    archive_uri,
    local_dir,
    include_attachments,
    fields,
    output,
):
    """Fetch details of a single example."""
    source = ReplicaSource(source)
    logger = ctx.obj["logger"]
    configure_logger_streams(ctx, logger, output=output, fields=fields)

    logger.debug(f"Fetching example: example_id={example_id}, as_of={as_of}")

    if source is ReplicaSource.CLOUD:
        client = get_or_create_client(ctx)
        example = client.read_example(example_id, as_of=as_of)
    else:
        repository = _replica_repository(source, archive_uri, local_dir)
        example = repository.read_example(
            example_id,
            as_of=as_of,
            include_attachments=include_attachments,
        )

    data = filter_fields(example, fields)

    def render_example_details(data: dict, console: ConsoleProtocol) -> None:
        from rich.syntax import Syntax

        render_detail_fields(data, console, [("id", "Example ID")])
        if "inputs" in data:
            console.print("\n[bold]Inputs:[/bold]")
            console.print(Syntax(json_dumps(data["inputs"], indent=2), "json"))
        if "outputs" in data:
            console.print("\n[bold]Outputs:[/bold]")
            console.print(Syntax(json_dumps(data["outputs"], indent=2), "json"))

    output_single_item(
        ctx, data, console, output=output, render_fn=render_example_details
    )


def _replica_repository(source, archive_uri, local_dir):
    from langsmith_cli.dataset_replica.repository import DatasetReplicaError
    from langsmith_cli.dataset_replica.service import repository_for

    try:
        return repository_for(
            source, archive_uri=archive_uri, local_directory=local_dir
        )
    except KeyError as exc:
        raise click.ClickException(
            "Archive reads require --archive-uri or LANGSMITH_ARCHIVE_URI"
        ) from exc
    except (DatasetReplicaError, ValueError, OSError) as exc:
        raise click.ClickException(str(exc)) from exc


@examples.command("create")
@click.option("--dataset", required=True, help="Dataset ID or Name.")
@click.option("--inputs", required=True, help="JSON string of inputs.")
@click.option("--outputs", help="JSON string of outputs.")
@click.option("--metadata", help="JSON string of metadata.")
@click.option("--split", help="Dataset split (e.g., train, test, validation).")
@click.pass_context
def create_example(ctx, dataset, inputs, outputs, metadata, split):
    """Create a new example in a dataset."""
    logger = ctx.obj["logger"]
    configure_logger_streams(ctx, logger)

    logger.debug(f"Creating example in dataset: {dataset}")

    client = get_or_create_client(ctx)

    input_dict = parse_json_string(inputs, "inputs")
    output_dict = parse_json_string(outputs, "outputs")
    metadata_dict = parse_json_string(metadata, "metadata")

    example = client.create_example(
        inputs=input_dict,
        outputs=output_dict,
        dataset_name=dataset,
        metadata=metadata_dict,
        split=normalize_split(split),
    )

    emit_action_result(
        ctx,
        logger,
        model=example,
        success_message=f"Created example (ID: {example.id}) in dataset {dataset}",
    )


@examples.command("update")
@click.argument("example_id")
@click.option("--inputs", help="JSON string of new inputs.")
@click.option("--outputs", help="JSON string of new outputs.")
@click.option("--metadata", help="JSON string of new metadata.")
@click.option("--split", help="Dataset split (e.g., train, test, validation).")
@click.pass_context
def update_example(ctx, example_id, inputs, outputs, metadata, split):
    """Update an existing example's inputs, outputs, or metadata."""
    logger = ctx.obj["logger"]
    configure_logger_streams(ctx, logger)

    if not any([inputs, outputs, metadata, split]):
        raise click.UsageError(
            "At least one of --inputs, --outputs, --metadata, or --split is required."
        )

    logger.debug(f"Updating example: {example_id}")

    client = get_or_create_client(ctx)

    input_dict = parse_json_string(inputs, "inputs")
    output_dict = parse_json_string(outputs, "outputs")
    metadata_dict = parse_json_string(metadata, "metadata")

    result = client.update_example(
        example_id,
        inputs=input_dict,
        outputs=output_dict,
        metadata=metadata_dict,
        split=normalize_split(split),
    )

    emit_action_result(
        ctx,
        logger,
        payload=result,
        success_message=f"Updated example {example_id}",
    )


@examples.command("delete")
@click.argument("example_ids", nargs=-1, required=True)
@confirm_option()
@click.pass_context
def delete_examples(ctx, example_ids, confirm):
    """Delete one or more examples by ID."""
    logger = ctx.obj["logger"]
    configure_logger_streams(ctx, logger)

    require_confirmation(
        confirm,
        f"Are you sure you want to delete {len(example_ids)} example(s)?",
    )

    logger.debug(f"Deleting {len(example_ids)} example(s)")

    client = get_or_create_client(ctx)

    from langsmith.utils import LangSmithError, LangSmithNotFoundError

    deleted = []
    errors = []
    for eid in example_ids:
        try:
            client.delete_example(eid)
            deleted.append(eid)
        except (LangSmithNotFoundError, LangSmithError) as e:
            errors.append({"id": eid, "error": str(e)})

    if is_json_context(ctx):
        click.echo(
            json_dumps({"status": "success", "deleted": deleted, "errors": errors})
        )
    else:
        if deleted:
            logger.success(f"Deleted {len(deleted)} example(s)")
        if errors:
            for err in errors:
                logger.warning(f"Failed to delete {err['id']}: {err['error']}")


@examples.command("from-run")
@click.argument("run_id")
@click.option("--dataset", required=True, help="Dataset name to add the example to.")
@click.pass_context
def example_from_run(ctx, run_id, dataset):
    """Create an example from a run's inputs/outputs."""
    logger = ctx.obj["logger"]
    configure_logger_streams(ctx, logger)

    logger.debug(f"Creating example from run {run_id} in dataset {dataset}")

    client = get_or_create_client(ctx)

    # Read the run first
    with not_found_as_click_exception("Run", run_id):
        run = client.read_run(run_id)

    # Create example from the run
    example = client.create_example_from_run(run, dataset_name=dataset)

    emit_action_result(
        ctx,
        logger,
        model=example,
        success_message=(
            f"Created example (ID: {example.id}) from run {run_id} in dataset '{dataset}'"
        ),
    )
