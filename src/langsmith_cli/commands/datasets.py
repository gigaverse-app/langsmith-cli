import click
from rich.console import Console
from rich.table import Table
import langsmith
import json
import os
from langsmith_cli.utils import (
    print_empty_result_message,
    parse_json_string,
    parse_comma_separated_list,
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
@click.pass_context
def list_datasets(
    ctx, dataset_ids, limit, data_type, dataset_name, name_contains, metadata
):
    """List all available datasets."""
    client = langsmith.Client()

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

    if ctx.obj.get("json"):
        # Use SDK's Pydantic models with focused field selection for context efficiency
        data = [
            d.model_dump(
                include={
                    "id",
                    "name",
                    "inputs_schema",
                    "outputs_schema",
                    "description",
                    "data_type",
                    "example_count",
                    "session_count",
                    "created_at",
                    "modified_at",
                    "last_session_start_time",
                },
                mode="json",
            )
            for d in datasets_list
        ]
        click.echo(json.dumps(data, default=str))
        return

    table = Table(title="Datasets")
    table.add_column("Name", style="cyan")
    table.add_column("ID", style="dim")
    table.add_column("Type")

    for d in datasets_list:
        table.add_row(
            d.name,
            str(d.id),
            d.data_type,
        )

    if not datasets_list:
        print_empty_result_message(console, "datasets")
    else:
        console.print(table)


@datasets.command("get")
@click.argument("dataset_id")
@click.pass_context
def get_dataset(ctx, dataset_id):
    """Fetch details of a single dataset."""
    client = langsmith.Client()
    dataset = client.read_dataset(dataset_id=dataset_id)

    data = dataset.dict() if hasattr(dataset, "dict") else dict(dataset)

    if ctx.obj.get("json"):
        click.echo(json.dumps(data, default=str))
        return

    console.print(f"[bold]Name:[/bold] {data.get('name')}")
    console.print(f"[bold]ID:[/bold] {data.get('id')}")
    console.print(f"[bold]Description:[/bold] {data.get('description')}")


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

    client = langsmith.Client()

    # Convert string to DataType enum
    data_type_enum = DataType(dataset_type)

    dataset = client.create_dataset(
        dataset_name=name, description=description, data_type=data_type_enum
    )

    if ctx.obj.get("json"):
        data = dataset.dict() if hasattr(dataset, "dict") else dict(dataset)
        click.echo(json.dumps(data, default=str))
        return

    console.print(f"[green]Created dataset {dataset.name}[/green] (ID: {dataset.id})")


@datasets.command("push")
@click.argument("file_path", type=click.Path(exists=True))
@click.option("--dataset", help="Dataset name to push to. Created if not exists.")
@click.pass_context
def push_dataset(ctx, file_path, dataset):
    """Upload examples from a JSONL file to a dataset."""
    client = langsmith.Client()

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
