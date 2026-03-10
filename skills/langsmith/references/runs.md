## Runs (Traces)

### `runs list`

List runs with advanced filtering.

```bash
langsmith-cli --json runs list [OPTIONS]
```

**Options:**
- `--project TEXT` - Project name (default: "default")
- `--project-id TEXT` - Project UUID (bypasses name resolution, fastest lookup)
- `--project-name TEXT` - Substring/contains match for project names
- `--project-name-exact TEXT` - Exact project name match
- `--project-name-pattern TEXT` - Wildcard pattern for project names (e.g., `'dev/*'`)
- `--project-name-regex TEXT` - Regex pattern for project names
- `--limit INTEGER` - Maximum results (default: 10)
- `--status [success|error]` - Filter by status
- `--failed` - Show only failed/error runs (shorthand for `--status error`)
- `--succeeded` - Show only successful runs (shorthand for `--status success`)
- `--slow` - Filter to slow runs (latency > 5s)
- `--recent` - Filter to recent runs (last hour)
- `--today` - Filter to today's runs
- `--run-type TEXT` - Filter by type: `llm`, `chain`, `tool`, `retriever`, `prompt`, `parser`
- `--is-root BOOLEAN` - Filter for root traces only: `true` or `false`
- `--roots` - Show only root traces (shorthand for `--is-root true`)
- `--trace-id UUID` - Get all runs in a specific trace tree
- `--filter TEXT` - Advanced FQL query (see Filter Query Language section)
- `--since TEXT` - Show runs since this time (ISO format, relative shorthand like `7d`/`24h`, or natural language like `3 days ago`)
- `--last TEXT` - Show runs from last duration (e.g., `24h`, `7d`). When combined with `--since`, defines a time window: runs from `--since` to `--since + --last`.
- `--trace-filter TEXT` - Filter applied to root run of trace
- `--tree-filter TEXT` - Filter applied to any run in trace tree
- `--reference-example-id UUID` - Filter runs by reference example ID
- `--tag TEXT` - Filter by tag (repeatable for AND logic)
- `--name-pattern TEXT` - Wildcard filter on run names (client-side, e.g., `'*auth*'`)
- `--name-regex TEXT` - Regex filter on run names (client-side)
- `--model TEXT` - Filter by model name (e.g., `gpt-4`, `claude-3`)
- `--since TEXT` - Show runs since timestamp or relative time (ISO, `3d`, `3 days ago`)
- `--last TEXT` - Show runs from last duration (e.g., `24h`, `7d`, `30m`, `2w`)
- `--min-latency TEXT` - Minimum latency (e.g., `2s`, `500ms`)
- `--max-latency TEXT` - Maximum latency (e.g., `10s`, `2000ms`)
- `--query TEXT` - Server-side full-text search (fast, first ~250 chars)
- `--grep TEXT` - Client-side content search (unlimited, supports regex)
- `--grep-ignore-case` - Case-insensitive grep
- `--grep-regex` - Treat grep pattern as regex
- `--grep-in TEXT` - Comma-separated fields to search in (e.g., `inputs,outputs,error`)
- `--fetch INTEGER` - Override automatic fetch multiplier for client-side filters
- `--sort-by TEXT` - Sort by field (name, status, latency, start_time). Prefix `-` for descending
- `--format [table|json|csv|yaml]` - Output format
- `--no-truncate` - Show full content in table columns
- `--fields TEXT` - Comma-separated field names to include
- `--exclude TEXT` - Exclude items containing substring (repeatable)
- `--count` - Output only the count of results
- `--output TEXT` - Write output to file (JSONL format)

**Output Fields:**
- `id` (UUID) - Run identifier
- `name` (string) - Run name
- `run_type` (string) - Type of run (llm, chain, tool, etc.)
- `start_time` (datetime) - Start timestamp
- `end_time` (datetime|null) - End timestamp
- `status` (string) - Status: success, error, pending
- `error` (string|null) - Error message if failed
- `inputs` (object) - Input data
- `outputs` (object|null) - Output data
- `trace_id` (UUID) - Root trace identifier
- `dotted_order` (string) - Position in trace tree
- `parent_run_id` (UUID|null) - Parent run ID
- `session_id` (UUID) - Project/session ID
- `tags` (array|null) - Run tags
- `extra` (object|null) - Extra metadata
- `feedback_stats` (object|null) - Feedback statistics
- `total_tokens` (integer|null) - Total tokens used
- `prompt_tokens` (integer|null) - Prompt tokens
- `completion_tokens` (integer|null) - Completion tokens
- `first_token_time` (datetime|null) - Time to first token
- `total_cost` (float|null) - Total cost in USD

