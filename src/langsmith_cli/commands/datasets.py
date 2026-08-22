from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any, TypedDict

import click
from langsmith_cli.dataset_replica.cli import (
    replica_repository,
    replica_source_options,
)
from langsmith_cli.dataset_replica.models import ReplicaDestination, ReplicaSource
from langsmith_cli.dataset_resolution import (
    DatasetResolutionError,
    resolve_dataset as resolve_dataset_strict,
)
from langsmith_cli.utils import (
    ConsoleProtocol,
    LazyConsole,
    add_name_filter_options,
    apply_exclude_filter,
    apply_name_filters,
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
    sort_by_option,
    sort_items,
    parse_fields_option,
    output_option,
    output_single_item,
    parse_comma_separated_list,
    parse_json_string,
    render_detail_fields,
    render_output,
    require_confirmation,
)

if TYPE_CHECKING:
    from langsmith import Client
    from langsmith.schemas import Dataset

console = LazyConsole()


def resolve_dataset(client: Client, name_or_id: str) -> Dataset:
    """Translate pure Dataset identity failures at the Click view boundary."""
    try:
        return resolve_dataset_strict(client, name_or_id)
    except DatasetResolutionError as exc:
        raise click.ClickException(str(exc)) from exc


class DatasetPushRow(TypedDict):
    """Validated JSONL row for datasets push.

    Both ``inputs`` and ``outputs`` are populated by the validator;
    ``outputs`` may be ``None`` if the source row omitted it.
    """

    inputs: dict[str, Any]
    outputs: dict[str, Any] | None


def _validate_dataset_push_row(raw_row: Any, line_number: int) -> DatasetPushRow:
    if not isinstance(raw_row, dict):
        raise click.ClickException(
            f"{line_number}: expected a JSON object with 'inputs' and optional 'outputs'."
        )
    if "inputs" not in raw_row:
        raise click.ClickException(f"{line_number}: missing required field 'inputs'.")
    inputs = raw_row["inputs"]
    if not isinstance(inputs, dict):
        raise click.ClickException(f"{line_number}: field 'inputs' must be an object.")

    outputs: dict[str, Any] | None = None
    if "outputs" in raw_row:
        raw_outputs = raw_row["outputs"]
        if raw_outputs is not None and not isinstance(raw_outputs, dict):
            raise click.ClickException(
                f"{line_number}: field 'outputs' must be an object or null."
            )
        outputs = raw_outputs
    return DatasetPushRow(inputs=inputs, outputs=outputs)


@click.group()
def datasets():
    """Manage LangSmith datasets."""
    pass


