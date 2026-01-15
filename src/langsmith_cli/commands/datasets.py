import click
from rich.console import Console
from rich.table import Table
import os
from langsmith_cli.utils import (
    apply_exclude_filter,
    count_option,
    exclude_option,
    fields_option,
    filter_fields,
    get_or_create_client,
    json_dumps,
    output_option,
    parse_comma_separated_list,
    parse_json_string,
    render_output,
    safe_model_dump,
    write_output_to_file,
)

console = Console()


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
    exclude,
    fields,
    count,
    output,
):
    """List all available datasets."""
    logger = ctx.obj["logger"]
    is_machine_readable = ctx.obj.get("json") or bool(output) or bool(fields)
    logger.use_stderr = is_machine_readable

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

    # Client-side exclude filtering
    datasets_list = apply_exclude_filter(datasets_list, exclude, lambda d: d.name)

    # Handle file output - short circuit if writing to file
    if output:
        data = filter_fields(datasets_list, fields)
        write_output_to_file(data, output, console, format_type="jsonl")
        return

    # Define table builder function
    def build_datasets_table(datasets):
        table = Table(title="Datasets")
        table.add_column("Name", style="cyan")
        table.add_column("ID", style="dim")
        table.add_column("Type")
        for d in datasets:
            table.add_row(d.name, str(d.id), d.data_type)
        return table

    # Determine which fields to include
    if fields:
        include_fields = {f.strip() for f in fields.split(",") if f.strip()}
    else:
        # Default fields for output
        include_fields = None

    # Unified output rendering
    render_output(
        datasets_list,
        build_datasets_table,
        ctx,
        include_fields=include_fields,
        empty_message="No datasets found",
        count_flag=count,
    )


@datasets.command("get")
@click.argument("dataset_id")
@fields_option()
@click.pass_context
def get_dataset(ctx, dataset_id, fields):
    """Fetch details of a single dataset."""
    logger = ctx.obj["logger"]
    is_machine_readable = ctx.obj.get("json") or bool(fields)
    logger.use_stderr = is_machine_readable

    logger.debug(f"Fetching dataset: dataset_id={dataset_id}")

    client = get_or_create_client(ctx)
    dataset = client.read_dataset(dataset_id=dataset_id)

    # Use shared field filtering utility
    data = filter_fields(dataset, fields)

    if ctx.obj.get("json"):
        click.echo(json_dumps(data))
        return

    console.print(f"[bold]Name:[/bold] {dataset.name}")
    console.print(f"[bold]ID:[/bold] {dataset.id}")
    console.print(f"[bold]Description:[/bold] {dataset.description}")


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
    is_machine_readable = ctx.obj.get("json")
    logger.use_stderr = is_machine_readable

    logger.debug(f"Creating dataset: name={name}, type={dataset_type}")

    client = get_or_create_client(ctx)

    # Convert string to DataType enum
    data_type_enum = DataType(dataset_type)

    dataset = client.create_dataset(
        dataset_name=name, description=description, data_type=data_type_enum
    )

    if ctx.obj.get("json"):
        data = safe_model_dump(dataset)
        click.echo(json_dumps(data))
        return

    logger.success(f"Created dataset {dataset.name} (ID: {dataset.id})")


@datasets.command("push")
@click.argument("file_path", type=click.Path(exists=True))
@click.option("--dataset", help="Dataset name to push to. Created if not exists.")
@click.pass_context
def push_dataset(ctx, file_path, dataset):
    """Upload examples from a JSONL file to a dataset."""
    import json

    logger = ctx.obj["logger"]
    is_machine_readable = ctx.obj.get("json")
    logger.use_stderr = is_machine_readable

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

    examples = []
    with open(file_path, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                examples.append(json.loads(line))

    # Expecting examples in [{"inputs": {...}, "outputs": {...}}, ...] format
    client.create_examples(
        inputs=[e.get("inputs", {}) for e in examples],
        outputs=[e.get("outputs") for e in examples],
        dataset_name=dataset,
    )

    logger.success(
        f"Successfully pushed {len(examples)} examples to dataset '{dataset}'"
    )