**Examples:**
```bash
# Recent errors in project
langsmith-cli --json runs list --project myapp --status error --limit 5

# All LLM calls in a trace
langsmith-cli --json runs list --trace-id <uuid> --run-type llm

# Slow runs (>5 seconds)
langsmith-cli --json runs list --filter 'gt(latency, "5s")' --limit 10

# Root runs with specific tag
langsmith-cli --json runs list --is-root true --filter 'has(tags, "production")'
```

### `runs get`

Get detailed information about a specific run.

```bash
langsmith-cli --json runs get <run-id> [OPTIONS]
```

**Arguments:**
- `run-id` (required) - Run UUID or trace ID

**Options:**
- `--fields TEXT` - Comma-separated list of fields to return (critical for context efficiency)

**Available Fields:**
Core fields (always small):
- `id` - Run UUID
- `name` - Run name
- `run_type` - Type (llm, chain, tool, etc.)
- `start_time` - Start timestamp
- `end_time` - End timestamp
- `status` - Status (success, error, pending)
- `trace_id` - Root trace ID
- `dotted_order` - Position in trace tree
- `parent_run_id` - Parent run UUID
- `session_id` - Project UUID

Large fields (use sparingly):
- `inputs` - Input data (can be large)
- `outputs` - Output data (can be large)
- `error` - Error message and traceback (can be large)
- `serialized` - Serialized component config (very large)
- `events` - Streaming events (very large)
- `extra` - Extra metadata

Metadata fields:
- `tags` - Run tags
- `feedback_stats` - Feedback statistics
- `total_tokens`, `prompt_tokens`, `completion_tokens` - Token counts
- `first_token_time` - Time to first token
- `total_cost` - Cost in USD

**Output:** Full run object or pruned object if `--fields` specified

**Examples:**
```bash
# Context-efficient (recommended)
langsmith-cli --json runs get <id> --fields inputs,outputs,error

# Minimal metadata only
langsmith-cli --json runs get <id> --fields name,status,start_time,end_time

# Full object (use sparingly, ~20KB)
langsmith-cli --json runs get <id>
```

### `runs stats`

Get aggregate statistics for a project.

```bash
langsmith-cli --json runs stats [OPTIONS]
```

**Options:**
- `--project TEXT` - Project name (default: "default")
- `--project-id TEXT` - Project UUID

**Output Fields:**
- `project_name` (string) - Project name
- `total_runs` (integer) - Total runs analyzed
- `successful_runs` (integer) - Number of successful runs
- `failed_runs` (integer) - Number of failed runs
- `success_rate` (float) - Success rate as percentage
- `avg_latency` (float|null) - Average latency in seconds
- `p50_latency` (float|null) - Median latency
- `p95_latency` (float|null) - 95th percentile latency
- `p99_latency` (float|null) - 99th percentile latency
- `total_tokens` (integer) - Total tokens across all runs
- `total_cost` (float) - Total cost in USD
- `run_types` (object) - Breakdown by run type

**Example:**
```bash
langsmith-cli --json runs stats --project myapp
```

### `runs get-latest`

Get the most recent run matching filters. Eliminates the need for piping `runs list` into `jq` and then `runs get`.

```bash
langsmith-cli --json runs get-latest [OPTIONS]
```

**Options:**
- `--project TEXT` - Project name (default: "default")
- `--project-id TEXT` - Project UUID
- `--project-name TEXT` - Substring match for project names
- `--project-name-exact TEXT` - Exact project name match
- `--project-name-pattern TEXT` - Wildcard pattern (e.g., `'prd/*'`)
- `--project-name-regex TEXT` - Regex pattern for project names
- `--status [success|error]` - Filter by status
- `--failed` - Show only failed runs (shorthand for `--status error`)
- `--succeeded` - Show only successful runs (shorthand for `--status success`)
- `--roots` - Get latest root trace only
- `--tag TEXT` - Filter by tag (repeatable)
- `--model TEXT` - Filter by model name (e.g., `gpt-4`, `claude-3`)
- `--slow` - Filter to slow runs (latency > 5s)
- `--recent` - Filter to recent runs (last hour)
- `--today` - Filter to today's runs
- `--min-latency TEXT` - Minimum latency (e.g., `2s`, `500ms`)
- `--max-latency TEXT` - Maximum latency (e.g., `10s`)
- `--since TEXT` - Since time (ISO or relative like `1 hour ago`)
- `--last TEXT` - From last duration (e.g., `24h`, `7d`)
- `--filter TEXT` - Custom FQL filter string
- `--fields TEXT` - Comma-separated field names (reduces context)
- `--output TEXT` - Write output to file