@datasets.command("list")
@replica_source_options()
@click.option("--dataset-ids", help="Specific dataset IDs (comma-separated).")
@click.option("--limit", default=20, help="Limit number of datasets (default 20).")
@click.option("--data-type", help="Filter by dataset type (kv, chat, llm).")
@click.option("--name", "dataset_name", help="Exact dataset name match.")
@click.option("--name-contains", help="Dataset name substring search.")
@click.option("--metadata", help="Filter by metadata (JSON string).")
@add_name_filter_options
@sort_by_option(fields="name, created_at, example_count")
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
def list_datasets(
    ctx,
    source,
    archive_uri,
    local_dir,
    dataset_ids,
    limit,
    data_type,
    dataset_name,
    name_contains,
    metadata,
    name_pattern,
    name_regex,
    sort_by,
    output_format,
    exclude,
    fields,
    count,
    output,
):
    """List all available datasets."""
    source = ReplicaSource(source)
    logger = ctx.obj["logger"]
    configure_logger_streams(
        ctx, logger, output=output, output_format=output_format, fields=fields
    )

    logger.debug(
        f"Listing datasets: limit={limit}, data_type={data_type}, "
        f"dataset_name={dataset_name}, name_contains={name_contains}"
    )

    # Parse comma-separated dataset IDs
    dataset_ids_list = parse_comma_separated_list(dataset_ids)

    # Parse metadata JSON
    metadata_dict = parse_json_string(metadata, "metadata")

    if source is ReplicaSource.CLOUD:
        client = get_or_create_client(ctx)
        # Build kwargs for list_datasets (type-safe approach)
        list_kwargs = {
            "limit": limit,
            "data_type": data_type,
            "dataset_name": dataset_name,
            "dataset_name_contains": name_contains,
            "metadata": metadata_dict,
        }
        if dataset_ids_list is not None:
            list_kwargs["dataset_ids"] = dataset_ids_list
        datasets_list = list(client.list_datasets(**list_kwargs))
    else:
        repository = replica_repository(source, archive_uri, local_dir)
        datasets_list = repository.list_datasets()
        if dataset_ids_list is not None:
            selected_ids = set(dataset_ids_list)
            datasets_list = [d for d in datasets_list if str(d.id) in selected_ids]
        if data_type is not None:
            datasets_list = [
                d
                for d in datasets_list
                if d.data_type is not None and d.data_type.value == data_type
            ]
        if dataset_name is not None:
            datasets_list = [d for d in datasets_list if d.name == dataset_name]
        if name_contains is not None:
            datasets_list = [d for d in datasets_list if name_contains in d.name]
        if metadata_dict is not None:
            datasets_list = [
                d
                for d in datasets_list
                if d.metadata is not None
                and all(
                    key in d.metadata and d.metadata[key] == value
                    for key, value in metadata_dict.items()
                )
            ]
        datasets_list = datasets_list[:limit]

    # Client-side name pattern/regex filtering
    datasets_list = apply_name_filters(
        datasets_list,
        lambda d: d.name,
        name_pattern=name_pattern,
        name_regex=name_regex,
    )

    # Client-side exclude filtering
    datasets_list = apply_exclude_filter(datasets_list, exclude, lambda d: d.name)

    # Client-side sorting
    if sort_by:
        datasets_list = sort_items(
            datasets_list,
            sort_by,
            {
                "name": lambda d: d.name,
                "created_at": lambda d: d.created_at,
                "example_count": lambda d: d.example_count,
            },
        )

    # Define table builder function
    def build_datasets_table(datasets):
        from rich.table import Table

        table = Table(title="Datasets")
        table.add_column("Name", style="cyan")
        table.add_column("ID", style="dim")
        table.add_column("Type")
        for d in datasets:
            table.add_row(d.name, str(d.id), d.data_type)
        return table

    include_fields = parse_fields_option(fields)

    # Unified output rendering (handles --json, --format, --output, --count uniformly)
    render_output(
        datasets_list,
        build_datasets_table,
        ctx,
        include_fields=include_fields,
        empty_message="No datasets found",
        output_format=output_format,
        count_flag=count,
        output_path=output,
    )


@datasets.command("get")
@click.argument("dataset_id")
@replica_source_options()
@click.option("--as-of", default="latest", show_default=True)
@fields_option()
@output_option()
@click.pass_context
def get_dataset(ctx, dataset_id, source, archive_uri, local_dir, as_of, fields, output):
    """Fetch details of a single dataset."""
    source = ReplicaSource(source)
    logger = ctx.obj["logger"]
    configure_logger_streams(ctx, logger, output=output, fields=fields)

    logger.debug(f"Fetching dataset: dataset_id={dataset_id}")

    if source is ReplicaSource.CLOUD:
        if as_of != "latest":
            raise click.ClickException(
                "--as-of is only supported for --source archive or local on "
                "datasets get; cloud Dataset metadata has no versioned read API"
            )
        client = get_or_create_client(ctx)
        dataset = resolve_dataset(client, dataset_id)
    else:
        repository = replica_repository(source, archive_uri, local_dir)
        dataset = repository.read_dataset(dataset_id, as_of)

    data = filter_fields(dataset, fields)

    def render_dataset_details(data: dict, console: ConsoleProtocol) -> None:
        render_detail_fields(
            data,
            console,
            [
                ("name", "Name"),
                ("id", "ID"),
                ("description", "Description"),
            ],
        )

    output_single_item(
        ctx, data, console, output=output, render_fn=render_dataset_details
    )


