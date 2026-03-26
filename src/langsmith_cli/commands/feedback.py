import click
from rich.console import Console
from rich.table import Table
from langsmith_cli.utils import (
    configure_logger_streams,
    fields_option,
    get_or_create_client,
    json_dumps,
    output_single_item,
    parse_fields_option,
    render_output,
    safe_model_dump,
)

console = Console()


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
@fields_option()
@click.pass_context
def list_feedback(ctx, run_id, feedback_key, feedback_source_type, limit, fields):
    """List feedback items, optionally filtered by run, key, or source."""
    logger = ctx.obj["logger"]
    configure_logger_streams(ctx, logger, fields=fields)

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
    )


@feedback.command("get")
@click.argument("feedback_id")
@fields_option()
@click.pass_context
def get_feedback(ctx, feedback_id, fields):
    """Fetch a single feedback item by ID."""
    logger = ctx.obj["logger"]
    configure_logger_streams(ctx, logger, fields=fields)

    logger.debug(f"Fetching feedback: {feedback_id}")

    client = get_or_create_client(ctx)
    fb = client.read_feedback(feedback_id)

    from langsmith_cli.utils import filter_fields

    data = filter_fields(fb, fields)

    def render_feedback_details(data: dict, console: object) -> None:
        from rich.console import Console as RichConsole

        assert isinstance(console, RichConsole)
        console.print(f"[bold]ID:[/bold] {data.get('id')}")
        console.print(f"[bold]Key:[/bold] {data.get('key')}")
        console.print(f"[bold]Score:[/bold] {data.get('score')}")
        if data.get("comment"):
            console.print(f"[bold]Comment:[/bold] {data.get('comment')}")
        if data.get("run_id"):
            console.print(f"[bold]Run ID:[/bold] {data.get('run_id')}")

    output_single_item(ctx, data, console, render_fn=render_feedback_details)


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
    is_machine_readable = ctx.obj.get("json")
    logger.use_stderr = is_machine_readable

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

    if ctx.obj.get("json"):
        click.echo(json_dumps(safe_model_dump(fb)))
    else:
        logger.success(f"Created feedback (ID: {fb.id}) for run {run_id}")


@feedback.command("delete")
@click.argument("feedback_id")
@click.option("--confirm", is_flag=True, help="Skip confirmation prompt.")
@click.pass_context
def delete_feedback_cmd(ctx, feedback_id, confirm):
    """Delete a feedback item by ID."""
    logger = ctx.obj["logger"]
    is_machine_readable = ctx.obj.get("json")
    logger.use_stderr = is_machine_readable

    if not confirm:
        click.confirm(
            f"Are you sure you want to delete feedback {feedback_id}?", abort=True
        )

    logger.debug(f"Deleting feedback: {feedback_id}")

    client = get_or_create_client(ctx)

    from langsmith.utils import LangSmithNotFoundError

    try:
        client.delete_feedback(feedback_id)
    except LangSmithNotFoundError:
        raise click.ClickException(f"Feedback '{feedback_id}' not found.")

    if ctx.obj.get("json"):
        click.echo(json_dumps({"status": "success", "deleted": feedback_id}))
    else:
        logger.success(f"Deleted feedback {feedback_id}")