**Output:** Single run object (or pruned object if `--fields` specified)

**Examples:**
```bash
# Get latest run with inputs/outputs
langsmith-cli --json runs get-latest --project my-project --fields inputs,outputs

# Get latest error from production projects
langsmith-cli --json runs get-latest --project-name-pattern "prd/*" --failed --fields id,name,error

# Get latest slow run from last hour
langsmith-cli --json runs get-latest --project my-project --slow --recent --fields name,latency
```

### `runs search`

Full-text search across runs in one or more projects.

```bash
langsmith-cli --json runs search <query> [OPTIONS]
```

**Arguments:**
- `query` (required) - Search query string

**Options:**
- `--project TEXT` - Project name (default: "default")
- `--project-id TEXT` - Project UUID
- `--project-name TEXT` - Substring match for project names
- `--project-name-exact TEXT` - Exact project name match
- `--project-name-pattern TEXT` - Wildcard pattern (e.g., `'prod-*'`)
- `--project-name-regex TEXT` - Regex pattern for project names
- `--limit INTEGER` - Maximum results (default: 10)
- `--roots` - Show only root traces
- `--in [all|inputs|outputs|error]` - Where to search (default: all fields)
- `--input-contains TEXT` - Filter by content in inputs
- `--output-contains TEXT` - Filter by content in outputs
- `--since TEXT` - Since time (ISO, `3d`, or `3 days ago`)
- `--last TEXT` - From last duration (e.g., `24h`, `7d`)
- `--format [table|json|csv|yaml]` - Output format

**Output:** List of runs matching query

**Examples:**
```bash
# Search for errors
langsmith-cli --json runs search "database connection error" --project myapp

# Search only in error field
langsmith-cli --json runs search "timeout" --in error

# Search across production projects
langsmith-cli --json runs search "user_123" --project-name-pattern "prod-*" --in inputs
```

### `runs open`

Open run in browser (no `--json` needed).

```bash
langsmith-cli runs open <run-id>
```

**Arguments:**
- `run-id` (required) - Run UUID

**Behavior:** Opens default browser to LangSmith trace viewer

### `runs watch`

Live monitoring dashboard (interactive, no `--json`).

```bash
langsmith-cli runs watch [OPTIONS]
```

**Options:**
- `--project TEXT` - Project to monitor (default: "default")
- `--interval FLOAT` - Refresh interval in seconds (default: 2)

**Behavior:** Shows live table of recent runs with auto-refresh

### `runs usage`

Analyze token usage over time with grouping and breakdowns.

```bash
langsmith-cli --json runs usage [OPTIONS]
```

**Options:**
- `--group-by TEXT` - Group by metadata/tag (e.g., `metadata:channel_id`, `metadata:community_name`)
- `--breakdown TEXT` - Breakdown by `model` and/or `project` (repeatable)
- `--interval TEXT` - Time bucket size: `hour` or `day` (default: `hour`)
- `--active-only` - Only show time buckets with activity
- `--from-cache` - Use local cache instead of API (fast, offline)
- `--metadata TEXT` - Filter by metadata key=value (repeatable)
- `--sample-size INTEGER` - Limit runs per project

**Examples:**
```bash
# Token usage breakdown by model over last 7 days
langsmith-cli --json runs usage --project-name-pattern "prd/*" --last 7d --breakdown model

# Usage from cache, grouped by community
langsmith-cli runs usage --from-cache --group-by metadata:community_name --breakdown project --interval day
```

### `runs pricing`

Check model pricing coverage and look up missing prices from OpenRouter.

```bash
langsmith-cli --json runs pricing [OPTIONS]
```

**Options:**
- `--from-cache` - Analyze cached runs (fast)
- `--no-lookup` - Skip OpenRouter price lookup

**Examples:**
```bash
# Check pricing coverage for production services
langsmith-cli runs pricing --project-name-pattern "prd/*" --from-cache

# JSON output for programmatic use
langsmith-cli --json runs pricing --project-name-pattern "prd/*" --from-cache
```

### `runs cache download`

