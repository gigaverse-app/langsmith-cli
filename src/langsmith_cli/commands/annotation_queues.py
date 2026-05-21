import click
from langsmith_cli.utils import (
    ConsoleProtocol,
    LazyConsole,
    configure_logger_streams,
    confirm_option,
    count_option,
    emit_action_result,
    fields_option,
    filter_fields,
    get_or_create_client,
    not_found_as_click_exception,
    output_option,
    output_single_item,
    parse_fields_option,
    render_detail_fields,
    render_output,
    require_confirmation,
)

console = LazyConsole()


@click.group(name="annotation-queues")
def annotation_queues():
    """Manage annotation queues for human review."""
    pass


@annotation_queues.command("list")
@click.option("--name", help="Filter queues by name (exact match).")
@click.option("--name-contains", help="Filter queues by name substring.")
@click.option("--limit", default=20, help="Maximum number of queues (default 20).")
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["table", "json", "csv", "yaml"]),
    help="Output format (default: table, or json if --json flag used).",
)
@fields_option()
@count_option()
@output_option()
@click.pass_context
def list_queues(ctx, name, name_contains, limit, output_format, fields, count, output):
    """List annotation queues."""
    logger = ctx.obj["logger"]
    configure_logger_streams(
        ctx,
        logger,
        output=output,
        output_format=output_format,
        count=count,
        fields=fields,
    )

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
        from rich.table import Table

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
        output_format=output_format,
        count_flag=count,
        output_path=output,
    )


@annotation_queues.command("get")
@click.argument("queue_id")
@fields_option()
@output_option()
@click.pass_context
def get_queue(ctx, queue_id, fields, output):
    """Fetch details of a single annotation queue by ID."""
    logger = ctx.obj["logger"]
    configure_logger_streams(ctx, logger, output=output, fields=fields)

    logger.debug(f"Fetching annotation queue: {queue_id}")

    client = get_or_create_client(ctx)
    with not_found_as_click_exception("Annotation queue", queue_id):
        queue = client.read_annotation_queue(queue_id)

    data = filter_fields(queue, fields)

    def render_queue_details(data: dict, console: ConsoleProtocol) -> None:
        render_detail_fields(
            data,
            console,
            [
                ("id", "ID"),
                ("name", "Name"),
                ("description", "Description"),
            ],
        )

    output_single_item(
        ctx, data, console, output=output, render_fn=render_queue_details
    )


@annotation_queues.command("create")
@click.argument("name")
@click.option("--description", help="Optional description of the queue.")
@click.pass_context
def create_queue(ctx, name, description):
    """Create a new annotation queue."""
    logger = ctx.obj["logger"]
    configure_logger_streams(ctx, logger)

    logger.debug(f"Creating annotation queue: {name}")

    client = get_or_create_client(ctx)

    queue = client.create_annotation_queue(
        name=name,
        description=description,
    )

    emit_action_result(
        ctx,
        logger,
        model=queue,
        success_message=f"Created annotation queue '{name}' (ID: {queue.id})",
    )


@annotation_queues.command("update")
@click.argument("queue_id")
@click.option("--name", help="New name for the queue.")
@click.option("--description", help="New description for the queue.")
@click.pass_context
def update_queue(ctx, queue_id, name, description):
    """Update an annotation queue's name or description."""
    logger = ctx.obj["logger"]
    configure_logger_streams(ctx, logger)

    if not any([name, description]):
        raise click.UsageError("At least one of --name or --description is required.")

    logger.debug(f"Updating annotation queue: {queue_id}")

    client = get_or_create_client(ctx)
    with not_found_as_click_exception("Annotation queue", queue_id):
        client.read_annotation_queue(queue_id)

    client.update_annotation_queue(
        queue_id,
        name=name,
        description=description,
    )

    emit_action_result(
        ctx,
        logger,
        payload={"status": "success", "queue_id": queue_id},
        success_message=f"Updated annotation queue {queue_id}",
    )


@annotation_queues.command("delete")
@click.argument("queue_id")
@confirm_option()
@click.pass_context
def delete_queue(ctx, queue_id, confirm):
    """Delete an annotation queue by ID."""
    logger = ctx.obj["logger"]
    configure_logger_streams(ctx, logger)

    client = get_or_create_client(ctx)
    with not_found_as_click_exception("Annotation queue", queue_id):
        client.read_annotation_queue(queue_id)

    require_confirmation(
        confirm, f"Are you sure you want to delete annotation queue {queue_id}?"
    )

    logger.debug(f"Deleting annotation queue: {queue_id}")

    client.delete_annotation_queue(queue_id)

    emit_action_result(
        ctx,
        logger,
        payload={"status": "success", "deleted": queue_id},
        success_message=f"Deleted annotation queue {queue_id}",
    )
