"""Usage analysis command for runs."""

from typing import Any

import click
from langsmith.schemas import Run
from pydantic import BaseModel
from rich.table import Table

from langsmith_cli.commands.runs._group import console, runs
from langsmith_cli.time_parsing import ensure_aware_datetime
from langsmith_cli.output import json_dumps
from langsmith_cli.utils import (
    add_grep_options,
    add_metadata_filter_options,
    add_project_filter_options,
    add_time_filter_options,
    apply_grep_filter,
    build_metadata_fql_filters,
    build_tag_fql_filters,
    build_time_fql_filters,
    combine_fql_filters,
    determine_output_format,
    filter_runs_by_tags,
    get_or_create_client,
    output_formatted_data,
    output_option,
    resolve_project_filters,
    write_output_to_file,
)


class UsageBucket(BaseModel):
    """Accumulated token/cost metrics for a single time+group+breakdown bucket."""

    total_tokens: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_cost: float = 0.0
    prompt_cost: float = 0.0
    completion_cost: float = 0.0
    run_count: int = 0


def _get_model_name(run: Run) -> str:
    """Extract model name from a run, checking multiple locations."""
    extra = run.extra or {}
    metadata = extra.get("metadata", {}) or {}
    model = metadata.get("ls_model_name")
    if model:
        return str(model)
    invocation = extra.get("invocation_params", {}) or {}
    model = invocation.get("model") or invocation.get("model_name")
    if model:
        return str(model)
    return "unknown"


def _get_project_name(run: Run) -> str:
    """Extract project name from a run, handling missing attribute when using select."""
    name: str | None = getattr(run, "session_name", None)
    return name if name else "unknown"


def _get_gateway(run: Run) -> str:
    """Extract gateway/API provider from ls_provider metadata."""
    extra = run.extra or {}
    metadata = extra.get("metadata", {}) or {}
    return str(metadata.get("ls_provider", "unknown"))


def _get_service_tier(run: Run) -> str:
    """Extract service_tier from invocation_params.

    service_tier lives in extra.invocation_params.service_tier, NOT metadata.
    Known values: 'priority' (~2x cost), 'default' (1x), 'flex' (~0.5x).
    Returns 'unknown' when not set.
    """
    extra = run.extra or {}
    invocation = extra.get("invocation_params", {}) or {}
    tier = invocation.get("service_tier")
    if tier:
        return str(tier)
    return "unknown"


# Model name prefix -> original provider (who made the model)
_MODEL_PROVIDER_PREFIXES: list[tuple[str, str]] = [
    ("gemini", "Google"),
    ("gpt-", "OpenAI"),
    ("o1", "OpenAI"),
    ("o3", "OpenAI"),
    ("o4", "OpenAI"),
    ("llama", "Meta"),
    ("meta-llama", "Meta"),
    ("sonar", "Perplexity"),
    ("qwen", "Alibaba"),
    ("grok", "xAI"),
    ("x-ai/", "xAI"),
    ("claude", "Anthropic"),
    ("mistral", "Mistral"),
    ("deepseek", "DeepSeek"),
    ("command", "Cohere"),
]


def _get_provider(run: Run) -> str:
    """Infer the model provider (who made the model) from the model name."""
    model = _get_model_name(run).lower()
    for prefix, provider in _MODEL_PROVIDER_PREFIXES:
        if model.startswith(prefix) or f"/{prefix}" in model:
            return provider
    # Fall back to gateway as provider for proprietary models (e.g. cerebras/gpt-oss)
    gateway = _get_gateway(run)
    if gateway != "unknown":
        return gateway.title()
    return "unknown"


