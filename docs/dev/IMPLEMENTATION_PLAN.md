# Implementation Plan: Stratified Sampling and Analytics Commands

## Overview

Implement two new LangSmith CLI commands for metadata-based analysis:
1. **`runs sample`** - Stratified sampling by tags/metadata
2. **`runs analyze`** - Group-by analytics with aggregate metrics

## Command Specifications

### `runs sample` - Stratified Sampling

**Purpose:** Collect balanced samples from different groups (strata) based on tags or metadata.

**Syntax:**
```bash
langsmith-cli --json runs sample \
  --project "my-project" \
  --stratify-by "tag:length_category" \
  --values "short,medium,long" \
  --samples-per-stratum 10 \
  --output sample.jsonl
```

**Key Options:**
- `--stratify-by` (required): Grouping field - `tag:field_name` or `metadata:field_name`
- `--values` (required): Comma-separated stratum values
- `--samples-per-stratum` (default: 10): Samples per stratum
- `--output` (optional): Output JSONL file path
- `--fields` (optional): Field pruning for context efficiency
- Project filter options (inherited)

**Output Format (JSONL):**
```jsonl
{"run_id": "abc123", "stratum": "length_category:short", "name": "...", "inputs": {...}}
{"run_id": "def456", "stratum": "length_category:medium", "name": "...", "inputs": {...}}
```

### `runs analyze` - Grouped Analytics

**Purpose:** Group runs and compute aggregate statistics.

**Syntax:**
```bash
langsmith-cli --json runs analyze \
  --project "my-project" \
  --group-by "tag:length_category" \
  --metrics "count,error_rate,p50_latency,p95_latency"
```

**Key Options:**
- `--group-by` (required): Grouping field - `tag:field_name` or `metadata:field_name`
- `--metrics` (default: "count,error_rate,p50_latency,p95_latency"): Comma-separated metrics
- `--filter` (optional): Additional FQL filter
- `--format` (optional): Output format (table/json/csv/yaml)
- Project filter options (inherited)

**Supported Metrics:**
- `count` - Number of runs
- `error_rate` - Fraction with errors (0.0-1.0)
- `p50_latency`, `p95_latency`, `p99_latency` - Latency percentiles (seconds)
- `avg_latency` - Average latency
- `total_tokens` - Sum of tokens

**Output Format (JSON):**
```json
[
  {"group": "length_category:short", "count": 650, "error_rate": 0.02, "p50_latency": 1.2},
  {"group": "length_category:medium", "count": 280, "error_rate": 0.05, "p50_latency": 2.3}
]
```

## Implementation Approach

### Files to Modify

1. **`src/langsmith_cli/commands/runs.py`**
   - Add helper functions: `parse_grouping_field()`, `build_grouping_fql_filter()`, `extract_group_value()`, `compute_metrics()`
   - Add `@runs.command("sample")` with full implementation
   - Add `@runs.command("analyze")` with full implementation

2. **`tests/test_runs.py`**
   - Add unit tests for helper functions
   - Add integration tests for both commands
   - Test edge cases (invalid formats, empty results, etc.)

3. **`skills/langsmith/SKILL.md`**
   - Document new commands with examples
   - Add usage guidelines for Claude Code plugin

### Key Implementation Details

#### 1. Parse Grouping Field

Parse `tag:field_name` or `metadata:field_name` syntax:

```python
def parse_grouping_field(grouping_str: str) -> tuple[str, str]:
    """Parse 'tag:field_name' or 'metadata:field_name'."""
    if ":" not in grouping_str:
        raise click.BadParameter("Use 'tag:field_name' or 'metadata:field_name'")

    grouping_type, field_name = grouping_str.split(":", 1)

    if grouping_type not in ["tag", "metadata"]:
        raise click.BadParameter("Grouping type must be 'tag' or 'metadata'")

    return grouping_type.strip(), field_name.strip()
```

#### 2. Build FQL Filters

Generate appropriate FQL filters for tags vs metadata:

```python
def build_grouping_fql_filter(grouping_type: str, field_name: str, value: str) -> str:
    """Build FQL filter for a group value."""
    if grouping_type == "tag":
        # Tags stored as "field_name:value"
        return f'has(tags, "{field_name}:{value}")'
    else:  # metadata
        # Metadata requires key/value match
        return f'and(in(metadata_key, ["{field_name}"]), eq(metadata_value, "{value}"))'
```

**Tag Filter Example:** `has(tags, "length_category:short")`
**Metadata Filter Example:** `and(in(metadata_key, ["user_tier"]), eq(metadata_value, "premium"))`

#### 3. Extract Group Value from Run

Extract the group value from a Run instance:

