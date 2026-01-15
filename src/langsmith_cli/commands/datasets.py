import click
from rich.console import Console
from rich.table import Table
import os
from langsmith_cli.utils import (
    parse_json_string,
    parse_comma_separated_list,
    fields_option,
    filter_fields,
    get_or_create_client,
    render_output,
    safe_model_dump,
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
@fields_option()
@click.pass_context
def list_datasets(
    ctx, dataset_ids, limit, data_type, dataset_name, name_contains, metadata, fields
):
    """List all available datasets."""
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
    )


@datasets.command("get")
@click.argument("dataset_id")
@fields_option()
@click.pass_context
def get_dataset(ctx, dataset_id, fields):
    """Fetch details of a single dataset."""
    import json

    client = get_or_create_client(ctx)
    dataset = client.read_dataset(dataset_id=dataset_id)

    # Use shared field filtering utility
    data = filter_fields(dataset, fields)

    if ctx.obj.get("json"):
        click.echo(json.dumps(data, default=str))
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
    import json
    from langsmith.schemas import DataType

    client = get_or_create_client(ctx)

    # Convert string to DataType enum
    data_type_enum = DataType(dataset_type)

    dataset = client.create_dataset(
        dataset_name=name, description=description, data_type=data_type_enum
    )

    if ctx.obj.get("json"):
        data = safe_model_dump(dataset)
        click.echo(json.dumps(data, default=str))
        return

    console.print(f"[green]Created dataset {dataset.name}[/green] (ID: {dataset.id})")


@datasets.command("push")
@click.argument("file_path", type=click.Path(exists=True))
@click.option("--dataset", help="Dataset name to push to. Created if not exists.")
@click.pass_context
def push_dataset(ctx, file_path, dataset):
    """Upload examples from a JSONL file to a dataset."""
    import json

    client = get_or_create_client(ctx)

    if not dataset:
        dataset = os.path.basename(file_path).split(".")[0]

    # Create dataset if not exists (simple check)
    try:
        client.read_dataset(dataset_name=dataset)
    except Exception:
        console.print(f"[yellow]Dataset '{dataset}' not found. Creating it...[/yellow]")
        client.create_dataset(dataset_name=dataset)

    examples = []
    with open(file_path, "r") as f:
        for line in f:
            if line.strip():
                examples.append(json.loads(line))

    # Expecting examples in [{"inputs": {...}, "outputs": {...}}, ...] format
    client.create_examples(
        inputs=[e.get("inputs", {}) for e in examples],
        outputs=[e.get("outputs") for e in examples],
        dataset_name=dataset,
    )

    console.print(
        f"[green]Successfully pushed {len(examples)} examples to dataset '{dataset}'[/green]"
    )