@datasets.command("create")
@click.argument("name")
@click.option("--description", help="Dataset description.")
@click.option(
    "--type",
    "dataset_type",
    default="kv",
    type=click.Choice(["kv", "llm", "chat"], case_sensitive=False),
    help="Dataset type (kv, llm, or chat)",
)
@click.pass_context
def create_dataset(ctx, name, description, dataset_type):
    """Create a new dataset."""
    from langsmith.schemas import DataType

    logger = ctx.obj["logger"]
    configure_logger_streams(ctx, logger)

    logger.debug(f"Creating dataset: name={name}, type={dataset_type}")

    client = get_or_create_client(ctx)

    # Convert string to DataType enum
    data_type_enum = DataType(dataset_type)

    dataset = client.create_dataset(
        dataset_name=name, description=description, data_type=data_type_enum
    )

    emit_action_result(
        ctx,
        logger,
        model=dataset,
        success_message=f"Created dataset {dataset.name} (ID: {dataset.id})",
    )


@datasets.command("push")
@click.argument("file_path", type=click.Path(exists=True))
@click.option("--dataset", help="Dataset name to push to. Created if not exists.")
@click.pass_context
def push_dataset(ctx, file_path, dataset):
    """Upload examples from a JSONL file to a dataset."""
    import json

    logger = ctx.obj["logger"]
    configure_logger_streams(ctx, logger)

    logger.debug(f"Pushing dataset from file: {file_path}")

    client = get_or_create_client(ctx)

    if not dataset:
        dataset = os.path.basename(file_path).split(".")[0]

    # Create dataset if not exists (simple check)
    from langsmith.utils import LangSmithNotFoundError

    try:
        client.read_dataset(dataset_name=dataset)
    except LangSmithNotFoundError:
        logger.warning(f"Dataset '{dataset}' not found. Creating it...")
        client.create_dataset(dataset_name=dataset)

    examples: list[DatasetPushRow] = []
    with open(file_path, "r", encoding="utf-8") as f:
        for line_number, line in enumerate(f, start=1):
            if not line.strip():
                continue
            try:
                raw_row = json.loads(line)
            except json.JSONDecodeError as e:
                raise click.ClickException(
                    f"{line_number}: invalid JSON: {e.msg}."
                ) from e
            examples.append(_validate_dataset_push_row(raw_row, line_number))

    # Expecting examples in [{"inputs": {...}, "outputs": {...}}, ...] format
    client.create_examples(
        inputs=[e["inputs"] for e in examples],
        outputs=[e["outputs"] for e in examples],
        dataset_name=dataset,
    )

    emit_action_result(
        ctx,
        logger,
        payload={
            "status": "success",
            "dataset": dataset,
            "examples_count": len(examples),
        },
        success_message=f"Successfully pushed {len(examples)} examples to dataset '{dataset}'",
    )


@datasets.command("delete")
@click.argument("name_or_id")
@confirm_option()
@click.pass_context
def delete_dataset(ctx, name_or_id, confirm):
    """Delete a dataset by name or ID."""
    logger = ctx.obj["logger"]
    configure_logger_streams(ctx, logger)

    require_confirmation(
        confirm, f"Are you sure you want to delete dataset '{name_or_id}'?"
    )

    logger.debug(f"Deleting dataset: {name_or_id}")

    client = get_or_create_client(ctx)

    # Resolve first, then delete by ID (consistent pattern)
    dataset = resolve_dataset(client, name_or_id)
    client.delete_dataset(dataset_id=str(dataset.id))

    emit_action_result(
        ctx,
        logger,
        payload={"status": "success", "name": dataset.name},
        success_message=f"Deleted dataset '{dataset.name}'",
    )