def _load_pricing_file(path: str, logger: Any) -> dict[str, dict[str, float]]:
    """Load model pricing from a YAML file.

    Expected format:
        llama-3.3-70b-versatile:
          input_per_million: 0.59
          output_per_million: 0.79
        sonar:
          input_per_million: 1.00
          output_per_million: 1.00

    Returns dict mapping lowercase model name -> {input_per_million, output_per_million}.
    """
    import yaml

    with open(path) as f:
        raw = yaml.safe_load(f)

    if not isinstance(raw, dict):
        raise click.ClickException(
            f"Pricing file must be a YAML dict, got {type(raw).__name__}"
        )

    table: dict[str, dict[str, float]] = {}
    for model_name, prices in raw.items():
        if not isinstance(prices, dict):
            logger.warning(f"Skipping invalid pricing entry for {model_name}")
            continue
        table[str(model_name).lower()] = {
            "input_per_million": float(prices.get("input_per_million", 0.0)),
            "output_per_million": float(prices.get("output_per_million", 0.0)),
        }

    logger.info(f"Loaded pricing for {len(table)} models from {path}")
    return table


def _estimate_run_cost(
    run: Run,
    pricing_table: dict[str, dict[str, float]],
) -> tuple[float, float] | None:
    """Estimate prompt/completion cost using an external pricing table.

    Returns (prompt_cost, completion_cost) or None if no pricing found.
    Only applies when the run has tokens but zero cost from LangSmith.
    """
    model = _get_model_name(run).lower()
    tier = _get_service_tier(run)
    tier_key = f"{model}+{tier}" if tier != "unknown" else None
    pricing = (pricing_table.get(tier_key) if tier_key else None) or pricing_table.get(
        model
    )
    if pricing is None:
        return None

    input_per_m = pricing.get("input_per_million", 0.0)
    output_per_m = pricing.get("output_per_million", 0.0)
    prompt_tokens = run.prompt_tokens or 0
    completion_tokens = run.completion_tokens or 0

    prompt_cost = prompt_tokens * input_per_m / 1_000_000
    completion_cost = completion_tokens * output_per_m / 1_000_000
    return (prompt_cost, completion_cost)


def _extract_input_context(run: Run) -> dict[str, str]:
    """Extract structured context from run inputs.

    Looks for known patterns like channel_info JSON embedded in inputs,
    and returns a flat dict of extracted key-value pairs.
    """
    import json as json_mod

    result: dict[str, str] = {}
    inputs = run.inputs or {}

    # Look for channel_info (common pattern: JSON string in inputs)
    channel_info = inputs.get("channel_info", "")
    if isinstance(channel_info, str) and channel_info.strip().startswith("{"):
        try:
            parsed = json_mod.loads(channel_info)
            if isinstance(parsed, dict):
                for key in ("community_name", "channel_id", "channel_name"):
                    val = parsed.get(key)
                    if val:
                        result[key] = str(val)
        except (json_mod.JSONDecodeError, ValueError):
            pass
    elif isinstance(channel_info, dict):
        for key in ("community_name", "channel_id", "channel_name"):
            val = channel_info.get(key)
            if val:
                result[key] = str(val)

    return result


def _emit_empty_usage_json(output_format: str | None, ctx: Any, interval: str) -> None:
    """Emit an empty usage JSON summary when no data matches filters."""
    format_type = determine_output_format(output_format, ctx.obj.get("json"))
    if format_type == "table":
        return
    empty_data: dict[str, Any] = {
        "summary": {
            "total_tokens": 0,
            "total_cost": 0,
            "prompt_cost": 0,
            "completion_cost": 0,
            "active_buckets": 0,
            "unique_groups": 0,
            "max_concurrent_groups": 0,
            "avg_concurrent_groups": 0,
            "interval": interval,
            "run_count": 0,
        },
        "buckets": [],
    }
    if format_type in ("csv", "yaml"):
        output_formatted_data([empty_data], format_type)
    else:
        click.echo(json_dumps(empty_data))


