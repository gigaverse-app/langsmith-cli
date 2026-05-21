from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any, TypedDict

import click
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
    resolve_by_name_or_id,
    sort_by_option,
    sort_items,
    parse_fields_option,
    output_option,
    output_single_item,
    parse_comma_separated_list,
    parse_json_string,
    render_output,
    require_confirmation,
)

if TYPE_CHECKING:
    from langsmith import Client
    from langsmith.schemas import Dataset

console = LazyConsole()


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
    logger = ctx.obj["logger"]
    configure_logger_streams(
        ctx, logger, output=output, output_format=output_format, fields=fields
    )

    logger.debug(
        f"Listing datasets: limit={limit}, data_type={data_type}, "
        f"dataset_name={dataset_name}, name_contains={name_contains}"
    )

    client = get_or_create_client(ctx)

    # Parse comma-separated dataset IDs
    dataset_ids_list = parse_comma_separated_list(dataset_ids)

    # Parse metadata JSON
    metadata_dict = parse_json_string(metadata, "metadata")

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

    datasets_gen = client.list_datasets(**list_kwargs)
    datasets_list = list(datasets_gen)

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
@fields_option()
@output_option()
@click.pass_context
def get_dataset(ctx, dataset_id, fields, output):
    """Fetch details of a single dataset."""
    logger = ctx.obj["logger"]
    configure_logger_streams(ctx, logger, output=output, fields=fields)

    logger.debug(f"Fetching dataset: dataset_id={dataset_id}")

    client = get_or_create_client(ctx)
    dataset = client.read_dataset(dataset_id=dataset_id)

    data = filter_fields(dataset, fields)

    def render_dataset_details(data: dict, console: ConsoleProtocol) -> None:
        console.print(f"[bold]Name:[/bold] {data.get('name')}")
        console.print(f"[bold]ID:[/bold] {data.get('id')}")
        console.print(f"[bold]Description:[/bold] {data.get('description')}")

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


def resolve_dataset(
    client: Client,
    name_or_id: str,
) -> Dataset:
    """Resolve a dataset by name or UUID, with smart UUID auto-detection."""
    return resolve_by_name_or_id(
        name_or_id,
        read_by_name=lambda n: client.read_dataset(dataset_name=n),
        read_by_id=lambda i: client.read_dataset(dataset_id=i),
        entity_name="Dataset",
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
