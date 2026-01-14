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
@click.pass_context
def list_examples(ctx, dataset):
    """List examples for a dataset."""
    client = langsmith.Client()
    # Try ID first, then name? The SDK handles some of this or we might need to resolve it.
    examples = client.list_examples(
        dataset_name=dataset
    )  # list_examples often takes name or id

    if ctx.obj.get("json"):
        data = [e.dict() if hasattr(e, "dict") else dict(e) for e in examples]
        click.echo(json.dumps(data, default=str))
        return

    table = Table(title=f"Examples: {dataset}")
    table.add_column("ID", style="dim")
    table.add_column("Inputs")
    table.add_column("Outputs")

    for e in examples:
        inputs = json.dumps(getattr(e, "inputs", {}))
        outputs = json.dumps(getattr(e, "outputs", {}))
        # Truncate for table
        if len(inputs) > 50:
            inputs = inputs[:47] + "..."
        if len(outputs) > 50:
            outputs = outputs[:47] + "..."

        table.add_row(str(getattr(e, "id", "")), inputs, outputs)

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
@click.pass_context
def create_example(ctx, dataset, inputs, outputs):
    """Create a new example in a dataset."""
    client = langsmith.Client()

    input_dict = json.loads(inputs)
    output_dict = json.loads(outputs) if outputs else None

    example = client.create_example(
        inputs=input_dict, outputs=output_dict, dataset_name=dataset
    )

    if ctx.obj.get("json"):
        data = example.dict() if hasattr(example, "dict") else dict(example)
        click.echo(json.dumps(data, default=str))
        return

    console.print(
        f"[green]Created example[/green] (ID: {example.id}) in dataset {dataset}"
    )