def _metadata_value_matches(candidate: str | None, pattern: str) -> bool:
    """Check if a candidate value matches a metadata filter pattern.

    Supports three matching modes:
    - Exact match: ``channel_id=room-A``
    - Wildcard match: ``channel_id=room-*`` (``*`` and ``?`` supported)
    - Regex match: ``channel_id=/^room-[A-Z]+$/`` (slash-delimited)

    Args:
        candidate: The value to test (from metadata, tag, or trace context)
        pattern: The filter pattern string

    Returns:
        True if the candidate matches the pattern
    """
    if candidate is None:
        return False

    # Regex mode: /pattern/
    if pattern.startswith("/") and pattern.endswith("/") and len(pattern) > 2:
        import re

        try:
            return bool(re.search(pattern[1:-1], candidate))
        except re.error:
            return candidate == pattern

    # Wildcard mode: contains * or ?
    if "*" in pattern or "?" in pattern:
        import re

        regex = pattern.replace("*", ".*").replace("?", ".")
        if not pattern.startswith("*"):
            regex = "^" + regex
        if not pattern.endswith("*"):
            regex = regex + "$"
        return bool(re.match(regex, candidate))

    # Exact match (default)
    return candidate == pattern


def _truncate_hour(dt: Any) -> str:
    """Truncate a datetime to the hour, return as ISO string."""
    from datetime import datetime

    if isinstance(dt, str):
        dt = datetime.fromisoformat(dt)
    dt = ensure_aware_datetime(dt)
    return dt.strftime("%Y-%m-%dT%H:00Z")


