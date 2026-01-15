import click
from rich.console import Console
from rich.table import Table
from langsmith_cli.utils import (
    apply_exclude_filter,
    count_option,
    exclude_option,
    fields_option,
    filter_fields,
    get_or_create_client,
    output_option,
    parse_comma_separated_list,
    parse_json_string,
    render_output,
    safe_model_dump,
    write_output_to_file,
    json_dumps,
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
@exclude_option()
@fields_option()
@count_option()
@output_option()
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
    exclude,
    fields,
    count,
    output,
):
    """List examples for a dataset."""
    logger = ctx.obj["logger"]
    is_machine_readable = ctx.obj.get("json") or bool(output) or bool(fields)
    logger.use_stderr = is_machine_readable

    logger.debug(
        f"Listing examples: dataset={dataset}, limit={limit}, "
        f"offset={offset}, filter={filter_}"
    )

    client = get_or_create_client(ctx)

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

    # Client-side exclude filtering (filter by ID string representation)
    examples_list = apply_exclude_filter(examples_list, exclude, lambda e: str(e.id))

    # Handle file output - short circuit if writing to file
    if output:
        data = filter_fields(examples_list, fields)
        write_output_to_file(data, output, console, format_type="jsonl")
        return

    # Define table builder function
    def build_examples_table(examples):
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

    # Determine which fields to include
    if fields:
        include_fields = {f.strip() for f in fields.split(",") if f.strip()}
    else:
        # Default fields for output
        include_fields = None

    # Unified output rendering
    render_output(
        examples_list,
        build_examples_table,
        ctx,
        include_fields=include_fields,
        empty_message="No examples found",
        count_flag=count,
    )


@examples.command("get")
@click.argument("example_id")
@click.option("--as-of", help="Dataset version tag or ISO timestamp.")
@fields_option()
@click.pass_context
def get_example(ctx, example_id, as_of, fields):
    """Fetch details of a single example."""
    logger = ctx.obj["logger"]
    is_machine_readable = ctx.obj.get("json") or bool(fields)
    logger.use_stderr = is_machine_readable

    logger.debug(f"Fetching example: example_id={example_id}, as_of={as_of}")

    client = get_or_create_client(ctx)
    example = client.read_example(example_id, as_of=as_of)

    # Use shared field filtering utility
    data = filter_fields(example, fields)

    if ctx.obj.get("json"):
        click.echo(json_dumps(data))
        return

    from rich.syntax import Syntax

    console.print(f"[bold]Example ID:[/bold] {example.id}")
    console.print("\n[bold]Inputs:[/bold]")
    console.print(Syntax(json_dumps(example.inputs, indent=2), "json"))
    console.print("\n[bold]Outputs:[/bold]")
    console.print(Syntax(json_dumps(example.outputs, indent=2), "json"))


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
    is_machine_readable = ctx.obj.get("json")
    logger.use_stderr = is_machine_readable

    logger.debug(f"Creating example in dataset: {dataset}")

    client = get_or_create_client(ctx)

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
        data = safe_model_dump(example)
        click.echo(json_dumps(data))
        return

    logger.success(f"Created example (ID: {example.id}) in dataset {dataset}")
