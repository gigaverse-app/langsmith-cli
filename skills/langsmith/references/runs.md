## Runs (Traces)

### `runs list`

List runs with advanced filtering.

```bash
langsmith-cli --json runs list [OPTIONS]
```

**Options:**
- `--project TEXT` - Project name (default: "default")
- `--limit INTEGER` - Maximum results (default: 10, max: 100)
- `--status TEXT` - Filter by status: `success` or `error`
- `--run-type TEXT` - Filter by type: `llm`, `chain`, `tool`, `retriever`, `prompt`, `parser`
- `--is-root BOOLEAN` - Filter for root traces only: `true` or `false`
- `--trace-id UUID` - Get all runs in a specific trace tree
- `--filter TEXT` - Advanced FQL query (see Filter Query Language section)
- `--trace-filter TEXT` - Filter applied to root run of trace
- `--tree-filter TEXT` - Filter applied to any run in trace tree
- `--order-by TEXT` - Sort field (default: `-start_time`). Prefix with `-` for descending
- `--reference-example-id UUID` - Filter runs by reference example ID

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
- `--limit INTEGER` - Number of recent runs to analyze (default: 100)

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
langsmith-cli --json runs stats --project myapp --limit 1000
```

### `runs search`

Search runs by content (experimental).

```bash
langsmith-cli --json runs search <query> [OPTIONS]
```

**Arguments:**
- `query` (required) - Search query string

**Options:**
- `--project TEXT` - Project name (default: "default")
- `--limit INTEGER` - Maximum results (default: 10)

**Output:** List of runs matching query

**Example:**
```bash
langsmith-cli --json runs search "database connection error" --project myapp
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
- `--refresh INTEGER` - Refresh interval in seconds (default: 2)

**Behavior:** Shows live table of recent runs with auto-refresh

