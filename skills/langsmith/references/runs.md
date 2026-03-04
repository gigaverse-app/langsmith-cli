## Runs (Traces)

### `runs list`

List runs with advanced filtering.

```bash
langsmith-cli --json runs list [OPTIONS]
```

**Options:**
- `--project TEXT` - Project name (default: "default")
- `--project-id TEXT` - Project UUID
- `--limit INTEGER` - Maximum results (default: 10)
- `--status TEXT` - Filter by status: `success` or `error`
- `--run-type TEXT` - Filter by type: `llm`, `chain`, `tool`, `retriever`, `prompt`, `parser`
- `--is-root BOOLEAN` - Filter for root traces only: `true` or `false`
- `--trace-id UUID` - Get all runs in a specific trace tree
- `--filter TEXT` - Advanced FQL query (see Filter Query Language section)
- `--trace-filter TEXT` - Filter applied to root run of trace
- `--tree-filter TEXT` - Filter applied to any run in trace tree
- `--reference-example-id UUID` - Filter runs by reference example ID
- `--tag TEXT` - Filter by tag (repeatable for AND logic)
- `--roots` - Show only root traces (shorthand for `--is-root true`)
- `--since TEXT` - Show runs since timestamp or relative time
- `--last TEXT` - Show runs from last duration (e.g. `24h`, `7d`)
- `--query TEXT` - Client-side text search across run content
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
- `--interval FLOAT` - Refresh interval in seconds (default: 2)

**Behavior:** Shows live table of recent runs with auto-refresh

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