```python
def extract_group_value(run: Run, grouping_type: str, field_name: str) -> str | None:
    """Extract group value from run."""
    if grouping_type == "tag":
        # Search tags for "field_name:*" pattern
        prefix = f"{field_name}:"
        if run.tags:
            for tag in run.tags:
                if tag.startswith(prefix):
                    return tag[len(prefix):]
        return None
    else:  # metadata
        # Lookup in metadata dict
        if run.metadata and isinstance(run.metadata, dict):
            return run.metadata.get(field_name)
        return None
```

#### 4. Compute Metrics

Calculate aggregate statistics over a list of runs:

```python
def compute_metrics(runs: list[Run], requested_metrics: list[str]) -> dict[str, float | int]:
    """Compute aggregate metrics."""
    import statistics

    result: dict[str, float | int] = {}

    if not runs:
        return {m: 0 for m in requested_metrics}

    if "count" in requested_metrics:
        result["count"] = len(runs)

    if "error_rate" in requested_metrics:
        error_count = sum(1 for r in runs if r.error is not None)
        result["error_rate"] = error_count / len(runs)

    # Latency metrics
    latencies = [r.latency for r in runs if r.latency is not None]
    if latencies:
        if "p50_latency" in requested_metrics:
            result["p50_latency"] = statistics.median(latencies)
        if "p95_latency" in requested_metrics:
            result["p95_latency"] = statistics.quantiles(latencies, n=20)[18]
        if "p99_latency" in requested_metrics:
            result["p99_latency"] = statistics.quantiles(latencies, n=100)[98]

    if "total_tokens" in requested_metrics:
        result["total_tokens"] = sum(r.total_tokens or 0 for r in runs)

    return result
```

#### 5. `runs sample` Implementation Flow

1. Parse `--stratify-by` → get (grouping_type, field_name)
2. Parse `--values` → get list of stratum values
3. Get matching projects
4. For each stratum value:
   - Build FQL filter: `build_grouping_fql_filter(grouping_type, field_name, value)`
   - Fetch runs: `client.list_runs(filter=fql_filter, limit=samples_per_stratum)`
   - Add "stratum" field to each run dict
5. Output as JSONL (to file or stdout)

#### 6. `runs analyze` Implementation Flow

1. Parse `--group-by` → get (grouping_type, field_name)
2. Parse `--metrics` → get list of metric names
3. Get matching projects
4. Fetch all runs with optional filter: `client.list_runs(filter=additional_filter, limit=None)`
5. Group runs client-side:
   - Extract group value from each run: `extract_group_value(run, grouping_type, field_name)`
   - Add run to group dict: `groups[group_value].append(run)`
6. Compute metrics for each group: `compute_metrics(group_runs, requested_metrics)`
7. Output as JSON/table/CSV/YAML

### Edge Cases

1. **Empty results:** Print warning, continue with other strata
2. **Invalid grouping syntax:** Raise `click.BadParameter` with clear message
3. **Missing field values:** Skip run when grouping (return None from `extract_group_value`)
4. **No latency data:** Return 0.0 for latency metrics
5. **File write errors:** Catch exception, show clear error message

### Type Safety

- All functions use strict types: `Run`, `list[Run]`, `str | None`, `dict[str, float | int]`
- No `Any` types (except JSON serialization contexts)
- No `getattr()` - use direct attribute access on SDK models
- No `# type: ignore` comments
- Run `uv run pyright` - must be 0 errors

## Testing Strategy

### Unit Tests (test_runs.py)

1. **Helper function tests:**
   - `test_parse_grouping_field_valid()` - Valid tag/metadata syntax
   - `test_parse_grouping_field_invalid()` - Invalid formats
   - `test_build_grouping_fql_filter_tag()` - Tag FQL generation
   - `test_build_grouping_fql_filter_metadata()` - Metadata FQL generation
   - `test_extract_group_value_from_tags()` - Extract from run.tags
   - `test_extract_group_value_from_metadata()` - Extract from run.metadata
   - `test_compute_metrics_count()` - Count metric
   - `test_compute_metrics_error_rate()` - Error rate metric
   - `test_compute_metrics_latency_percentiles()` - Latency percentiles

2. **Integration tests:**
   - `test_runs_sample_basic()` - Basic stratified sampling
   - `test_runs_sample_with_output_file()` - JSONL file output
   - `test_runs_sample_with_fields_pruning()` - Field filtering
   - `test_runs_sample_invalid_stratify_by()` - Error handling
   - `test_runs_analyze_basic()` - Basic analytics
   - `test_runs_analyze_multiple_groups()` - Multiple groups
   - `test_runs_analyze_table_output()` - Table format
   - `test_runs_analyze_with_filter()` - Additional FQL filter
   - `test_runs_analyze_invalid_group_by()` - Error handling

### Test Data Pattern

