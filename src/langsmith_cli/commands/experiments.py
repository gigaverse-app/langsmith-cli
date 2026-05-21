from datetime import timedelta
from decimal import Decimal
from typing import Any, TypedDict

import click
from langsmith_cli.utils import (
    LazyConsole,
    configure_logger_streams,
    get_or_create_client,
    is_json_context,
    json_dumps,
    not_found_as_click_exception,
)

console = LazyConsole()


class ExperimentRunStats(TypedDict, total=False):
    """Validated subset of experiment run stats used by the CLI."""

    run_count: int
    error_rate: float
    latency_p50: timedelta
    latency_p99: timedelta
    total_tokens: int
    total_cost: Decimal | float | int


class ExperimentResults(TypedDict):
    """Validated experiment result payload used by this command."""

    run_stats: ExperimentRunStats
    feedback_stats: dict[str, Any]


def _validate_experiment_results(payload: object) -> ExperimentResults:
    """Validate the SDK experiment results shape before rendering."""
    if not isinstance(payload, dict):
        raise TypeError(
            f"Expected experiment results to be a mapping, got {type(payload).__name__}"
        )
    if "run_stats" not in payload:
        raise TypeError("Experiment results missing run_stats")
    if "feedback_stats" not in payload:
        raise TypeError("Experiment results missing feedback_stats")

    run_stats = payload["run_stats"]
    feedback_stats = payload["feedback_stats"]
    if not isinstance(run_stats, dict):
        raise TypeError(
            f"Expected experiment run_stats to be a mapping, got {type(run_stats).__name__}"
        )
    if not isinstance(feedback_stats, dict):
        raise TypeError(
            f"Expected experiment feedback_stats to be a mapping, got {type(feedback_stats).__name__}"
        )

    validated_run_stats: ExperimentRunStats = {}
    for key in (
        "run_count",
        "error_rate",
        "latency_p50",
        "latency_p99",
        "total_tokens",
        "total_cost",
    ):
        if key in run_stats and run_stats[key] is not None:
            validated_run_stats[key] = run_stats[key]

    return {"run_stats": validated_run_stats, "feedback_stats": feedback_stats}


def _json_safe_run_stats(run_stats: ExperimentRunStats) -> dict[str, Any]:
    """Serialize experiment run stats to JSON-compatible values."""
    run_stats_json: dict[str, Any] = {}
    for k, v in run_stats.items():
        if isinstance(v, timedelta):
            run_stats_json[k] = v.total_seconds()
        elif isinstance(v, Decimal):
            run_stats_json[k] = float(v)
        else:
            run_stats_json[k] = v
    return run_stats_json


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
    configure_logger_streams(ctx, logger)

    logger.debug(f"Fetching experiment results: {name}")

    from rich.table import Table

    client = get_or_create_client(ctx)
    with not_found_as_click_exception("Experiment", name):
        experiment_results = _validate_experiment_results(
            client.get_experiment_results(name=name)
        )

    run_stats = experiment_results["run_stats"]
    feedback_stats = experiment_results["feedback_stats"]

    if is_json_context(ctx):
        click.echo(
            json_dumps(
                {
                    "name": name,
                    "run_stats": _json_safe_run_stats(run_stats),
                    "feedback_stats": feedback_stats,
                }
            )
        )
        return

    # Rich table: run stats
    configure_logger_streams(ctx, logger)

    stats_table = Table(title=f"Experiment: {name}")
    stats_table.add_column("Metric")
    stats_table.add_column("Value")
    if "run_count" in run_stats:
        stats_table.add_row("Run Count", str(run_stats["run_count"]))
    if "error_rate" in run_stats:
        stats_table.add_row("Error Rate", f"{run_stats['error_rate']:.1%}")
    if "latency_p50" in run_stats:
        stats_table.add_row(
            "Latency p50", f"{run_stats['latency_p50'].total_seconds():.2f}s"
        )
    if "latency_p99" in run_stats:
        stats_table.add_row(
            "Latency p99", f"{run_stats['latency_p99'].total_seconds():.2f}s"
        )
    if "total_tokens" in run_stats:
        stats_table.add_row("Total Tokens", str(run_stats["total_tokens"]))
    if "total_cost" in run_stats:
        stats_table.add_row("Total Cost", f"${float(run_stats['total_cost']):.4f}")
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
