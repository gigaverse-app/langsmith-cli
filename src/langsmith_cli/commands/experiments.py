from datetime import timedelta
from decimal import Decimal

import click
from rich.console import Console
from rich.table import Table
from langsmith_cli.utils import (
    configure_logger_streams,
    get_or_create_client,
    json_dumps,
)

console = Console()


@click.group()
def experiments():
    """View experiment results and statistics."""
    pass


@experiments.command("results")
@click.argument("name")
@click.pass_context
def results(ctx, name):
    """Show run stats and feedback scores for an experiment.

    NAME is the experiment (project) name in LangSmith.
    """
    logger = ctx.obj["logger"]
    is_machine_readable = ctx.obj.get("json")
    logger.use_stderr = is_machine_readable

    logger.debug(f"Fetching experiment results: {name}")

    client = get_or_create_client(ctx)

    from langsmith.utils import LangSmithNotFoundError

    try:
        experiment_results = client.get_experiment_results(name=name)
    except LangSmithNotFoundError:
        raise click.ClickException(f"Experiment '{name}' not found.")

    run_stats = experiment_results.get("run_stats", {})
    feedback_stats = experiment_results.get("feedback_stats", {})

    if ctx.obj.get("json"):
        # Serialize timedeltas and Decimals to JSON-safe values
        run_stats_json: dict = {}
        for k, v in run_stats.items():
            if isinstance(v, timedelta):
                run_stats_json[k] = v.total_seconds()
            elif v is None:
                run_stats_json[k] = None
            elif isinstance(v, Decimal):
                run_stats_json[k] = float(v)
            else:
                run_stats_json[k] = v
        click.echo(
            json_dumps(
                {
                    "name": name,
                    "run_stats": run_stats_json,
                    "feedback_stats": feedback_stats,
                }
            )
        )
        return

    # Rich table: run stats
    configure_logger_streams(ctx, logger)
    run_count = run_stats.get("run_count")
    error_rate = run_stats.get("error_rate")

    stats_table = Table(title=f"Experiment: {name}")
    stats_table.add_column("Metric")
    stats_table.add_column("Value")
    if run_count is not None:
        stats_table.add_row("Run Count", str(run_count))
    if error_rate is not None:
        stats_table.add_row("Error Rate", f"{error_rate:.1%}")
    latency_p50 = run_stats.get("latency_p50")
    if latency_p50 is not None:
        stats_table.add_row("Latency p50", f"{latency_p50.total_seconds():.2f}s")
    latency_p99 = run_stats.get("latency_p99")
    if latency_p99 is not None:
        stats_table.add_row("Latency p99", f"{latency_p99.total_seconds():.2f}s")
    total_tokens = run_stats.get("total_tokens")
    if total_tokens is not None:
        stats_table.add_row("Total Tokens", str(total_tokens))
    total_cost = run_stats.get("total_cost")
    if total_cost is not None:
        stats_table.add_row("Total Cost", f"${float(total_cost):.4f}")
    console.print(stats_table)

    # Feedback scores table
    if feedback_stats:
        fb_table = Table(title="Feedback Scores")
        fb_table.add_column("Key")
        fb_table.add_column("Average Score")
        for key, score in feedback_stats.items():
            fb_table.add_row(
                key, f"{score:.3f}" if isinstance(score, float) else str(score)
            )
        console.print(fb_table)
