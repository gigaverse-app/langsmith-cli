import click
from langsmith_cli.utils import (
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
    render_output,
    require_confirmation,
)

console = LazyConsole()


@click.group()
def feedback():
    """Manage run feedback (scores and comments)."""
    pass


@feedback.command("list")
@click.option("--run-id", help="Filter feedback by run ID.")
@click.option(
    "--key", "feedback_key", help="Filter by feedback key (e.g. correctness)."
)
@click.option(
    "--source",
    "feedback_source_type",
    type=click.Choice(["api", "model"]),
    help="Filter by feedback source type.",
)
@click.option(
    "--limit", default=20, help="Maximum number of feedback items (default 20)."
)
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
def list_feedback(
    ctx,
    run_id,
    feedback_key,
    feedback_source_type,
    limit,
    output_format,
    fields,
    count,
    output,
):
    """List feedback items, optionally filtered by run, key, or source."""
    logger = ctx.obj["logger"]
    configure_logger_streams(
        ctx,
        logger,
        output=output,
        output_format=output_format,
        count=count,
        fields=fields,
    )

    logger.debug(
        f"Listing feedback: run_id={run_id}, key={feedback_key}, limit={limit}"
    )

    client = get_or_create_client(ctx)

    run_ids = [run_id] if run_id else None
    feedback_items = list(
        client.list_feedback(
            run_ids=run_ids,
            feedback_key=[feedback_key] if feedback_key else None,
            feedback_source_type=feedback_source_type,
            limit=limit,
        )
    )

    def build_feedback_table(items):
        from rich.table import Table

        table = Table(title="Feedback")
        table.add_column("ID", style="dim")
        table.add_column("Key")
        table.add_column("Score")
        table.add_column("Comment")
        table.add_column("Run ID", style="dim")
        for fb in items:
            table.add_row(
                str(fb.id),
                fb.key,
                str(fb.score) if fb.score is not None else "",
                fb.comment or "",
                str(fb.run_id) if fb.run_id else "",
            )
        return table

    include_fields = parse_fields_option(fields)
    render_output(
        feedback_items,
        build_feedback_table,
        ctx,
        include_fields=include_fields,
        empty_message="No feedback found",
        output_format=output_format,
        count_flag=count,
        output_path=output,
    )


@feedback.command("get")
@click.argument("feedback_id")
@fields_option()
@output_option()
@click.pass_context
def get_feedback(ctx, feedback_id, fields, output):
    """Fetch a single feedback item by ID."""
    logger = ctx.obj["logger"]
    configure_logger_streams(ctx, logger, output=output, fields=fields)

    logger.debug(f"Fetching feedback: {feedback_id}")

    client = get_or_create_client(ctx)
    with not_found_as_click_exception("Feedback", feedback_id):
        fb = client.read_feedback(feedback_id)

    data = filter_fields(fb, fields)

    def render_feedback_details(data: dict, console: object) -> None:
        console.print(f"[bold]ID:[/bold] {data.get('id')}")
        console.print(f"[bold]Key:[/bold] {data.get('key')}")
        console.print(f"[bold]Score:[/bold] {data.get('score')}")
        if data.get("comment"):
            console.print(f"[bold]Comment:[/bold] {data.get('comment')}")
        if data.get("run_id"):
            console.print(f"[bold]Run ID:[/bold] {data.get('run_id')}")

    output_single_item(
        ctx, data, console, output=output, render_fn=render_feedback_details
    )


@feedback.command("create")
@click.argument("run_id")
@click.option(
    "--key", required=True, help="Feedback key (e.g. correctness, helpfulness)."
)
@click.option(
    "--score",
    type=float,
    help="Numeric score (0.0–1.0 convention, but any float is accepted).",
)
@click.option("--value", help="String value for categorical feedback.")
@click.option("--comment", help="Optional text comment.")
@click.option(
    "--source",
    "feedback_source_type",
    type=click.Choice(["api", "model"]),
    default="api",
    help="Feedback source type (default: api).",
)
@click.pass_context
def create_feedback_cmd(ctx, run_id, key, score, value, comment, feedback_source_type):
    """Create a feedback entry for a run."""
    logger = ctx.obj["logger"]
    configure_logger_streams(ctx, logger)

    logger.debug(f"Creating feedback for run {run_id}: key={key}, score={score}")

    client = get_or_create_client(ctx)

    fb = client.create_feedback(
        run_id=run_id,
        key=key,
        score=score,
        value=value,
        comment=comment,
        feedback_source_type=feedback_source_type,
    )

    emit_action_result(
        ctx,
        logger,
        model=fb,
        success_message=f"Created feedback (ID: {fb.id}) for run {run_id}",
    )


@feedback.command("delete")
@click.argument("feedback_id")
@confirm_option()
@click.pass_context
def delete_feedback_cmd(ctx, feedback_id, confirm):
    """Delete a feedback item by ID."""
    logger = ctx.obj["logger"]
    configure_logger_streams(ctx, logger)

    require_confirmation(
        confirm, f"Are you sure you want to delete feedback {feedback_id}?"
    )

    logger.debug(f"Deleting feedback: {feedback_id}")

    client = get_or_create_client(ctx)
    with not_found_as_click_exception("Feedback", feedback_id):
        client.delete_feedback(feedback_id)

    emit_action_result(
        ctx,
        logger,
        payload={"status": "success", "deleted": feedback_id},
        success_message=f"Deleted feedback {feedback_id}",
    )
