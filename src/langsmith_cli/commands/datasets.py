import click
from rich.console import Console
from rich.table import Table
import langsmith
import json
import os

console = Console()


@click.group()
def datasets():
    """Manage LangSmith datasets."""
    pass


@datasets.command("list")
@click.pass_context
def list_datasets(ctx):
    """List all available datasets."""
    client = langsmith.Client()
    datasets = client.list_datasets()

    if ctx.obj.get("json"):
        data = [d.dict() if hasattr(d, "dict") else dict(d) for d in datasets]
        click.echo(json.dumps(data, default=str))
        return

    table = Table(title="Datasets")
    table.add_column("Name", style="cyan")
    table.add_column("ID", style="dim")
    table.add_column("Type")

    for d in datasets:
        table.add_row(
            getattr(d, "name", "Unknown"),
            str(getattr(d, "id", "")),
            getattr(d, "dataset_type", "kv"),
        )
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
    "--type", "dataset_type", default="kv", help="Dataset type (kv, chat, etc.)"
)
@click.pass_context
def create_dataset(ctx, name, description, dataset_type):
    """Create a new dataset."""
    client = langsmith.Client()
    dataset = client.create_dataset(
        dataset_name=name, description=description, dataset_type=dataset_type
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
