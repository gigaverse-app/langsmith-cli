"""Pricing check command for runs."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING, TypedDict
import json

import click

from langsmith_cli.commands.runs._group import runs
from langsmith_cli.utils import (
    add_project_filter_options,
    add_time_filter_options,
    build_tag_fql_filters,
    build_time_fql_filters,
    collect_runs_streaming,
    combine_fql_filters,
    configure_logger_streams,
    filter_runs_by_tags,
    get_full_model_name,
    get_or_create_client,
    is_json_context,
    json_dumps,
    resolve_project_filters,
)

if TYPE_CHECKING:
    from langsmith.schemas import Run
    from langsmith_cli.cli_logging import CLILogger

# Backward-compat alias for tests that import this name from pricing_cmd.
# The real implementation lives in run_helpers.get_full_model_name so
# pricing and usage stay in sync.
_get_model_name = get_full_model_name


class OpenRouterAPIModelPricing(TypedDict):
    """Pricing payload for a single OpenRouter model."""

    prompt: str
    completion: str


class OpenRouterAPIModel(TypedDict):
    """Validated subset of an OpenRouter model response."""

    id: str
    pricing: OpenRouterAPIModelPricing


class OpenRouterMatchedPrice(TypedDict):
    """Pricing match returned for a LangSmith model name."""

    openrouter_id: str
    input_per_million: float
    output_per_million: float


@runs.command("pricing")
@add_project_filter_options
@add_time_filter_options
@click.option(
    "--tag",
    multiple=True,
    help="Filter by tag (can specify multiple times for AND logic).",
)
@click.option(
    "--from-cache",
    is_flag=True,
    help="Analyze cached runs instead of fetching from API.",
)
@click.option(
    "--lookup/--no-lookup",
    default=False,
    help="Look up missing prices from OpenRouter API (default: disabled).",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["table", "json", "yaml"]),
    help="Output format. Use 'yaml' to generate a pricing file for --apply-pricing.",
)
@click.pass_context
def pricing_check(
    ctx: click.Context,
    project: str | None,
    project_id: str | None,
    project_name: str | None,
    project_name_exact: str | None,
    project_name_pattern: str | None,
    project_name_regex: str | None,
    since: str | None,
    before: str | None,
    last: str | None,
    tag: tuple[str, ...],
    from_cache: bool,
    lookup: bool,
    output_format: str | None,
) -> None:
    """Check model pricing coverage and look up missing prices.

    Scans runs to find models with and without cost data, then optionally
    looks up missing prices from the OpenRouter API.

    Models with $0.00 cost despite having tokens are flagged as missing pricing.
    The lookup provides input/output prices per million tokens that can be
    configured in LangSmith Settings > Model Pricing.

    \b
    Examples:
      # Check pricing for all prd/* projects from cache
      langsmith-cli runs pricing --project-name-pattern "prd/*" --from-cache
      # Check without OpenRouter lookup
      langsmith-cli runs pricing \\
        --project-name-pattern "prd/*" \\
        --from-cache \\
        --no-lookup
      # Check recent runs from API
      langsmith-cli runs pricing --project my-project --last 7d
      # JSON output for automation
      langsmith-cli --json runs pricing \\
        --project-name-pattern "prd/*" \\
        --from-cache
    """
    from collections import defaultdict

    logger = ctx.obj["logger"]
    is_json = is_json_context(ctx) or output_format == "json"
    configure_logger_streams(ctx, logger, output_format=output_format)

    # Fetch runs
    all_runs: list[Run] = []
    if from_cache:
        from langsmith_cli.cache import load_runs_from_cache
        from langsmith_cli.filters import parse_time_filter

        client = get_or_create_client(ctx)
        pq = resolve_project_filters(
            client,
            project=project,
            project_id=project_id,
            name=project_name,
            name_exact=project_name_exact,
            name_pattern=project_name_pattern,
            name_regex=project_name_regex,
        )
        project_names = pq.names if not pq.use_id else [f"id:{pq.project_id}"]
        since_dt, until_dt = parse_time_filter(since=since, last=last, before=before)

        logger.info(f"Scanning cached runs from {len(project_names)} project(s)...")
        result = load_runs_from_cache(project_names, since=since_dt, until=until_dt)
        all_runs = [r for r in result.items if r.run_type == "llm"]
        # Apply tag filters client-side
        if tag:
            all_runs = filter_runs_by_tags(all_runs, tag)
    else:
        client = get_or_create_client(ctx)
        pq = resolve_project_filters(
            client,
            project=project,
            project_id=project_id,
            name=project_name,
            name_exact=project_name_exact,
            name_pattern=project_name_pattern,
            name_regex=project_name_regex,
        )

        time_filters = build_time_fql_filters(since=since, last=last, before=before)
        base_filters = time_filters.copy()
        base_filters.append('eq(run_type, "llm")')
        # Tag filtering (AND logic - all tags must be present)
        if tag:
            base_filters.extend(build_tag_fql_filters(tag))
        combined_filter = combine_fql_filters(base_filters)

        logger.info(f"Scanning LLM runs from {len(pq.names)} project(s)...")
        stream_result = collect_runs_streaming(
            client,
            pq,
            filter=combined_filter,
            select=[
                "total_tokens",
                "prompt_tokens",
                "completion_tokens",
                "total_cost",
                "prompt_cost",
                "completion_cost",
                "extra",
                "run_type",
            ],
        )
        all_runs = stream_result.items
        for source_name, err in stream_result.failed_sources:
            logger.warning(f"Failed to fetch pricing runs from {source_name}: {err}")

    # Aggregate by model
    model_stats: dict[str, dict[str, int | float]] = defaultdict(
        lambda: {
            "runs": 0,
            "tokens": 0,
            "cost": 0.0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "prompt_cost": 0.0,
            "completion_cost": 0.0,
        }
    )
    for r in all_runs:
        model = get_full_model_name(r)
        if model == "unknown":
            continue
        model_stats[model]["runs"] += 1
        model_stats[model]["tokens"] += r.total_tokens or 0
        model_stats[model]["cost"] += float(r.total_cost or 0.0)
        model_stats[model]["prompt_tokens"] += r.prompt_tokens or 0
        model_stats[model]["completion_tokens"] += r.completion_tokens or 0
        model_stats[model]["prompt_cost"] += float(r.prompt_cost or 0.0)
        model_stats[model]["completion_cost"] += float(r.completion_cost or 0.0)

    if not model_stats:
        logger.warning("No LLM runs with model info found.")
        if is_json:
            click.echo(json_dumps({"models": []}))
        return

    # Identify missing pricing (has tokens but no cost)
    missing_models = [
        name
        for name, stats in model_stats.items()
        if stats["tokens"] > 0 and stats["cost"] == 0.0
    ]

    # Look up pricing from OpenRouter
    openrouter_prices: dict[str, OpenRouterMatchedPrice] = {}
    if lookup and missing_models:
        openrouter_prices = _fetch_openrouter_pricing(missing_models, logger)

    # YAML output: generate a pricing file for use with --apply-pricing
    if output_format == "yaml":
        click.echo(_render_pricing_yaml(model_stats, openrouter_prices))
        return

    if is_json:
        click.echo(
            json_dumps(
                {"models": _build_pricing_models_list(model_stats, openrouter_prices)}
            )
        )
    else:
        _render_pricing_tables(model_stats, missing_models, openrouter_prices)


def _build_pricing_models_list(
    model_stats: dict[str, dict[str, int | float]],
    openrouter_prices: dict[str, OpenRouterMatchedPrice],
) -> list[dict[str, Any]]:
    """Convert aggregated stats into the JSON-output row list (pure data)."""
    models_data: list[dict[str, Any]] = []
    for name, stats in sorted(model_stats.items(), key=lambda x: -x[1]["tokens"]):
        entry: dict[str, Any] = {
            "model": name,
            "runs": stats["runs"],
            "total_tokens": stats["tokens"],
            "total_cost": round(float(stats["cost"]), 6),
            "has_pricing": stats["cost"] > 0 or stats["tokens"] == 0,
        }
        if name in openrouter_prices:
            entry["openrouter_pricing"] = openrouter_prices[name]
        models_data.append(entry)
    return models_data


def _render_pricing_yaml(
    model_stats: dict[str, dict[str, int | float]],
    openrouter_prices: dict[str, OpenRouterMatchedPrice],
) -> str:
    """Render the pricing YAML used as input to ``--apply-pricing``.

    Pure function so callers (and tests) don't need a Click context.
    Models with cost data get $/1M derived from their actual token totals;
    everything else falls back to OpenRouter, then zero.
    """
    import yaml

    pricing_data: dict[str, dict[str, float]] = {}
    for name in sorted(model_stats.keys()):
        stats = model_stats[name]
        has_pricing = stats["cost"] > 0 or stats["tokens"] == 0
        if has_pricing:
            pt = int(stats["prompt_tokens"])
            ct = int(stats["completion_tokens"])
            pc = float(stats["prompt_cost"])
            cc = float(stats["completion_cost"])
            pricing_data[name] = {
                "input_per_million": round(pc / pt * 1_000_000, 4) if pt > 0 else 0.0,
                "output_per_million": round(cc / ct * 1_000_000, 4) if ct > 0 else 0.0,
            }
        elif name in openrouter_prices:
            p = openrouter_prices[name]
            pricing_data[name] = {
                "input_per_million": p["input_per_million"],
                "output_per_million": p["output_per_million"],
            }
        else:
            pricing_data[name] = {
                "input_per_million": 0.0,
                "output_per_million": 0.0,
            }
    return yaml.dump(pricing_data, default_flow_style=False, sort_keys=True)


def _render_pricing_tables(
    model_stats: dict[str, dict[str, int | float]],
    missing_models: list[str],
    openrouter_prices: dict[str, OpenRouterMatchedPrice],
) -> None:
    """Render the human-mode pricing tables to stdout via Rich Console."""
    from rich.console import Console
    from rich.table import Table

    table = Table(title="Model Pricing Coverage")
    table.add_column("Model", style="cyan")
    table.add_column("Runs", justify="right")
    table.add_column("Tokens", justify="right")
    table.add_column("Cost", justify="right")
    table.add_column("Status", justify="center")

    for name, stats in sorted(model_stats.items(), key=lambda x: -x[1]["tokens"]):
        has_pricing = stats["cost"] > 0 or stats["tokens"] == 0
        status = "[green]OK[/green]" if has_pricing else "[red]MISSING[/red]"
        table.add_row(
            name,
            f"{stats['runs']:,}",
            f"{stats['tokens']:,}",
            f"${stats['cost']:.4f}",
            status,
        )
    console = Console()
    console.print(table)

    if not missing_models:
        return

    console.print()
    if openrouter_prices:
        price_table = Table(title="OpenRouter Pricing (per million tokens)")
        price_table.add_column("Model", style="cyan")
        price_table.add_column("OpenRouter ID", style="dim")
        price_table.add_column("Input $/M", justify="right")
        price_table.add_column("Output $/M", justify="right")

        for model_name in missing_models:
            if model_name in openrouter_prices:
                p = openrouter_prices[model_name]
                price_table.add_row(
                    model_name,
                    p["openrouter_id"],
                    f"${p['input_per_million']:.4f}",
                    f"${p['output_per_million']:.4f}",
                )
            else:
                price_table.add_row(model_name, "", "[dim]not found[/dim]", "")
        console.print(price_table)

    console.print()
    console.print(
        "[yellow]To add missing pricing:[/yellow]\n"
        "  1. Open LangSmith Settings > Model Pricing\n"
        "  2. Click '+ Model' for each missing model\n"
        "  3. Set the match pattern to the model name shown above\n"
        "  4. Enter input/output prices per million tokens\n"
        "  [dim]Note: Pricing updates are NOT retroactive.[/dim]"
    )


def _fetch_openrouter_pricing(
    model_names: list[str],
    logger: CLILogger,
) -> dict[str, OpenRouterMatchedPrice]:
    """Fetch pricing from OpenRouter API for given model names.

    Returns dict mapping our model name -> pricing info.
    """
    import urllib.request
    import urllib.error

    # Map our model names to likely OpenRouter IDs
    name_mappings: dict[str, list[str]] = {}
    for name in model_names:
        candidates = [name]
        # Common transformations
        if "/" not in name:
            # Try adding common prefixes
            if name.startswith("llama"):
                candidates.append(f"meta-llama/{name}")
                candidates.append(f"meta-llama/{name}-instruct")
            elif name.startswith("qwen"):
                candidates.append(f"qwen/{name}")
            elif name.startswith("gpt"):
                candidates.append(f"openai/{name}")
        # Handle provider-specific suffixes
        if "-versatile" in name:
            base = name.replace("-versatile", "")
            candidates.append(f"meta-llama/{base}-instruct")
        name_mappings[name] = candidates

    # Fetch OpenRouter model list
    try:
        logger.info("Fetching pricing from OpenRouter API...")
        req = urllib.request.Request(
            "https://openrouter.ai/api/v1/models",
            headers={"User-Agent": "langsmith-cli"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
        logger.warning(f"Failed to fetch OpenRouter pricing: {e}")
        return {}

    try:
        models = _validate_openrouter_models_response(data)
    except ValueError as e:
        logger.warning(f"OpenRouter pricing response had unexpected shape: {e}")
        return {}

    # Build lookup from OpenRouter data
    or_models: dict[str, OpenRouterMatchedPrice] = {}
    for model in models:
        model_id = model["id"]
        pricing = model["pricing"]
        or_models[model_id.lower()] = {
            "openrouter_id": model_id,
            "input_per_million": round(float(pricing["prompt"]) * 1_000_000, 4),
            "output_per_million": round(float(pricing["completion"]) * 1_000_000, 4),
        }

    # Match our models to OpenRouter
    result: dict[str, OpenRouterMatchedPrice] = {}
    for our_name, candidates in name_mappings.items():
        for candidate in candidates:
            key = candidate.lower()
            if key in or_models:
                result[our_name] = or_models[key]
                break

    return result


def _validate_openrouter_models_response(raw: Any) -> list[OpenRouterAPIModel]:
    """Validate the OpenRouter models response at the network boundary."""
    if not isinstance(raw, dict):
        raise ValueError(f"expected object, got {type(raw).__name__}")
    if "data" not in raw:
        raise ValueError("missing required field 'data'")
    data = raw["data"]
    if not isinstance(data, list):
        raise ValueError("field 'data' must be a list")

    models: list[OpenRouterAPIModel] = []
    for index, item in enumerate(data):
        if not isinstance(item, dict):
            raise ValueError(f"data[{index}] must be an object")
        if "id" not in item:
            raise ValueError(f"data[{index}] missing required field 'id'")
        model_id = item["id"]
        if not isinstance(model_id, str):
            raise ValueError(f"data[{index}].id must be a string")
        if "pricing" not in item:
            raise ValueError(f"data[{index}] missing required field 'pricing'")
        pricing = item["pricing"]
        if not isinstance(pricing, dict):
            raise ValueError(f"data[{index}].pricing must be an object")
        if "prompt" not in pricing:
            raise ValueError(f"data[{index}].pricing missing required field 'prompt'")
        if "completion" not in pricing:
            raise ValueError(
                f"data[{index}].pricing missing required field 'completion'"
            )
        prompt = pricing["prompt"]
        completion = pricing["completion"]
        if not isinstance(prompt, str):
            raise ValueError(f"data[{index}].pricing.prompt must be a string")
        if not isinstance(completion, str):
            raise ValueError(f"data[{index}].pricing.completion must be a string")
        try:
            float(prompt)
            float(completion)
        except ValueError as e:
            raise ValueError(
                f"data[{index}].pricing must contain numeric strings"
            ) from e
        models.append(
            OpenRouterAPIModel(
                id=model_id,
                pricing=OpenRouterAPIModelPricing(
                    prompt=prompt,
                    completion=completion,
                ),
            )
        )
    return models
