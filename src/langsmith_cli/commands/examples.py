import click
from rich.console import Console
from rich.table import Table
import langsmith
import json

console = Console()


@click.group()
def examples():
    """Manage dataset examples."""
    pass


@examples.command("list")
@click.option("--dataset", required=True, help="Dataset ID or Name.")
@click.option("--limit", default=20, help="Limit number of examples (default 20).")
@click.option("--offset", default=0, help="Number of examples to skip (pagination).")
@click.option("--filter", "filter_", help="LangSmith query filter.")
@click.option("--splits", help="Filter by dataset splits (comma-separated).")
@click.pass_context
def list_examples(ctx, dataset, limit, offset, filter_, splits):
    """List examples for a dataset."""
    client = langsmith.Client()

    # Parse splits if provided
    splits_list = None
    if splits:
        splits_list = [s.strip() for s in splits.split(",")]

    # list_examples takes dataset_name and limit
    examples_gen = client.list_examples(
        dataset_name=dataset,
        limit=limit,
        offset=offset,
        filter=filter_,
        splits=splits_list,
    )
    examples_list = list(examples_gen)

    if ctx.obj.get("json"):
        # Use SDK's Pydantic models with focused field selection for context efficiency
        data = [
            e.model_dump(
                include={
                    "id",
                    "inputs",
                    "outputs",
                    "metadata",
                    "dataset_id",
                    "created_at",
                    "modified_at",
                },
                mode="json",
            )
            for e in examples_list
        ]
        click.echo(json.dumps(data, default=str))
        return

    table = Table(title=f"Examples: {dataset}")
    table.add_column("ID", style="dim")
    table.add_column("Inputs")
    table.add_column("Outputs")

    for e in examples_list:
        inputs = json.dumps(e.inputs)
        outputs = json.dumps(e.outputs)
        # Truncate for table
        if len(inputs) > 50:
            inputs = inputs[:47] + "..."
        if len(outputs) > 50:
            outputs = outputs[:47] + "..."

        table.add_row(str(e.id), inputs, outputs)

    if not examples_list:
        console.print("[yellow]No examples found.[/yellow]")
    else:
        console.print(table)


@examples.command("get")
@click.argument("example_id")
@click.pass_context
def get_example(ctx, example_id):
    """Fetch details of a single example."""
    client = langsmith.Client()
    example = client.read_example(example_id)

    data = example.dict() if hasattr(example, "dict") else dict(example)

    if ctx.obj.get("json"):
        click.echo(json.dumps(data, default=str))
        return

    from rich.syntax import Syntax

    console.print(f"[bold]Example ID:[/bold] {data.get('id')}")
    console.print("\n[bold]Inputs:[/bold]")
    console.print(Syntax(json.dumps(data.get("inputs"), indent=2), "json"))
    console.print("\n[bold]Outputs:[/bold]")
    console.print(Syntax(json.dumps(data.get("outputs"), indent=2), "json"))


@examples.command("create")
@click.option("--dataset", required=True, help="Dataset ID or Name.")
@click.option("--inputs", required=True, help="JSON string of inputs.")
@click.option("--outputs", help="JSON string of outputs.")
@click.option("--metadata", help="JSON string of metadata.")
@click.option("--split", help="Dataset split (e.g., train, test, validation).")
@click.pass_context
def create_example(ctx, dataset, inputs, outputs, metadata, split):
    """Create a new example in a dataset."""
    client = langsmith.Client()

    input_dict = json.loads(inputs)
    output_dict = json.loads(outputs) if outputs else None
    metadata_dict = json.loads(metadata) if metadata else None

    # Handle split - can be a single string or list
    split_value = None
    if split:
        split_value = [split] if isinstance(split, str) else split

    example = client.create_example(
        inputs=input_dict,
        outputs=output_dict,
        dataset_name=dataset,
        metadata=metadata_dict,
        split=split_value,
    )

    if ctx.obj.get("json"):
        data = example.dict() if hasattr(example, "dict") else dict(example)
        click.echo(json.dumps(data, default=str))
        return

    console.print(
        f"[green]Created example[/green] (ID: {example.id}) in dataset {dataset}"
    )
