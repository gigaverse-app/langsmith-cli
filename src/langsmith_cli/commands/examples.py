import click
from rich.console import Console
from rich.table import Table
import langsmith
import json
from langsmith_cli.utils import (
    print_empty_result_message,
    parse_json_string,
    parse_comma_separated_list,
)

console = Console()


@click.group()
def examples():
    """Manage dataset examples."""
    pass


@examples.command("list")
@click.option("--dataset", help="Dataset ID or Name.")
@click.option("--example-ids", help="Specific example IDs (comma-separated).")
@click.option("--limit", default=20, help="Limit number of examples (default 20).")
@click.option("--offset", default=0, help="Number of examples to skip (pagination).")
@click.option("--filter", "filter_", help="LangSmith query filter.")
@click.option("--metadata", help="Filter by metadata (JSON string).")
@click.option("--splits", help="Filter by dataset splits (comma-separated).")
@click.option("--inline-s3-urls", type=bool, help="Include S3 URLs inline.")
@click.option("--include-attachments", type=bool, help="Include attachments.")
@click.option("--as-of", help="Dataset version tag or ISO timestamp.")
@click.pass_context
def list_examples(
    ctx,
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
):
    """List examples for a dataset."""
    client = langsmith.Client()

    # Parse comma-separated values
    example_ids_list = parse_comma_separated_list(example_ids)
    splits_list = parse_comma_separated_list(splits)
    metadata_dict = parse_json_string(metadata, "metadata")

    # list_examples takes dataset_name and limit
    examples_gen = client.list_examples(
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
        print_empty_result_message(console, "examples")
    else:
        console.print(table)


@examples.command("get")
@click.argument("example_id")
@click.option("--as-of", help="Dataset version tag or ISO timestamp.")
@click.pass_context
def get_example(ctx, example_id, as_of):
    """Fetch details of a single example."""
    client = langsmith.Client()
    example = client.read_example(example_id, as_of=as_of)

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

    input_dict = parse_json_string(inputs, "inputs")
    output_dict = parse_json_string(outputs, "outputs")
    metadata_dict = parse_json_string(metadata, "metadata")

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
