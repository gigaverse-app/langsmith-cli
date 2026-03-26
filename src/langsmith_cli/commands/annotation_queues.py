import click
from rich.console import Console
from rich.table import Table
from langsmith.utils import LangSmithNotFoundError
from langsmith_cli.utils import (
    configure_logger_streams,
    fields_option,
    filter_fields,
    get_or_create_client,
    json_dumps,
    output_single_item,
    parse_fields_option,
    render_output,
    safe_model_dump,
)

console = Console()


@click.group(name="annotation-queues")
def annotation_queues():
    """Manage annotation queues for human review."""
    pass


@annotation_queues.command("list")
@click.option("--name", help="Filter queues by name (exact match).")
@click.option("--name-contains", help="Filter queues by name substring.")
@click.option("--limit", default=20, help="Maximum number of queues (default 20).")
@fields_option()
@click.pass_context
def list_queues(ctx, name, name_contains, limit, fields):
    """List annotation queues."""
    logger = ctx.obj["logger"]
    configure_logger_streams(ctx, logger, fields=fields)

    logger.debug(f"Listing annotation queues: name={name}, limit={limit}")

    client = get_or_create_client(ctx)

    queues = list(
        client.list_annotation_queues(
            name=name,
            name_contains=name_contains,
            limit=limit,
        )
    )

    def build_queues_table(items):
        table = Table(title="Annotation Queues")
        table.add_column("ID", style="dim")
        table.add_column("Name")
        table.add_column("Description")
        for q in items:
            table.add_row(
                str(q.id),
                q.name,
                q.description or "",
            )
        return table

    include_fields = parse_fields_option(fields)
    render_output(
        queues,
        build_queues_table,
        ctx,
        include_fields=include_fields,
        empty_message="No annotation queues found",
    )


@annotation_queues.command("get")
@click.argument("queue_id")
@fields_option()
@click.pass_context
def get_queue(ctx, queue_id, fields):
    """Fetch details of a single annotation queue by ID."""
    logger = ctx.obj["logger"]
    configure_logger_streams(ctx, logger, fields=fields)

    logger.debug(f"Fetching annotation queue: {queue_id}")

    client = get_or_create_client(ctx)

    try:
        queue = client.read_annotation_queue(queue_id)
    except LangSmithNotFoundError:
        raise click.ClickException(f"Annotation queue '{queue_id}' not found.")

    data = filter_fields(queue, fields)

    def render_queue_details(data: dict, console: object) -> None:
        from rich.console import Console as RichConsole

        assert isinstance(console, RichConsole)
        console.print(f"[bold]ID:[/bold] {data.get('id')}")
        console.print(f"[bold]Name:[/bold] {data.get('name')}")
        if data.get("description"):
            console.print(f"[bold]Description:[/bold] {data.get('description')}")

    output_single_item(ctx, data, console, render_fn=render_queue_details)


@annotation_queues.command("create")
@click.argument("name")
@click.option("--description", help="Optional description of the queue.")
@click.pass_context
def create_queue(ctx, name, description):
    """Create a new annotation queue."""
    logger = ctx.obj["logger"]
    is_machine_readable = ctx.obj.get("json")
    logger.use_stderr = is_machine_readable

    logger.debug(f"Creating annotation queue: {name}")

    client = get_or_create_client(ctx)

    queue = client.create_annotation_queue(
        name=name,
        description=description,
    )

    if ctx.obj.get("json"):
        click.echo(json_dumps(safe_model_dump(queue)))
    else:
        logger.success(f"Created annotation queue '{name}' (ID: {queue.id})")


@annotation_queues.command("update")
@click.argument("queue_id")
@click.option("--name", help="New name for the queue.")
@click.option("--description", help="New description for the queue.")
@click.pass_context
def update_queue(ctx, queue_id, name, description):
    """Update an annotation queue's name or description."""
    logger = ctx.obj["logger"]
    is_machine_readable = ctx.obj.get("json")
    logger.use_stderr = is_machine_readable

    if not any([name, description]):
        raise click.UsageError("At least one of --name or --description is required.")

    logger.debug(f"Updating annotation queue: {queue_id}")

    client = get_or_create_client(ctx)

    try:
        client.read_annotation_queue(queue_id)
    except LangSmithNotFoundError:
        raise click.ClickException(f"Annotation queue '{queue_id}' not found.")

    client.update_annotation_queue(
        queue_id,
        name=name,
        description=description,
    )

    if ctx.obj.get("json"):
        click.echo(json_dumps({"status": "success", "queue_id": queue_id}))
    else:
        logger.success(f"Updated annotation queue {queue_id}")


@annotation_queues.command("delete")
@click.argument("queue_id")
@click.option("--confirm", is_flag=True, help="Skip confirmation prompt.")
@click.pass_context
def delete_queue(ctx, queue_id, confirm):
    """Delete an annotation queue by ID."""
    logger = ctx.obj["logger"]
    is_machine_readable = ctx.obj.get("json")
    logger.use_stderr = is_machine_readable

    client = get_or_create_client(ctx)

    try:
        client.read_annotation_queue(queue_id)
    except LangSmithNotFoundError:
        raise click.ClickException(f"Annotation queue '{queue_id}' not found.")

    if not confirm:
        click.confirm(
            f"Are you sure you want to delete annotation queue {queue_id}?", abort=True
        )

    logger.debug(f"Deleting annotation queue: {queue_id}")

    client.delete_annotation_queue(queue_id)

    if ctx.obj.get("json"):
        click.echo(json_dumps({"status": "success", "deleted": queue_id}))
    else:
        logger.success(f"Deleted annotation queue {queue_id}")