**Always use real Pydantic models, never MagicMock:**

```python
from langsmith.schemas import Run
from uuid import UUID
from datetime import datetime, timezone

test_run = Run(
    id=UUID("00000000-0000-0000-0000-000000000001"),
    name="test-run",
    run_type="chain",
    start_time=datetime.now(timezone.utc),
    tags=["length_category:short"],
    metadata={"user_tier": "premium"},
    latency=1.5,
    error=None,
)
```

## Verification Steps

1. **Run type checker:** `uv run pyright` (must be 0 errors)
2. **Run all tests:** `uv run pytest tests/test_runs.py -v`
3. **Check coverage:** `uv run pytest --cov=src/langsmith_cli/commands/runs --cov-report=term-missing`
4. **Manual smoke test:**
   ```bash
   # Sample command
   uv run langsmith-cli --json runs sample \
     --project default \
     --stratify-by "tag:category" \
     --values "test" \
     --samples-per-stratum 5

   # Analyze command
   uv run langsmith-cli --json runs analyze \
     --project default \
     --group-by "tag:category" \
     --metrics "count,error_rate"
   ```

5. **Update SKILL.md** with new command examples
6. **Verify no regressions:** `uv run pytest` (all existing tests pass)

## Critical Files

- **`src/langsmith_cli/commands/runs.py`** - All implementation code
- **`tests/test_runs.py`** - All tests
- **`tests/conftest.py`** - Fixtures (reference for Run instances)
- **`skills/langsmith/SKILL.md`** - Documentation update

## Success Criteria

✅ Both commands implemented with full functionality
✅ Helper functions have comprehensive unit tests
✅ Integration tests cover happy paths and edge cases
✅ Type checker passes with 0 errors
✅ All existing tests still pass
✅ Field pruning works correctly with `--fields`
✅ Both JSON and table output modes work
✅ SKILL.md updated with new commands
✅ 100% test coverage of new code