@datasets.command("versions")
@click.argument("dataset")
@replica_source_options()
@fields_option()
@output_option()
@click.pass_context
def list_dataset_versions(ctx, dataset, source, archive_uri, local_dir, fields, output):
    """List exact versions available from one dataset source."""
    source = ReplicaSource(source)
    logger = ctx.obj["logger"]
    configure_logger_streams(ctx, logger, output=output, fields=fields)
    if source is ReplicaSource.CLOUD:
        client = get_or_create_client(ctx)
        resolved = resolve_dataset(client, dataset)
        versions = list(client.list_dataset_versions(dataset_id=resolved.id))
    else:
        repository = replica_repository(source, archive_uri, local_dir)
        versions = repository.list_versions(dataset)

    def build_versions_table(items):
        from rich.table import Table

        table = Table(title=f"Dataset versions: {dataset}")
        table.add_column("As of", style="cyan")
        table.add_column("Tags")
        for item in items:
            table.add_row(item.as_of.isoformat(), ", ".join(item.tags or []))
        return table

    render_output(
        versions,
        build_versions_table,
        ctx,
        include_fields=parse_fields_option(fields),
        empty_message="No dataset versions found",
        output_path=output,
    )


@datasets.command("status")
@replica_source_options(include_cloud=False, default=ReplicaSource.LOCAL)
@output_option()
@click.pass_context
def dataset_replica_status(ctx, source, archive_uri, local_dir, output):
    """Show datasets and exact versions present in a replica source."""
    source = ReplicaSource(source)
    logger = ctx.obj["logger"]
    configure_logger_streams(ctx, logger, output=output)
    repository = replica_repository(source, archive_uri, local_dir)
    payload = [
        {
            "dataset_id": item.dataset_id,
            "dataset_name": item.dataset_name,
            "latest_as_of": item.latest_as_of.isoformat(),
            "versions": item.versions,
            "source": source.value,
            "source_uri": item.source_uri,
        }
        for item in repository.statuses()
    ]

    def build_status_table(items):
        from rich.table import Table

        table = Table(title=f"Dataset replica: {source.value}")
        table.add_column("Dataset", style="cyan")
        table.add_column("ID", style="dim")
        table.add_column("Versions", justify="right")
        table.add_column("Latest")
        for item in items:
            table.add_row(
                item["dataset_name"],
                item["dataset_id"],
                str(item["versions"]),
                item["latest_as_of"],
            )
        return table

    render_output(
        payload,
        build_status_table,
        ctx,
        empty_message="No replicated datasets found",
        output_path=output,
    )


@datasets.command("pull")
@click.argument("dataset")
@replica_source_options()
@click.option(
    "--to",
    "destination",
    type=click.Choice([item.value for item in ReplicaDestination]),
    required=True,
)
@click.option("--as-of", default="latest", show_default=True)
@click.option("--all-versions", is_flag=True)
@click.pass_context
def pull_dataset_command(
    ctx,
    dataset,
    source,
    destination,
    as_of,
    all_versions,
    archive_uri,
    local_dir,
):
    """Replicate exact, read-only dataset versions between sources."""
    from langsmith_cli.dataset_replica.service import (
        pull_dataset,
        write_result_payload,
    )
    from langsmith_cli.dataset_replica.repository import DatasetReplicaError

    source = ReplicaSource(source)
    destination = ReplicaDestination(destination)
    logger = ctx.obj["logger"]
    configure_logger_streams(ctx, logger)
    client = get_or_create_client(ctx) if source is ReplicaSource.CLOUD else None
    try:
        results = pull_dataset(
            client=client,
            dataset_name_or_id=dataset,
            source=source,
            destination=destination,
            as_of=as_of,
            all_versions=all_versions,
            archive_uri=archive_uri,
            local_directory=local_dir,
        )
    except (DatasetReplicaError, ValueError, OSError) as exc:
        raise click.ClickException(str(exc)) from exc
    payload = [write_result_payload(result) for result in results]
    if is_json_context(ctx):
        click.echo(json_dumps(payload))
        return
    created = sum(not result.already_present for result in results)
    logger.success(
        f"Replicated {len(results)} version(s) of '{dataset}' "
        f"to {destination.value} ({created} new)"
    )