Download runs to local JSONL cache for fast offline analysis.

```bash
langsmith-cli runs cache download [OPTIONS]
```

**Options:**
- `--last TEXT` - Time range (e.g., `7d`, `24h`)
- `--since TEXT` - Start time (ISO format, relative, or natural language)
- `--full` - Force full re-download (clear existing cache)
- `--run-type TEXT` - Filter by run type
- `--workers INTEGER` - Parallel workers (default: min(8, num_projects))
- `--filter TEXT` - Additional FQL filter

Binary data (base64-encoded images/videos) is automatically stripped during download, replaced with size-preserving placeholders. This reduces cache size by up to 96% for services with inline media.

**Examples:**
```bash
# Cache all prd/* runs from last 7 days
langsmith-cli runs cache download --project-name-pattern "prd/*" --last 7d

# Full re-download with 4 workers
langsmith-cli runs cache download --project prd/video_moderation_service --full --workers 4

# Cache only LLM runs
langsmith-cli runs cache download --project-name-pattern "prd/*" --run-type llm
```

### `runs cache list`

List cached projects with run counts and file sizes.

```bash
langsmith-cli runs cache list
```

### `runs cache clear`

Clear cached data.

```bash
langsmith-cli runs cache clear [--project TEXT] [--yes]
```

**Options:**
- `--project TEXT` - Clear only this project's cache
- `--yes` - Skip confirmation prompt

### `runs export`

Export runs as individual JSON files for offline analysis.

```bash
langsmith-cli --json runs export <directory> [OPTIONS]
```

**Arguments:**
- `directory` (required) - Output directory (created if needed)

**Options:**
- `--project TEXT` - Project name (required)
- `--limit INTEGER` - Maximum runs to export (default: 50)
- `--status TEXT` - Filter: `success` or `error`
- `--roots` - Export only root traces
- `--run-type TEXT` - Filter by type: `llm`, `chain`, `tool`, etc.
- `--tag TEXT` - Filter by tag (can specify multiple)
- `--last TEXT` - Time window: `24h`, `7d`, `30m`, etc.
- `--since TEXT` - Since timestamp or relative time
- `--filter TEXT` - FQL filter string
- `--filename-pattern TEXT` - Output filename template (default: `{run_id}.json`)
  - Placeholders: `{run_id}`, `{trace_id}`, `{index}`, `{name}`
- `--fields TEXT` - Comma-separated fields to include (reduces file size)

**Output (JSON mode):**
```json
{
  "status": "success",
  "exported": 10,
  "directory": "/path/to/output",
  "files": ["<run-id-1>.json", "<run-id-2>.json", ...],
  "errors": []
}
```

**Examples:**
```bash
# Export last 50 root traces
langsmith-cli runs export ./traces --project my-project --roots

# Export error traces from last 24h
langsmith-cli --json runs export ./errors --project my-project --status error --last 24h

# Export with field pruning for smaller files
langsmith-cli runs export ./traces --project my-project \
  --fields name,inputs,outputs,status,error --limit 100

# Custom filenames
langsmith-cli runs export ./traces --project my-project \
  --filename-pattern "{name}_{run_id}.json"
```

### `runs sample`

Stratified sampling of runs by tags or metadata.

```bash
langsmith-cli --json runs sample [OPTIONS]
```

**Options:**
- `--project TEXT` - Project name (default: "default")
- Multi-project: `--project-name`, `--project-name-pattern`, `--project-name-regex`, etc.
- `--stratify-by TEXT` - Grouping field (e.g., `tag:length_category`, `metadata:user_tier`). Comma-separated for multi-dimensional.
- `--values TEXT` - Stratum values to sample from (comma-separated). For multi-dimensional: colon-separated combinations.
- `--dimension-values TEXT` - Cartesian product sampling (pipe-separated per dimension, comma-separated dimensions).
- `--samples-per-stratum INTEGER` - Samples per stratum (default: 10)
- `--samples-per-combination INTEGER` - Alias for `--samples-per-stratum` in multi-dimensional mode
- `--since TEXT` / `--last TEXT` - Time filters
- `--fields TEXT` - Comma-separated fields to include
- `--output TEXT` - Write to JSONL file (recommended for data extraction)