---

 ## Approach A: Metadata-Based (Works Today)

  ### Core Principle

  **Users tag runs with relevant metadata during authoring**, then LangSmith queries those tags.

  ### Design Pattern

  **Step 1: Enrich runs with metadata at authoring time**

  ```python
  from langsmith import Client
  import json

  def log_run_with_enriched_metadata(inputs, outputs, **kwargs):
  """Log run to LangSmith with computed metadata for clustering/analytics."""
  client = Client()

  # Compute metadata from inputs
  transcript_length = len(inputs.get("transcript", ""))

  # Categorize
  if transcript_length < 1000:
  length_category = "short"
  elif transcript_length < 5000:
  length_category = "medium"
  else:
  length_category = "long"

  # Add tags for queryability
  tags = [
  f"length_category:{length_category}",
  f"channel:{inputs.get('channel_id', 'unknown')}",
  ]

  # Add metadata for analytics
  metadata = {
  "transcript_length": transcript_length,
  "length_category": length_category,
  "channel_id": inputs.get("channel_id"),
  }

  return client.create_run(
  inputs=inputs,
  outputs=outputs,
  tags=tags,
  extra={"metadata": metadata},
  **kwargs
  )
  ```

  **Step 2: Query using metadata**

  ```bash
  # Cluster by length_category (via tags)
  langsmith-cli --json runs list \
  --filter 'has(tags, "length_category:short")' \
  --limit 10 \
  > short_runs.json

  langsmith-cli --json runs list \
  --filter 'has(tags, "length_category:medium")' \
  --limit 10 \
  > medium_runs.json

  langsmith-cli --json runs list \
  --filter 'has(tags, "length_category:long")' \
  --limit 10 \
  > long_runs.json
  ```

  ### Feature Request A1: Stratified Sampling Shorthand

  **Problem**: Manual clustering is tedious (3 separate commands above).

  **Proposed Syntax:**
  ```bash
  langsmith-cli --json runs sample \
  --project "prd/my-service" \
  --stratify-by "tag:length_category" \
  --values "short,medium,long" \
  --samples-per-stratum 10 \
  --output stratified_sample.jsonl
  ```

  **Semantics:**
  1. Parse `--stratify-by "tag:length_category"`
  - Prefix `tag:` means search in run tags
  - Look for tags matching pattern `length_category:*`
  2. For each value in `--values`:
  - Execute: `filter='has(tags, "length_category:{value}")'`
  - Fetch up to `--samples-per-stratum` runs
  3. Combine results into output file

  **Output format:**
  ```jsonl
  {"run_id": "abc123", "stratum": "short", "inputs": {...}, "outputs": {...}}
  {"run_id": "def456", "stratum": "short", "inputs": {...}, "outputs": {...}}
  ...
  {"run_id": "xyz789", "stratum": "medium", "inputs": {...}, "outputs": {...}}
  ```

  **Alternative: Metadata prefix**
  ```bash
  langsmith-cli --json runs sample \
  --stratify-by "metadata:length_category" \
  --values "short,medium,long" \
  --samples-per-stratum 10
  ```

  **Semantics:**
  - Prefix `metadata:` means search in `extra.metadata`
  - Execute: `filter='and(in(metadata_key, ["length_category"]), eq(metadata_value, "{value}"))'`

  ### Feature Request A2: Analytics by Metadata

  **Problem**: Want to compute error rates, latency stats grouped by metadata fields.

  **Proposed Syntax:**
  ```bash
  langsmith-cli --json runs analyze \
  --project "prd/my-service" \
  --group-by "tag:length_category" \
  --metrics "error_rate,p50_latency,p95_latency,count" \
  --filter 'gte(start_time, "2026-01-01")'
  ```

  **Semantics:**
  1. Parse `--group-by "tag:length_category"`
  - Prefix `tag:` means group by tag pattern
  - Extract all tags matching `length_category:*`
  2. For each extracted value:
  - Count runs with that tag
  - Compute metrics (error_rate, latency percentiles)
  3. Return grouped results

  **Output format:**
  ```json
  [
  {
  "group": "length_category:short",
  "count": 650,
  "error_rate": 0.02,
  "p50_latency": 1.2,
  "p95_latency": 2.8,
  "p99_latency": 5.1
  },
  {
  "group": "length_category:medium",
  "count": 280,
  "error_rate": 0.05,
  "p50_latency": 2.3,
  "p95_latency": 6.7,
  "p99_latency": 12.3
  },
  {
  "group": "length_category:long",
  "count": 70,
  "error_rate": 0.18,
  "p50_latency": 8.9,
  "p95_latency": 24.5,
  "p99_latency": 45.2
  }
  ]
  ```

  **Insight from output**: Long inputs have 9x higher error rate (0.18 vs 0.02) and 7x higher latency!

  **Alternative: Group by metadata**
  ```bash
  langsmith-cli --json runs analyze \
  --group-by "metadata:channel_id" \
  --metrics "error_rate,avg_cost,total_tokens"
  ```

  ### Advantages of Metadata-Based Approach

  ✅ **Works with current LangSmith** (no API changes needed)
  ✅ **Explicit categorization** (users control groupings)
  ✅ **Queryable via FQL** (tags and metadata are indexed)
  ✅ **Composable** (can combine multiple grouping dimensions)

  ### Disadvantages

  ❌ **Requires upfront planning** (must tag during authoring)
  ❌ **No dynamic clustering** (can't discover natural groupings)
  ❌ **Metadata duplication** (same info in inputs AND metadata)
  ❌ **Not retroactive** (can't cluster old runs without metadata)
 ---
  Concrete Examples

  Example 1: Metamind Moments Stratified Sampling

  Current approach (manual):
  # 3 separate queries
  langsmith-cli --json runs list --filter 'has(tags, "length:short")' --limit 10
  langsmith-cli --json runs list --filter 'has(tags, "length:medium")' --limit 10
  langsmith-cli --json runs list --filter 'has(tags, "length:long")' --limit 10

  Proposed approach:
  # Single command
  langsmith-cli --json runs sample \
    --stratify-by "tag:length" \
    --values "short,medium,long" \
    --samples-per-stratum 10 \
    --output stratified_sample.jsonl

  Example 2: Find Problem Patterns

  langsmith-cli --json runs analyze \
    --group-by "tag:length" \
    --metrics "error_rate,p95_latency,avg_cost" \
    --filter 'has(tags, "schema:v2_4_6")'

  Output:
  [
    {"group": "short", "count": 650, "error_rate": 0.02, "p95_latency": 2.8},
    {"group": "medium", "count": 280, "error_rate": 0.05, "p95_latency": 6.7},
    {"group": "long", "count": 70, "error_rate": 0.18, "p95_latency": 24.5}
  ]

  Insight: Long inputs have 9x higher error rate (0.18 vs 0.02)! → Optimize prompt for long transcripts.

  ---
  Recommendation

  Start with Approach A (Metadata-Based) because:
  1. No LangSmith API changes needed
  2. Can implement in langsmith-cli today
  3. Gets 80% of the value

  File this feature request with LangSmith:
  Feature: Stratified Sampling & Analytics by Metadata

  Add two commands:

  1. `runs sample --stratify-by "tag:*" --values "..." --samples-per-stratum N`
  2. `runs analyze --group-by "tag:*" --metrics "error_rate,p95_latency,..."`

  Implementation: Query runs by tags (already indexed), group results,
  compute metrics per group.

  Use case: Metamind needs to sample diverse inputs for evaluation
  and analyze which input patterns correlate with failures.