@runs.command("usage")
@add_project_filter_options
@add_time_filter_options
@click.option(
    "--group-by",
    help="Group by a metadata or tag field (e.g., 'metadata:channel_id', 'tag:env'). "
    "Shows per-group breakdown.",
)
@click.option(
    "--breakdown",
    multiple=True,
    type=click.Choice(["model", "project", "provider", "gateway", "service_tier"]),
    help="Add breakdown dimensions (can specify multiple: --breakdown model --breakdown project --breakdown provider --breakdown gateway --breakdown service_tier).",
)
@click.option(
    "--interval",
    default="hour",
    type=click.Choice(["hour", "day"]),
    help="Time bucket interval (default: hour).",
)
@click.option(
    "--active-only",
    is_flag=True,
    help="Only show time buckets with non-zero token usage.",
)
@click.option(
    "--sample-size",
    default=0,
    type=int,
    help="Number of runs to analyze (default: 0 = all runs in time range).",
)
@click.option(
    "--tag",
    multiple=True,
    help="Filter by tag (can specify multiple times for AND logic).",
)
@add_grep_options
@add_metadata_filter_options
@click.option(
    "--filter",
    "additional_filter",
    help="Additional FQL filter (e.g., 'eq(run_type, \"llm\")').",
)
@click.option(
    "--from-cache",
    is_flag=True,
    help="Read runs from local cache instead of API. Use 'runs cache download' first.",
)
@click.option(
    "--apply-pricing",
    type=click.Path(exists=True),
    help="YAML file with model pricing ($/1M tokens) to fill in missing costs. "
    "Use 'runs pricing --from-cache --format yaml' to generate a template.",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["table", "json", "csv", "yaml"]),
    help="Output format (default: table, or json if --json flag used).",
)
@output_option()
@click.pass_context
def usage_runs(
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
    group_by: str | None,
    breakdown: tuple[str, ...],
    interval: str,
    active_only: bool,
    sample_size: int,
    tag: tuple[str, ...],
    grep: str | None,
    grep_ignore_case: bool,
    grep_regex: bool,
    grep_in: str | None,
    metadata_filters: tuple[str, ...],
    additional_filter: str | None,
    from_cache: bool,
    apply_pricing: str | None,
    output_format: str | None,
    output: str | None,
) -> None:
    """Analyze token usage over time with flexible grouping and breakdowns.

    Fetches LLM runs and aggregates token usage into time buckets (hour/day),
    with optional grouping by metadata fields and breakdowns by model/project.

    Only counts run_type="llm" runs with ls_model_name set to avoid
    double-counting tokens from parent chain runs.

    Examples:
        # Token usage per hour across all prd/* projects
        langsmith-cli runs usage --project-name-pattern "prd/*" --last 7d

        # Per channel_id breakdown with model detail
        langsmith-cli runs usage \\
          --project-name-pattern "prd/*" \\
          --group-by metadata:channel_id \\
          --breakdown model \\
          --last 7d --active-only

        # Session analysis: filter by specific channel_id
        langsmith-cli runs usage \\
          --project-name-pattern "prd/*" \\
          --metadata channel_id=chat:MyRoom-abc123 \\
          --breakdown model --breakdown project

        # From cache (fast, offline)
        langsmith-cli runs usage \\
          --project-name-pattern "prd/*" \\
          --from-cache --group-by metadata:channel_id \\
          --breakdown model --active-only

        # JSON output for further processing
        langsmith-cli --json runs usage \\
          --project-name-pattern "prd/*" \\
          --group-by metadata:channel_id \\
          --breakdown model \\
          --last 7d --active-only
    """
    from collections import defaultdict

    from langsmith_cli.commands.runs import extract_group_value, parse_grouping_field

    logger = ctx.obj["logger"]
    is_machine_readable = ctx.obj.get("json") or output_format in ["csv", "yaml"]
    logger.use_stderr = is_machine_readable

    # Build filters: only LLM runs (to avoid double-counting from chains)
    time_filters = build_time_fql_filters(since=since, last=last, before=before)
    base_filters = time_filters.copy()
    base_filters.append('eq(run_type, "llm")')

    # Tag filtering (AND logic - all tags must be present)
    if tag:
        base_filters.extend(build_tag_fql_filters(tag))

    # Add metadata filters (server-side, fast)
    if metadata_filters:
        base_filters.extend(build_metadata_fql_filters(metadata_filters))

    if additional_filter:
        base_filters.append(additional_filter)
    combined_filter = combine_fql_filters(base_filters)

    # Fetch runs - either from cache or API
    all_runs: list[Run] = []
    run_project_map: dict[str, str] = {}  # run.id -> project_name
    trace_context: dict[str, dict[str, str]] = {}  # trace_id -> {field: value}

    if from_cache:
        from langsmith_cli.cache import load_runs_from_cache

        # Resolve project names (need client for pattern matching)
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

        # Parse time filters for client-side filtering
        from langsmith_cli.filters import parse_time_filter

        since_dt, until_dt = parse_time_filter(since=since, last=last, before=before)

        logger.info(f"Loading from cache: {len(project_names)} project(s)...")
        result = load_runs_from_cache(project_names, since=since_dt, until=until_dt)
        if result.has_failures:
            for src, err in result.failed_sources[:3]:
                logger.warning(f"  {src}: {err}")

        # Build trace context map from all runs (for group-by/metadata propagation)
        # When LLM runs lack a metadata field, we can look it up from root/chain runs
        if group_by or metadata_filters:
            for run in result.items:
                tid = str(run.trace_id) if run.trace_id else None
                if not tid:
                    continue
                # Extract context from metadata
                meta = {}
                if run.extra and isinstance(run.extra, dict):
                    meta = run.extra.get("metadata", {}) or {}
                if run.metadata and isinstance(run.metadata, dict):
                    meta.update(run.metadata)
                # Extract context from inputs (e.g. channel_info JSON)
                input_ctx = _extract_input_context(run)
                # Merge (prefer root/chain data = runs with no parent)
                if tid not in trace_context:
                    trace_context[tid] = {}
                # Root runs (no parent) get priority
                is_root = run.parent_run_id is None
                for k, v in {**meta, **input_ctx}.items():
                    if v and (is_root or k not in trace_context[tid]):
                        trace_context[tid][k] = str(v)

        # Client-side filter: only LLM runs
        for run in result.items:
            if run.run_type != "llm":
                continue
            all_runs.append(run)
            # Use item_source_map from cache loader for accurate project attribution
            run_id = str(run.id)
            if run_id in result.item_source_map:
                run_project_map[run_id] = result.item_source_map[run_id]

        # Apply tag filters client-side
        if tag:
            all_runs = filter_runs_by_tags(all_runs, tag)

        # Apply metadata filters client-side (check metadata, tags, and trace context)
        # Supports exact match, wildcards (*/?), and regex (/pattern/)
        for mf in metadata_filters:
            if "=" not in mf:
                continue
            key, value = mf.split("=", 1)
            filtered: list[Run] = []
            for r in all_runs:
                # Check metadata
                direct = extract_group_value(r, "metadata", key)
                if _metadata_value_matches(direct, value):
                    filtered.append(r)
                    continue
                # Check tags: value may appear as a tag directly ("chat:Foo")
                # or as "key:value" ("channel_id:chat:Foo")
                if r.tags:
                    for run_tag in r.tags:
                        if _metadata_value_matches(
                            run_tag, value
                        ) or _metadata_value_matches(run_tag, f"{key}:{value}"):
                            filtered.append(r)
                            break
                    else:
                        # Fallback to trace context
                        tid = str(r.trace_id) if r.trace_id else None
                        if tid and _metadata_value_matches(
                            trace_context.get(tid, {}).get(key), value
                        ):
                            filtered.append(r)
                else:
                    # No tags — check trace context
                    tid = str(r.trace_id) if r.trace_id else None
                    if tid and _metadata_value_matches(
                        trace_context.get(tid, {}).get(key), value
                    ):
                        filtered.append(r)
            all_runs = filtered

        if not all_runs and not result.successful_sources:
            raise click.ClickException(
                "No cached data found. Run 'runs cache download' first."
            )
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

        logger.info(f"Fetching LLM runs from {len(pq.names)} project(s)...")

        select_fields = [
            "start_time",
            "total_tokens",
            "prompt_tokens",
            "completion_tokens",
            "total_cost",
            "extra",
            "run_type",
        ]
        # Grep needs content fields to search
        if grep:
            select_fields.extend(["inputs", "outputs", "error"])

        failed_projects: list[tuple[str, str]] = []
        sources: list[tuple[str, dict[str, Any]]] = []
        if pq.use_id:
            sources = [(f"id:{pq.project_id}", {"project_id": pq.project_id})]
        else:
            sources = [(name, {"project_name": name}) for name in pq.names]

        for source_label, proj_kwargs in sources:
            try:
                runs_iter = client.list_runs(
                    **proj_kwargs,
                    filter=combined_filter,
                    limit=None,
                    select=select_fields,
                )
                collected = 0
                for run in runs_iter:
                    all_runs.append(run)
                    run_project_map[str(run.id)] = source_label
                    collected += 1
                    if sample_size > 0 and collected >= sample_size:
                        break
            except Exception as e:
                failed_projects.append((source_label, str(e)))

        if failed_projects and len(all_runs) == 0:
            logger.warning("All projects failed to fetch:")
            for proj, error_msg in failed_projects[:3]:
                short = error_msg[:100] + "..." if len(error_msg) > 100 else error_msg
                logger.warning(f"  {proj}: {short}")
            raise click.ClickException(
                "No runs fetched. Check project names and API key."
            )

    # Apply grep filter (client-side content search)
    if grep:
        grep_fields_tuple: tuple[str, ...] = ()
        if grep_in:
            grep_fields_tuple = tuple(
                f.strip() for f in grep_in.split(",") if f.strip()
            )
        all_runs = apply_grep_filter(
            all_runs,
            grep_pattern=grep,
            grep_fields=grep_fields_tuple,
            ignore_case=grep_ignore_case,
            use_regex=grep_regex,
        )

    # Filter to only runs with a model name (avoids counting non-LLM chain wrappers)
    model_runs = [r for r in all_runs if _get_model_name(r) != "unknown"]
    source_label_str = "cache" if from_cache else "API"
    logger.info(
        f"Loaded {len(all_runs)} LLM runs ({len(model_runs)} with model info) "
        f"from {source_label_str}"
    )

    if not model_runs:
        logger.warning("No LLM runs with model info found in the selected time range.")
        _emit_empty_usage_json(output_format, ctx, interval)
        return

    # Parse group-by if provided
    group_type: str | None = None
    group_field: str | None = None
    if group_by:
        parsed = parse_grouping_field(group_by)
        if isinstance(parsed, list):
            raise click.BadParameter(
                "Multi-dimensional grouping not supported for usage. Use a single dimension."
            )
        group_type, group_field = parsed

    # Build bucket key function
    def _bucket_key(run: Run) -> str:
        if interval == "day":
            dt = ensure_aware_datetime(run.start_time)
            return dt.strftime("%Y-%m-%d")
        return _truncate_hour(run.start_time)

    # Aggregate into buckets
    # Key: (time_bucket, group_value, *breakdown_values) -> metrics
    buckets: dict[tuple[str, ...], UsageBucket] = defaultdict(UsageBucket)

    # Load external pricing table if provided
    pricing_table: dict[str, dict[str, float]] = {}
    if apply_pricing:
        pricing_table = _load_pricing_file(apply_pricing, logger)

    for run in model_runs:
        time_key = _bucket_key(run)

        # Group value (with trace context fallback for cached runs)
        group_val = "all"
        if group_type and group_field:
            extracted = extract_group_value(run, group_type, group_field)
            if not extracted and from_cache:
                # Fallback: look up from trace context (root/chain runs)
                tid = str(run.trace_id) if run.trace_id else None
                if tid and tid in trace_context:
                    extracted = trace_context[tid].get(group_field)
            group_val = extracted or "ungrouped"

        # Breakdown values
        breakdown_vals: list[str] = []
        for dim in breakdown:
            if dim == "model":
                breakdown_vals.append(_get_model_name(run))
            elif dim == "project":
                breakdown_vals.append(
                    run_project_map.get(str(run.id), _get_project_name(run))
                )
            elif dim == "provider":
                breakdown_vals.append(_get_provider(run))
            elif dim == "gateway":
                breakdown_vals.append(_get_gateway(run))
            elif dim == "service_tier":
                breakdown_vals.append(_get_service_tier(run))

        key = (time_key, group_val, *breakdown_vals)

        bucket = buckets[key]
        bucket.total_tokens += run.total_tokens or 0
        bucket.prompt_tokens += run.prompt_tokens or 0
        bucket.completion_tokens += run.completion_tokens or 0

        run_total = float(run.total_cost or 0.0)
        run_prompt = float(run.prompt_cost or 0.0)
        run_completion = float(run.completion_cost or 0.0)

        # Estimate costs for runs with tokens but no pricing from LangSmith
        if pricing_table and run_total == 0.0 and (run.total_tokens or 0) > 0:
            estimated = _estimate_run_cost(run, pricing_table)
            if estimated is not None:
                run_prompt, run_completion = estimated
                run_total = run_prompt + run_completion

        bucket.total_cost += run_total
        bucket.prompt_cost += run_prompt
        bucket.completion_cost += run_completion
        bucket.run_count += 1

    # Build results list
    results: list[dict[str, Any]] = []
    for key, metrics in buckets.items():
        row: dict[str, Any] = {
            "time": key[0],
            "group": key[1],
        }
        # Add breakdown columns
        for i, dim in enumerate(breakdown):
            row[dim] = key[2 + i]

        row.update(metrics.model_dump())
        results.append(row)

    # Sort by time, then group
    results.sort(key=lambda r: (r["time"], r["group"]))

    # Filter active-only
    if active_only:
        results = [r for r in results if r["total_tokens"] > 0]

    if not results:
        logger.warning("No usage data found for the selected filters.")
        _emit_empty_usage_json(output_format, ctx, interval)
        return

    # Compute summary stats
    unique_groups = {r["group"] for r in results}
    unique_times = {r["time"] for r in results}
    total_tokens_all = sum(r["total_tokens"] for r in results)
    total_cost_all = sum(r["total_cost"] for r in results)
    prompt_cost_all = sum(r["prompt_cost"] for r in results)
    completion_cost_all = sum(r["completion_cost"] for r in results)

    # Concurrent groups per time bucket
    groups_per_bucket: dict[str, set[str]] = defaultdict(set)
    for r in results:
        groups_per_bucket[r["time"]].add(r["group"])
    max_concurrent = (
        max(len(v) for v in groups_per_bucket.values()) if groups_per_bucket else 0
    )
    avg_concurrent = (
        sum(len(v) for v in groups_per_bucket.values()) / len(groups_per_bucket)
        if groups_per_bucket
        else 0
    )

    # Determine output format
    format_type = determine_output_format(output_format, ctx.obj.get("json"))

    # Handle file output — write results to file and return
    if output:
        write_output_to_file(results, output, console, format_type="jsonl")
        return

    if format_type != "table":
        # CSV/YAML need a flat list; JSON gets the full nested structure
        if format_type in ("csv", "yaml"):
            output_formatted_data(results, format_type)
        else:
            output_data: dict[str, Any] = {
                "summary": {
                    "total_tokens": total_tokens_all,
                    "total_cost": round(total_cost_all, 6),
                    "prompt_cost": round(prompt_cost_all, 6),
                    "completion_cost": round(completion_cost_all, 6),
                    "active_buckets": len(unique_times),
                    "unique_groups": len(unique_groups),
                    "max_concurrent_groups": max_concurrent,
                    "avg_concurrent_groups": round(avg_concurrent, 1),
                    "interval": interval,
                    "run_count": sum(r["run_count"] for r in results),
                },
                "buckets": results,
            }
            click.echo(json_dumps(output_data))
        return

    # Print summary
    group_label = group_field or "group"
    console.print("\n[bold]Token Usage Summary[/bold]")
    console.print(f"  Total tokens: [cyan]{total_tokens_all:,}[/cyan]")
    console.print(f"  Total cost: [cyan]${total_cost_all:.4f}[/cyan]")
    console.print(f"  Active {interval}s: [cyan]{len(unique_times)}[/cyan]")
    if group_by:
        console.print(f"  Unique {group_label}s: [cyan]{len(unique_groups)}[/cyan]")
        console.print(f"  Max concurrent {group_label}s: [cyan]{max_concurrent}[/cyan]")
        console.print(
            f"  Avg concurrent {group_label}s: [cyan]{avg_concurrent:.1f}[/cyan]"
        )
    console.print()

    # Build table
    table = Table(title=f"Token Usage by {interval.title()}")
    table.add_column("Time", style="cyan")
    if group_by:
        table.add_column(group_label.title(), style="green")
    for dim in breakdown:
        table.add_column(dim.title(), style="yellow")
    table.add_column("Runs", justify="right")
    table.add_column("Total Tokens", justify="right", style="bold")
    table.add_column("Prompt", justify="right")
    table.add_column("Completion", justify="right")
    table.add_column("Cost", justify="right")
    table.add_column("In $", justify="right")
    table.add_column("Out $", justify="right")

    for r in results:
        row_values = [r["time"]]
        if group_by:
            row_values.append(str(r["group"]))
        for dim in breakdown:
            row_values.append(str(r.get(dim, "")))
        row_values.extend(
            [
                str(r["run_count"]),
                f"{r['total_tokens']:,}",
                f"{r['prompt_tokens']:,}",
                f"{r['completion_tokens']:,}",
                f"${r['total_cost']:.4f}",
                f"${r['prompt_cost']:.4f}",
                f"${r['completion_cost']:.4f}",
            ]
        )
        table.add_row(*row_values)

    console.print(table)