**Examples:**
```bash
# Single dimension
langsmith-cli runs sample --project my-project \
  --stratify-by "tag:length_category" --values "short,medium,long" \
  --samples-per-stratum 20 --output samples.jsonl

# Multi-dimensional (Cartesian product)
langsmith-cli runs sample --project my-project \
  --stratify-by "tag:length,tag:content_type" \
  --dimension-values "short|medium|long,news|gaming" \
  --samples-per-combination 5 --output multi.jsonl
```

### `runs analyze`

Group runs and compute aggregate metrics.

```bash
langsmith-cli --json runs analyze [OPTIONS]
```

**Options:**
- `--project TEXT` - Project name (default: "default")
- Multi-project: `--project-name`, `--project-name-pattern`, `--project-name-regex`, etc.
- `--group-by TEXT` - Grouping field (e.g., `tag:length_category`, `metadata:user_tier`)
- `--metrics TEXT` - Metrics to compute (comma-separated). Available: `count`, `error_rate`, `p50_latency`, `p95_latency`, `p99_latency`, `avg_latency`, `total_tokens`, `avg_cost`
- `--sample-size INTEGER` - Number of recent runs to analyze (default: 300, use 0 for all)
- `--filter TEXT` - Additional FQL filter
- `--format [table|json|csv|yaml]` - Output format

**Examples:**
```bash
langsmith-cli --json runs analyze --project my-project \
  --group-by "tag:schema" --metrics "count,error_rate,p95_latency"

langsmith-cli --json runs analyze --project my-project \
  --group-by "tag:schema" --sample-size 1000
```

### `runs tags`

Discover structured tag patterns (key:value format) in recent runs.

```bash
langsmith-cli --json runs tags [OPTIONS]
```

**Options:**
- `--project TEXT` - Project name (default: "default")
- Multi-project: `--project-name`, `--project-name-pattern`, `--project-name-regex`, etc.
- `--since TEXT` / `--last TEXT` - Time filters
- `--sample-size INTEGER` - Runs to sample (default: 1000)

**Output:** `{"tag_patterns": {"key1": ["val1", "val2"], ...}}`

### `runs metadata-keys`

Discover metadata keys used in recent runs.

```bash
langsmith-cli --json runs metadata-keys [OPTIONS]
```

**Options:**
- `--project TEXT` - Project name (default: "default")
- Multi-project: `--project-name`, `--project-name-pattern`, `--project-name-regex`, etc.
- `--since TEXT` / `--last TEXT` - Time filters
- `--sample-size INTEGER` - Runs to sample (default: 1000)

**Output:** `{"metadata_keys": ["key1", "key2", ...]}`

### `runs fields`

Discover all field paths, types, presence rates, and language distribution.

```bash
langsmith-cli --json runs fields [OPTIONS]
```

**Options:**
- `--project TEXT` - Project name (default: "default")
- Multi-project: `--project-name`, `--project-name-pattern`, `--project-name-regex`, etc.
- `--since TEXT` / `--last TEXT` - Time filters
- `--sample-size INTEGER` - Runs to sample (default: 100)
- `--include TEXT` - Only include fields starting with these paths (comma-separated)
- `--exclude TEXT` - Exclude fields starting with these paths (comma-separated)
- `--no-language` - Skip language detection (faster)

**Output:** `{"fields": [{"path": "inputs.query", "type": "string", "present_pct": 98.0, ...}], "total_runs": 100}`

**Examples:**
```bash
langsmith-cli --json runs fields --project my-project --include inputs,outputs
langsmith-cli --json runs fields --no-language --sample-size 50
```

### `runs describe`

Detailed field statistics with length/numeric stats. Like `runs fields` but includes min/max/avg/p50 for string lengths, numeric values, and list element counts.

```bash
langsmith-cli --json runs describe [OPTIONS]
```

**Options:** Same as `runs fields`.

**Examples:**
```bash
langsmith-cli --json runs describe --include inputs,outputs
langsmith-cli --json runs describe --project my-project --no-language
```

### `runs view-file`

View runs from JSONL files with table display. Use this to read files created by `--output`.

```bash
langsmith-cli runs view-file <pattern> [OPTIONS]
```

**Arguments:**
- `pattern` (required) - File path or glob pattern (e.g., `samples.jsonl`, `data/*.jsonl`)

**Options:**
- `--fields TEXT` - Comma-separated field names (critical for context efficiency)
- `--no-truncate` - Show full content in table columns

**Examples:**
```bash
langsmith-cli runs view-file samples.jsonl
langsmith-cli --json runs view-file samples.jsonl --fields id,name,status
langsmith-cli runs view-file "data/*.jsonl" --no-truncate
```
