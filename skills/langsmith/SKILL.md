---
name: langsmith
description: Inspect and manage LangSmith traces, runs, datasets, and prompts using the 'langsmith-cli'.
---

# LangSmith Tool

Use this tool to debug AI chains, inspect past runs, or manage datasets and prompts in LangSmith.

## Prerequisites

**The CLI must be installed before using this skill.**

**Recommended Installation:**
```bash
uv tool install langsmith-cli
```

**Alternative Methods:**
- Standalone installer (curl/PowerShell)
- pip install
- From source

See **[Installation Guide](references/installation.md)** for all installation methods, troubleshooting, and platform-specific instructions.

**After CLI installation, add this skill:**
```bash
/plugin marketplace add gigaverse-app/langsmith-cli
```

## 🚨 CRITICAL: How AI Agents Should Call This CLI

**Problem:** Shell redirection `> file.json` silently loses errors! You get an empty file with no explanation.

**Solution:** ALWAYS use the `--output` flag for data extraction:

```bash
# ✅ CORRECT - Use --output flag (ALWAYS do this for data extraction)
langsmith-cli runs list --project my-project --fields id,name,status --output runs.jsonl

# Why this is correct:
# - Writes data to file (JSONL format)
# - Shows errors/warnings on screen (you will see them!)
# - Returns non-zero exit code on failure (you can detect it)
# - Shows confirmation: "Wrote N items to runs.jsonl"
```

```bash
# ❌ WRONG - Never use shell redirection for data extraction
langsmith-cli --json runs list --project my-project > runs.json
# If API fails: errors go to stderr (invisible with redirection)
# You may get a JSON error object instead of data, and won't know what happened
```

```bash
# ✅ OK for quick queries (not data extraction) - use 2>&1 to see errors
langsmith-cli --json runs list --project my-project --limit 5 2>&1
# Errors will be visible in the output (mixed with JSON)
# Check exit code: 0 = success, non-zero = failure
```

**Quick Reference:**
| Use Case | Command Pattern |
|----------|-----------------|
| Extract data to file | `langsmith-cli runs list --output data.jsonl` |
| Quick query (see results) | `langsmith-cli --json runs list 2>&1` |
| Count items | `langsmith-cli --json runs list --count` |
| Debug issues | `langsmith-cli -v runs list 2>&1` |

**Note:** When piping to other processes (`| jq`, `| python3`), prefer using `--output` to write to a file first, then read the file. This avoids potential buffering issues.

## ⚡ MANDATORY: Always Use --json

**When called by an AI agent (not a human), you MUST pass `--json` as the FIRST argument to every `langsmith-cli` command** (except `auth login` and `runs watch` which are interactive).

Without `--json`, the CLI outputs Rich tables designed for human terminals — these are unparseable, waste tokens, and break automated workflows.

```bash
# ✅ CORRECT (agent usage)
langsmith-cli --json runs list --project my-project --limit 5

# ❌ WRONG (agent gets unparseable Rich table output)
langsmith-cli runs list --project my-project --limit 5
```

## ⚡ Efficient Usage Guidelines (READ THIS)
1. **Machine Output:** ALWAYS add `--json` as the FIRST argument to `langsmith-cli` (e.g. `langsmith-cli --json runs list ...`) to get parseable output. Never use table output for agents.
2. **Context Saving:** Use `--fields` on ALL list/get commands to reduce token usage (~90% reduction).
   - Works on: `runs list`, `runs get`, `projects list`, `datasets list/get`, `examples list/get`, `prompts list`
   - Example: `langsmith-cli --json runs list --fields id,name,status`
   - Example: `langsmith-cli --json runs get <id> --fields inputs,error`
3. **Filter Fast:** Use `--status error` to find failing runs quickly.
4. **Project Scope:** Always specify `--project` (default is "default") if you know it.
5. **File Output (Recommended):** ALL list commands support `--output <file>` to write directly to file (JSONL format). This is more reliable than shell redirection and provides better feedback.
   - Works on: `runs list`, `projects list`, `datasets list`, `examples list`, `prompts list`
   - Example: `langsmith-cli runs list --fields id,name,status --output runs.jsonl`
   - Example: `langsmith-cli projects list --output projects.jsonl`
   - Writes JSONL (newline-delimited JSON) format - one object per line
   - Shows confirmation message: "Wrote N items to file.jsonl"
   - Automatically handles Unicode (Hebrew, Chinese, etc.) correctly
6. **Universal Flags:** ALL list commands support `--count` (get count instead of data) and `--exclude` (exclude items by substring, repeatable).
   - Example: `langsmith-cli --json projects list --count` returns just the number
   - Example: `langsmith-cli --json runs list --exclude smoke-test --exclude dev-test` filters out unwanted runs
7. **Verbosity Control:** Use `-q`/`-qq` for quieter output, `-v`/`-vv` for more verbose output (diagnostics go to stderr in JSON mode).
   - Default: Shows progress messages + warnings (e.g., "Fetching 100 runs...")
   - `-q`: Warnings only, no progress messages
   - `-qq`: Silent mode (errors only) - cleanest for piping to jq/scripts
   - `-v`: Debug mode (shows API calls and processing details)
   - `-vv`: Trace mode (ultra-verbose with HTTP requests and timing)
   - Example: `langsmith-cli --json -qq runs list | jq` (clean JSON, no diagnostics)
   - Example: `langsmith-cli -v runs list` (debug info for troubleshooting)
8. **Error Handling:** See the "🚨 CRITICAL" section above. Use `--output` flag for data extraction, or `2>&1` for quick queries.
9. **Long-Running Commands:** Commands like `runs cache download`, `runs list` with large `--limit`, and `runs export` can take minutes for large datasets. These commands emit progress messages to stderr (e.g., "Downloading project X... 500 runs fetched"). **When running long tasks, inform the user that the command may take a while and report progress updates from stderr.** Use `-v` for more detailed progress, or `-qq` to suppress all progress and just wait for completion.
   - Example: `langsmith-cli runs cache download --project-name-pattern "prd/*" --last 7d` may take several minutes for many projects.
   - Example: `langsmith-cli -v runs cache download --project my-project --last 30d` shows detailed download progress.
10. **Client-Side Filtering Budget:** When using client-side filters (`--grep`, `--name-pattern`, `--name-regex`), the CLI fetches more runs than `--limit` to ensure enough matches. Use `--fetch <n>` to control the fetch budget (e.g., `--limit 10 --fetch 500` fetches 500 runs, returns up to 10 matches).

## API Reference

### Authentication
- `langsmith-cli auth login`: Configure API key (saves to global config).
  - `--local`: Save to `.env` in current directory instead.

### Projects
- `langsmith-cli --json projects list [OPTIONS]`: List all projects.
  - `--limit <n>`: Max results (default: 100, use 0 for no limit)
  - `--name <text>`: Filter by exact name
  - `--name-pattern <pattern>`: Wildcard filter (e.g., `'*prod*'`)
  - `--name-regex <regex>`: Regex filter
  - `--has-runs`: Show only projects with runs
  - `--sort-by <field>`: Sort by field (name, run_count). Prefix `-` for descending
  - `--fields <comma-separated>`: Select specific fields (e.g., `id,name`)
  - `--output <file>`: Write to file instead of stdout
  - See [Projects Reference](references/projects.md) for full options and output fields.
- `langsmith-cli --json projects get <name-or-id>`: Get project details (UUID auto-detected).
  - `--include-stats/--no-stats`: Include/exclude run statistics (default: include)
  - `--fields <comma-separated>`: Select fields
- `langsmith-cli --json projects create <name>`: Create a new project.
- `langsmith-cli --json projects update <name-or-id> --name <new> --description <desc>`: Update project.
- `langsmith-cli --json projects delete <name-or-id> --confirm`: Delete a project.

### Runs (Traces)
- `langsmith-cli --json runs list [OPTIONS]`: List recent runs.
  - `--project <name>`: Filter by project name (default: "default").
  - `--project-id <uuid>`: Filter by project UUID (bypasses name resolution, faster).
  - **Multi-project:** `--project-name <text>`, `--project-name-exact <text>`, `--project-name-pattern <pattern>`, `--project-name-regex <regex>`
  - `--limit <n>`: Max results (default 10, keep it small).
  - `--status <success|error>`: Filter by status.
  - `--since <time>`: Show runs since this time (ISO, relative like `7d`, or `3 days ago`).
  - `--before <time>`: Show runs before this time (ISO, relative like `3d`, or `3 days ago`). Upper bound for time window.
  - `--last <duration>`: Show runs from last duration (e.g., `24h`, `7d`). Combinable: `--since + --last` = forward window, `--before + --last` = backward window, `--since + --before` = explicit window.
  - **Convenience shortcuts:** `--failed`, `--succeeded`, `--slow` (>5s), `--recent` (last hour), `--today`
  - `--filter <string>`: Advanced FQL query string (see FQL examples below).
  - `--roots`: Show only root traces (recommended for cleaner output).
  - `--trace-id <uuid>`: Get all runs in a specific trace tree.
  - `--run-type <type>`: Filter by type (llm, chain, tool, retriever, etc).
  - `--tag <tag>`: Filter by tag (repeatable for AND logic).
  - `--name-pattern <pattern>`: Wildcard filter on run names (client-side).
  - `--name-regex <regex>`: Regex filter on run names (client-side).
  - `--model <name>`: Filter by model name (e.g., `gpt-4`, `claude-3`).

  - `--min-latency <dur>` / `--max-latency <dur>`: Latency range (e.g., `2s`, `500ms`).
  - `--trace-filter <fql>` / `--tree-filter <fql>`: Filter on root trace / any run in tree.
  - `--sort-by <field>`: Sort by field (name, status, latency, start_time). Prefix `-` for descending.
  - `--format <table|json|csv|yaml>`: Output format.
  - **Content Search Options:**
    - `--query <text>`: Server-side full-text search (fast, but only first ~250 chars indexed).
    - `--grep <pattern>`: Client-side content search (unlimited content, supports regex).
      - `--grep-ignore-case`: Case-insensitive search.
      - `--grep-regex`: Treat pattern as regex (e.g., `[\u0590-\u05FF]` for Hebrew chars).
      - `--grep-in <fields>`: Search only specific fields (e.g., `inputs,outputs,error`).
  - `--fields <comma-separated>`: Reduce output size (e.g., `id,name,status,error`).
  - `--output <file>`: Write to file (JSONL format) instead of stdout.
  - `--no-truncate`: Show full content in table columns (only affects table output, not JSON).
  - See [Runs Reference](references/runs.md) for full field list and examples.
- `langsmith-cli --json runs get <id> [OPTIONS]`: Get details of a single run.
  - `--fields <comma-separated>`: Only return specific fields (e.g., `inputs,outputs,error`).
- `langsmith-cli --json runs get-latest [OPTIONS]`: Get the most recent run matching filters.
  - **Eliminates need for piping `runs list` into `jq` and then `runs get`.**
  - Supports all filter options: `--status`, `--failed`, `--succeeded`, `--roots`, `--tag`, `--model`, `--slow`, `--recent`, `--today`, `--min-latency`, `--max-latency`, `--since`, `--before`, `--last`, `--filter`.
  - Supports `--fields` for context efficiency.
  - Searches across multiple projects if using `--project-name-pattern` or `--project-name-regex`.
  - Example: `langsmith-cli --json runs get-latest --project my-project --fields inputs,outputs`
  - Example: `langsmith-cli --json runs get-latest --project my-project --failed --fields id,name,error`
  - Example: `langsmith-cli --json runs get-latest --project-name-pattern "prd/*" --succeeded --roots`
  - **Before (complex):** `langsmith-cli --json runs list --project X --limit 1 --roots | jq -r '.[0].id' | xargs langsmith-cli --json runs get --fields inputs,outputs`
  - **After (simple):** `langsmith-cli --json runs get-latest --project X --roots --fields inputs,outputs`
- `langsmith-cli --json runs search <query> [OPTIONS]`: Full-text search across runs.
  - `--project <name>`: Project name (default: "default").
  - Multi-project: `--project-name-pattern`, `--project-name-regex`, etc.
  - `--limit <n>`: Max results (default: 10).
  - `--roots`: Show only root traces.
  - `--in <all|inputs|outputs|error>`: Where to search (default: all).
  - `--input-contains <text>`: Filter by content in inputs.
  - `--output-contains <text>`: Filter by content in outputs.
  - `--since <time>` / `--before <time>` / `--last <duration>`: Time filters (combinable for time windows).
  - `--format <table|json|csv|yaml>`: Output format.
  - Example: `langsmith-cli --json runs search "timeout" --in error --project myapp`
- `langsmith-cli runs watch [OPTIONS]`: Live monitoring dashboard (interactive, no `--json`).
  - `--project <name>`: Project to monitor (default: "default").
  - Multi-project: `--project-name-pattern`, `--project-name-regex`, etc.
  - `--interval <seconds>`: Refresh interval (default: 2).
- `langsmith-cli runs view-file <pattern> [OPTIONS]`: View runs from JSONL files with table display.
  - **Use this to read files created by `--output`** - don't use the Read tool on JSONL files (they can be 30K+ tokens).
  - `<pattern>`: File path or glob pattern (e.g., `samples.jsonl`, `data/*.jsonl`).
  - `--fields <comma-separated>`: Only show specific fields (critical for context efficiency).
  - `--no-truncate`: Show full content in table columns (for human viewing only).
  - Supports `--json` for JSON output.
  - Example: `langsmith-cli runs view-file samples.jsonl`
  - Example: `langsmith-cli runs view-file "data/*.jsonl" --no-truncate`
  - Example: `langsmith-cli --json runs view-file samples.jsonl --fields id,name,status`
- `langsmith-cli --json runs stats --project <name>`: Get aggregate stats.
- `langsmith-cli --json runs open <id>`: Instruct the human to open this run in their browser.
- `langsmith-cli --json runs sample [OPTIONS]`: Stratified sampling by tags/metadata.
  - `--stratify-by <field>`: Grouping field (e.g., `tag:length_category`, `metadata:user_tier`).
    - **Multi-dimensional:** Use comma-separated fields (e.g., `tag:length,tag:content_type`).
  - `--values <comma-separated>`: Stratum values to sample from (e.g., `short,medium,long`).
    - For multi-dimensional: Use colon-separated combinations (e.g., `short:news,medium:gaming`).
  - `--dimension-values <pipe-separated>`: Cartesian product sampling (e.g., `short|medium|long,news|gaming`).
    - Automatically generates all combinations: (short,news), (short,gaming), (medium,news), etc.
  - `--samples-per-stratum <n>`: Number of samples per stratum (default: 10).
  - `--samples-per-combination <n>`: Alias for `--samples-per-stratum` in multi-dimensional mode.
  - `--since <time>` / `--before <time>` / `--last <duration>`: Time filters (combinable for time windows).
  - `--output <path>`: Write samples to JSONL file instead of stdout. **Recommended for data extraction** (more reliable than piping).
  - `--fields <comma-separated>`: Reduce output size.
  - Example (to file): `langsmith-cli runs sample --stratify-by tag:length --values short,medium,long --samples-per-stratum 10 --output samples.jsonl`
  - Example (to stdout): `langsmith-cli --json runs sample --stratify-by tag:length --values short,medium,long --samples-per-stratum 10`
  - Example (multi): `langsmith-cli runs sample --stratify-by tag:length,tag:content_type --dimension-values "short|long,news|gaming" --samples-per-combination 2 --output multi_samples.jsonl`
- `langsmith-cli --json runs analyze [OPTIONS]`: Group runs and compute aggregate metrics.
  - `--group-by <field>`: Grouping field (e.g., `tag:length_category`, `metadata:user_tier`).
  - `--metrics <comma-separated>`: Metrics to compute (default: `count,error_rate,p50_latency,p95_latency`).
    - Available metrics: `count`, `error_rate`, `p50_latency`, `p95_latency`, `p99_latency`, `avg_latency`, `total_tokens`, `avg_cost`
  - `--sample-size <n>`: Number of recent runs to analyze (default: 300, use 0 for all runs).
  - `--since <time>` / `--before <time>` / `--last <duration>`: Time filters (combinable for time windows).
  - `--filter <string>`: Additional FQL filter to apply.
  - `--format <format>`: Output format (json/table/csv/yaml).
  - Example: `langsmith-cli --json runs analyze --group-by tag:length --metrics count,error_rate,p95_latency`
  - Example: `langsmith-cli --json runs analyze --group-by tag:schema --metrics count,error_rate --sample-size 1000`
- `langsmith-cli --json runs tags [OPTIONS]`: Discover structured tag patterns (key:value format).
  - `--sample-size <n>`: Number of recent runs to sample (default: 1000).
  - `--since <time>` / `--before <time>` / `--last <duration>`: Time filters.
  - Returns: `{"tag_patterns": {"key1": ["val1", "val2"], ...}}`
  - Example: `langsmith-cli --json runs tags --project my-project --sample-size 5000`
- `langsmith-cli --json runs metadata-keys [OPTIONS]`: Discover metadata keys used in runs.
  - `--sample-size <n>`: Number of recent runs to sample (default: 1000).
  - `--since <time>` / `--before <time>` / `--last <duration>`: Time filters.
  - Returns: `{"metadata_keys": ["key1", "key2", ...]}`
  - Example: `langsmith-cli --json runs metadata-keys --project my-project`
- `langsmith-cli --json runs fields [OPTIONS]`: Discover all field paths, types, presence rates, and language distribution.
  - `--sample-size <n>`: Number of recent runs to sample (default: 100).
  - `--include <paths>`: Only include fields starting with these paths (comma-separated, e.g., `inputs,outputs`).
  - `--exclude <paths>`: Exclude fields starting with these paths (comma-separated, e.g., `extra,events`).
  - `--no-language`: Skip language detection (faster).
  - `--since <time>` / `--before <time>` / `--last <duration>`: Time filters.
  - Returns: `{"fields": [{"path": "inputs.query", "type": "string", "present_pct": 98.0, "languages": {"en": 80.0, "he": 15.0}, "sample": "..."}, ...], "total_runs": 100}`
  - Example: `langsmith-cli --json runs fields --project my-project --include inputs,outputs`
  - Example: `langsmith-cli --json runs fields --no-language --sample-size 50`
- `langsmith-cli --json runs describe [OPTIONS]`: Detailed field statistics with length/numeric stats.
  - `--sample-size <n>`: Number of recent runs to sample (default: 100).
  - `--include <paths>`: Only include fields starting with these paths (comma-separated).
  - `--exclude <paths>`: Exclude fields starting with these paths (comma-separated).
  - `--no-language`: Skip language detection (faster).
  - `--since <time>` / `--before <time>` / `--last <duration>`: Time filters.
  - Returns: `{"fields": [{"path": "inputs.query", "type": "string", "present_pct": 98.0, "length": {"min": 5, "max": 500, "avg": 89}, "languages": {"en": 80.0}}, ...], "total_runs": 100}`
  - Example: `langsmith-cli --json runs describe --include inputs,outputs`
  - Example: `langsmith-cli --json runs describe --project my-project --no-language`
- `langsmith-cli runs export <directory> [OPTIONS]`: Export runs as individual JSON files.
  - `--project <name>`: Project name (required)
  - `--limit <n>`: Max runs to export (default: 50)
  - `--status <success|error>`: Filter by status
  - `--roots`: Export only root traces
  - `--tag <tag>`: Filter by tag (repeatable)
  - `--since <time>` / `--before <time>` / `--last <duration>`: Time filters (combinable for time windows).
  - `--fields <comma-separated>`: Reduce exported file size
  - `--filename-pattern <pattern>`: Custom filenames (placeholders: `{run_id}`, `{name}`, `{index}`, `{trace_id}`)
  - Example: `langsmith-cli runs export ./traces --project my-project --roots --limit 100`
  - Example: `langsmith-cli --json runs export ./errors --project my-project --status error --last 24h --fields name,inputs,outputs,error`

### Datasets & Examples
- `langsmith-cli --json datasets list [OPTIONS]`: List datasets.
  - `--fields <comma-separated>`: Select fields (e.g., `id,name,data_type`)
  - `--output <file>`: Write to file instead of stdout
- `langsmith-cli --json datasets get <id> [--fields id,name,description]`: Get dataset details.
- `langsmith-cli --json datasets create <name>`: Create a dataset.
  - `--description <text>`: Dataset description.
  - `--type [kv|llm|chat]`: Dataset type (default: kv).
- `langsmith-cli --json datasets delete <name-or-id> --confirm`: Delete a dataset.
- `langsmith-cli --json datasets push <file.jsonl> --dataset <name>`: Upload examples from JSONL.
- See [Datasets Reference](references/datasets.md) for full options and output fields.
- `langsmith-cli --json examples list --dataset <name> [OPTIONS]`: List examples in a dataset.
  - `--limit <n>` / `--offset <n>`: Pagination.
  - `--splits <comma-separated>`: Filter by splits (e.g., `train,test`).
  - `--as-of <tag-or-timestamp>`: Version snapshot.
  - `--filter <fql>`: Advanced FQL query.
  - `--metadata <json>`: Filter by metadata.
  - `--fields <comma-separated>`: Select fields (e.g., `id,inputs,outputs`)
  - `--output <file>`: Write to file instead of stdout
- `langsmith-cli --json examples get <id> [--fields id,inputs,outputs]`: Get example details.
- `langsmith-cli --json examples create --dataset <name> --inputs <json> --outputs <json>`: Add an example.
  - `--metadata <json>`: Custom metadata.
  - `--split <name>`: Split name (e.g., `train`, `test`).
- `langsmith-cli --json examples update <id> --inputs <json> --outputs <json>`: Update an example.
  - `--metadata <json>`: New metadata.
  - `--split <name>`: New split name.
- `langsmith-cli --json examples delete <id> [<id>...] --confirm`: Delete examples (supports bulk).
- `langsmith-cli --json examples from-run <run-id> --dataset <name>`: Create example from a run.
- See [Examples Reference](references/examples.md) for full options and output fields.

### Prompts
- `langsmith-cli --json prompts list [OPTIONS]`: List prompt repositories.
  - `--fields <comma-separated>`: Select fields (e.g., `repo_handle,description`)
  - `--output <file>`: Write to file instead of stdout
- `langsmith-cli --json prompts get <name> [--commit <hash>]`: Fetch a prompt template.
- `langsmith-cli --json prompts pull <name> [--commit <hash>]`: Pull full prompt content (manifest, examples).
  - `--include-model`: Include model configuration
  - `--fields <comma-separated>`: Select fields
- `langsmith-cli --json prompts push <name> <file_path>`: Push a local file as a prompt.
  - `--description <text>`: Prompt description.
  - `--tags <comma-separated>`: Tags.
  - `--is-public <bool>`: Make public.
- `langsmith-cli --json prompts create <name> [--description <text>]`: Create a new prompt.
  - `--tags <comma-separated>`: Tags.
  - `--is-public <bool>`: Make public.
- `langsmith-cli --json prompts delete <name> --confirm`: Delete a prompt.
- `langsmith-cli --json prompts commits <name> [--limit N]`: List prompt versions.
  - `--offset <n>`: Skip N commits.
  - `--include-model`: Include model configuration.
  - `--fields <comma-separated>`: Select fields.
  - `--count`: Return only the count of commits.
  - `--output <file>`: Write to file.
- See [Prompts Reference](references/prompts.md) for full options and output fields.

### Token Usage & Pricing
- `langsmith-cli runs usage [OPTIONS]`: Analyze token usage over time with grouping and breakdowns.
  - `--group-by <field>`: Group by metadata/tag (e.g., `metadata:channel_id`, `metadata:community_name`).
  - `--breakdown <dim>`: Breakdown by `model`, `project`, `provider`, or `gateway` (repeatable).
    - `provider` = who made the model (Google, OpenAI, Meta, Anthropic, etc.)
    - `gateway` = API endpoint used to call it (openai, groq, cerebras, google_genai, openrouter, perplexity, etc.)
  - `--interval <hour|day>`: Time bucket size (default: hour).
  - `--active-only`: Only show time buckets with activity.
  - `--from-cache`: Use local cache instead of API (fast, offline).
  - `--apply-pricing <file.yaml>`: YAML file with model pricing ($/1M tokens) to fill in missing costs. Generate with `runs pricing --format yaml`.
  - `--tag <tag>`: Filter by tag (repeatable for AND logic). Works with both API and `--from-cache`.
  - `--metadata key=value`: Filter by metadata (repeatable). Supports wildcards (`key=room-*`) and regex (`key=/^room-[0-9]+$/`). Transparently checks tags as fallback (see Tag Format below).
  - `--grep <pattern>`: Filter runs by content (inputs/outputs/error). Client-side search.
  - `--grep-ignore-case`: Case-insensitive grep.
  - `--grep-regex`: Treat grep pattern as regex.
  - `--sample-size <n>`: Limit runs per project.
  - `--format csv|yaml|json`: Output format (default: table/json).
  - `--since <time>` / `--before <time>` / `--last <duration>`: Time filters (combinable for time windows).
  - Output includes: `total_tokens`, `prompt_tokens`, `completion_tokens`, `total_cost`, `prompt_cost`, `completion_cost`, `run_count`.
  - Example: `langsmith-cli --json runs usage --project-name-pattern "prd/*" --last 7d --breakdown model`
  - Example: `langsmith-cli --json runs usage --from-cache --breakdown model --breakdown provider --breakdown gateway --active-only`
  - Example: `langsmith-cli --json runs usage --from-cache --apply-pricing pricing.yaml --breakdown provider --active-only`
  - Example: `langsmith-cli runs usage --from-cache --group-by metadata:community_name --breakdown project --interval day`
- `langsmith-cli runs pricing [OPTIONS]`: Check model pricing coverage and look up missing prices.
  - Scans runs to find models with/without cost data in LangSmith.
  - Looks up missing prices from OpenRouter API automatically (note: Perplexity models not on OpenRouter — add manually).
  - `--from-cache`: Analyze cached runs (fast).
  - `--tag <tag>`: Filter by tag (repeatable for AND logic). Works with both API and `--from-cache`.
  - `--no-lookup`: Skip OpenRouter price lookup.
  - `--format yaml`: Generate a YAML pricing file for use with `runs usage --apply-pricing`.
  - `--since <time>` / `--before <time>` / `--last <duration>`: Time filters.
  - Example: `langsmith-cli runs pricing --project-name-pattern "prd/*" --from-cache`
  - Example: `langsmith-cli runs pricing --format yaml --project-name-pattern "prd/*" --from-cache > pricing.yaml`
  - Example: `langsmith-cli --json runs pricing --project-name-pattern "prd/*" --from-cache`
- `langsmith-cli runs cache download [OPTIONS]`: Download runs to local JSONL cache.
  - `--last <duration>`: Time range (e.g., `7d`, `24h`).
  - `--since <time>` / `--before <time>`: Time bounds (combinable with `--last` for windows).
  - `--full`: Force full re-download (clear existing cache).
  - `--run-type <type>`: Filter by run type (optional, downloads all by default).
  - `--workers <n>`: Parallel workers (default: min(4, num_projects)).
  - Example: `langsmith-cli runs cache download --project-name-pattern "prd/*" --last 7d`
  - Example: `langsmith-cli runs cache download --project my-project --since 2025-02-17 --before 2025-02-20`
- `langsmith-cli runs cache list`: List cached projects with run counts and sizes.
- `langsmith-cli runs cache grep <pattern> [OPTIONS]`: Search cached runs for text patterns in inputs/outputs/error.
  - `-i`: Case-insensitive search.
  - `-E`: Treat pattern as regex.
  - `--grep-in <fields>`: Comma-separated fields to search (default: all).
  - `--project <name>`: Search only one project's cache.
  - `--limit <n>`: Max results (default 20).
  - `--since <time>` / `--before <time>` / `--last <duration>`: Time filters.
  - Example: `langsmith-cli --json runs cache grep "error" --project my-proj`
  - Example: `langsmith-cli runs cache grep -i -E "\\buser_id\\b" --grep-in inputs`
  - Example: `langsmith-cli runs cache grep "hello" --count`
- `langsmith-cli runs cache clear [--project <name>] [--yes]`: Clear cached data.

### Self (Installation Management)
- `langsmith-cli self detect`: Show installation details (version, install method, paths).
  - Reports: version, install method (uv tool, pipx, pip, editable), install path, executable path, Python version.
- `langsmith-cli self update`: Update langsmith-cli to the latest version.
  - Auto-detects install method and runs the appropriate upgrade command.
  - Checks PyPI for latest version before updating.

## Common Patterns (No Piping Needed)

The CLI provides built-in commands that eliminate the need for Unix pipes, jq, and nested commands:

### Pattern 1: Extract Data to File and View Later (Recommended)
```bash
# ❌ BAD (shell redirection - no feedback, can fail silently, errors go to stderr)
langsmith-cli --json runs list --limit 500 --fields id,inputs > data.json

# ✅ GOOD (built-in file writing - shows confirmation, handles errors gracefully)
langsmith-cli runs list --limit 500 --fields id,inputs,metadata --output data.jsonl

# ✅ Also works with all list commands
langsmith-cli projects list --output projects.jsonl
langsmith-cli datasets list --output datasets.jsonl
langsmith-cli examples list --dataset my-dataset --output examples.jsonl
langsmith-cli prompts list --output prompts.jsonl

# Writes JSONL format (one object per line) - easier to process line-by-line
# Shows confirmation: "Wrote 500 items to data.jsonl"
# Handles Unicode correctly (Hebrew, Chinese, etc.)
# Returns non-zero exit code on failure (so you can detect errors!)
```

**Reading saved files back:**
```bash
# ✅ Use view-file to read JSONL files created by --output
# IMPORTANT: Don't try to read these files with the Read tool - they can be very large!
langsmith-cli runs view-file data.jsonl                    # Table display
langsmith-cli --json runs view-file data.jsonl             # JSON output
langsmith-cli runs view-file data.jsonl --fields id,name   # Select specific fields
langsmith-cli runs view-file "samples/*.jsonl"             # Glob patterns supported

# view-file handles large files efficiently:
# - Streams line-by-line (no memory issues)
# - Validates each line as a Run object
# - Supports --fields for context efficiency
# - Supports glob patterns for multiple files
```

### Pattern 2: Filter Projects Without Piping
```bash
# ❌ BAD (requires piping)
langsmith-cli --json projects list | jq -r '.[].name' | grep -E "(prd|stg)/"

# ✅ GOOD (use built-in filters)
langsmith-cli --json projects list --name-regex "^(prd|stg)/" --fields name
```

### Pattern 3: Get Latest Run Without Nested Commands
```bash
# ❌ BAD (requires jq + nested command)
langsmith-cli --json runs get $(
  langsmith-cli --json runs list --project X --limit 1 --fields id --roots |
  jq -r '.[0].id'
) --fields inputs,outputs

# ✅ GOOD (use get-latest)
langsmith-cli --json runs get-latest --project X --roots --fields inputs,outputs
```

### Pattern 4: Get Latest Error from Production
```bash
# ❌ BAD (complex piping)
for project in $(langsmith-cli --json projects list | jq -r '.[].name' | grep "prd/"); do
  langsmith-cli --json runs list --project "$project" --failed --limit 1
done | jq -s '.[0]'

# ✅ GOOD (use project patterns + get-latest)
langsmith-cli --json runs get-latest --project-name-pattern "prd/*" --failed --fields id,name,error
```

### Pattern 5: Filter Projects by Pattern
```bash
# Filter by substring
langsmith-cli --json projects list --name "production" --fields name

# Filter by wildcard pattern
langsmith-cli --json projects list --name-pattern "*prod*" --fields name

# Filter by regex
langsmith-cli --json projects list --name-regex "^(prd|stg)/.*" --fields name
```

### Pattern 6: Get Latest Successful Run from Multiple Projects
```bash
# Searches across all matching projects
langsmith-cli --json runs get-latest \
  --project-name-pattern "prd/*" \
  --succeeded \
  --roots \
  --fields inputs,outputs
```

## Content Search & Filtering

### When to Use --query vs --grep

**Use `--query` for:**
- ✅ Quick searches in short content (< 250 chars)
- ✅ Simple substring matches
- ✅ Server-side filtering (faster, less data downloaded)

**Use `--grep` for:**
- ✅ Searching long content (inputs/outputs > 250 chars)
- ✅ Regex patterns (Hebrew Unicode, complex patterns)
- ✅ Field-specific searches (`--grep-in inputs`)
- ✅ Case-insensitive search (`--grep-ignore-case`)

### Content Search Examples

```bash
# Server-side text search (fast, first ~250 chars)
langsmith-cli runs list --project "prd/factcheck" --query "druze" --fields id,inputs

# Client-side substring search (unlimited content)
langsmith-cli runs list --project "prd/community_news" --grep "druze" --fields id,inputs

# Case-insensitive search
langsmith-cli runs list --project "prd/suggest_topics" --grep "druze" --grep-ignore-case

# Search only in specific fields
langsmith-cli runs list --grep "error" --grep-in error,outputs --fields id,name,error

# Regex: Find Hebrew characters
langsmith-cli runs list --grep "[\u0590-\u05FF]" --grep-regex --grep-in inputs --fields id,inputs

# Combine with other filters
langsmith-cli runs list --project "prd/*" --grep "hebrew" --succeeded --roots --output hebrew_runs.jsonl
```

### FQL (Filter Query Language) Examples

```bash
# Filter by run name
langsmith-cli runs list --filter 'eq(name, "extractor")' --fields id,name

# Filter by latency
langsmith-cli runs list --filter 'gt(latency, "5s")' --fields id,name,latency

# Filter by tags
langsmith-cli runs list --filter 'has(tags, "production")' --fields id,tags

# Combine multiple conditions
langsmith-cli runs list --filter 'and(eq(run_type, "chain"), gt(latency, "10s"))' --fields id,name,latency

# Complex: chains with high latency and token usage
langsmith-cli runs list --filter 'and(eq(run_type, "chain"), gt(latency, "10s"), gt(total_tokens, 5000))' --fields id,name,latency,total_tokens

# Filter by root trace feedback
langsmith-cli runs list --filter 'eq(name, "extractor")' --trace-filter 'and(eq(feedback_key, "user_score"), eq(feedback_score, 1))' --fields id,name
```

### FQL Operators Reference

**Comparison:**
- `eq(field, value)` - Equal
- `neq(field, value)` - Not equal
- `gt(field, value)` - Greater than
- `gte(field, value)` - Greater than or equal
- `lt(field, value)` - Less than
- `lte(field, value)` - Less than or equal

**Logical:**
- `and(condition1, condition2, ...)` - All conditions must be true
- `or(condition1, condition2, ...)` - At least one condition must be true
- `not(condition)` - Negation

**Special:**
- `has(tags, "value")` - Tag contains value
- `search("text")` - Full-text search in run data

## Prefer CLI Commands Over Inline Scripts

**Before writing an inline Python script, check if the CLI already handles it.** The CLI has built-in commands for cost analysis, token aggregation, time distribution, and metadata filtering that are faster, less error-prone, and produce structured output.

If you find yourself about to write a script that:
- Reads `.jsonl` files from the cache directory → Use `--from-cache` flag instead
- Loops over runs and sums tokens/costs → Use `runs usage` instead
- Groups by metadata/model/project → Use `--group-by` and `--breakdown` instead
- Computes hourly/daily distributions → Use `--interval hour|day` instead
- Filters by metadata values → Use `--metadata key=value` instead
- Filters by content substring → Use `--grep` instead

**Try the CLI commands below first.** Only fall back to scripts for analysis that genuinely has no CLI equivalent (e.g., custom multi-dimensional joins, bespoke visualizations).

## Cost & Token Analysis Recipes

### Recipe 1: Analyze Cost for a Specific Event/Channel
```bash
# Step 1: Ensure cache is fresh
langsmith-cli runs cache download --project-name-pattern "prd/*" --last 7d

# Step 2: Analyze token usage and cost, grouped by channel, broken down by model
langsmith-cli --json runs usage \
  --from-cache \
  --project-name-pattern "prd/*" \
  --metadata channel_id=chat:MyChannel-abc123 \
  --breakdown model --breakdown project \
  --interval hour

# Step 3: For a specific time window
langsmith-cli --json runs usage \
  --from-cache \
  --project-name-pattern "prd/*" \
  --metadata channel_id=chat:MyChannel-abc123 \
  --since "2026-02-19T14:00:00Z" --before "2026-02-19T20:30:00Z" \
  --breakdown model \
  --interval hour
```

### Recipe 2: Hourly Activity Distribution
```bash
# Get hourly run counts and costs (perfect for spotting broadcast windows)
langsmith-cli --json runs usage \
  --from-cache \
  --project-name-pattern "prd/*" \
  --interval hour \
  --active-only \
  --last 3d
```

### Recipe 3: Model/Provider/Gateway Cost Breakdown
```bash
# Full breakdown by model, provider, and gateway
langsmith-cli --json runs usage \
  --from-cache \
  --project-name-pattern "prd/*" \
  --breakdown model --breakdown provider --breakdown gateway \
  --active-only --last 7d
```

### Recipe 4: Fix Missing Model Pricing
```bash
# Step 1: Generate a pricing YAML (auto-fills from LangSmith + OpenRouter)
langsmith-cli runs pricing --from-cache --project-name-pattern "prd/*" --format yaml > pricing.yaml

# Step 2: Look up missing prices and edit the YAML (see instructions below)

# Step 3: Apply pricing to fill in missing costs
langsmith-cli --json runs usage \
  --from-cache \
  --project-name-pattern "prd/*" \
  --apply-pricing pricing.yaml \
  --breakdown provider --active-only
```

**Step 2 Details — Look up missing prices for models showing `0.0`:**

The `--format yaml` output auto-fills prices from LangSmith data and OpenRouter API. Models still
showing `0.0` need manual lookup. Use web fetch to get current prices from these provider pricing pages:

| Provider | Pricing URL | Models |
|----------|------------|--------|
| Groq | `https://groq.com/pricing` | llama-3.3-70b-versatile, llama-3.1-8b-instant |
| Cerebras | `https://www.cerebras.ai/pricing` | llama-3.3-70b, gpt-oss-120b, qwen-3-* |
| Perplexity | `https://docs.perplexity.ai/guides/pricing` | sonar, sonar-pro, sonar-reasoning |
| OpenRouter | `https://openrouter.ai/models` | qwen/*, meta-llama/* (via OpenRouter gateway) |
| xAI | `https://docs.x.ai/docs/models` | grok-* |

**How to look up and fill in prices:**
1. Read the pricing.yaml file to find models with `0.0` pricing
2. For each missing model, fetch the provider's pricing page using the URLs above
3. Find the input/output price per 1M tokens on the page
4. Edit the YAML file to set `input_per_million` and `output_per_million`

Example: if Perplexity Sonar costs $1.00/1M input and $1.00/1M output:
```yaml
sonar:
  input_per_million: 1.0
  output_per_million: 1.0
```

**Important notes:**
- OpenRouter prices differ from original provider prices (OpenRouter adds its own margin or uses different tiers)
- Groq/Cerebras have free tiers — the $0 cost may be correct if the org uses free tier
- Always verify prices on the provider's official pricing page, not third-party aggregators

### Recipe 5: Cost Attribution — Lower/Upper Bound Analysis

When runs aren't fully tagged (some projects don't propagate `channel_id` or session metadata to every LLM call), use two queries to establish cost bounds:

```bash
# Step 1: ALL runs in the time window (upper bound)
langsmith-cli --json runs usage \
  --from-cache \
  --project-name-pattern "prd/*" \
  --since "2026-02-19T14:00:00Z" --before "2026-02-19T20:30:00Z" \
  --breakdown model --breakdown project --breakdown provider --breakdown gateway \
  --active-only --apply-pricing pricing.yaml

# Step 2: Only ATTRIBUTED runs (lower bound) — same query + metadata filter
langsmith-cli --json runs usage \
  --from-cache \
  --project-name-pattern "prd/*" \
  --metadata channel_id=chat:MyChannel-abc123 \
  --since "2026-02-19T14:00:00Z" --before "2026-02-19T20:30:00Z" \
  --breakdown model --breakdown project --breakdown provider --breakdown gateway \
  --active-only --apply-pricing pricing.yaml
```

**How to interpret results:**
- Compare `run_count` and `total_cost` between the two queries
- **Coverage %** per project = attributed runs / all runs — shows which projects tag their LLM calls
- Projects at 0% attribution run shared pipelines (costs can't be assigned to a single channel)
- Projects at 87-100% have good metadata propagation
- **True cost** is between attributed (lower) and all (upper), adjusted for how many other sessions were active
- **Cost/hour** = total_cost / time_window_hours — useful for comparing events
- Compare `prompt_cost` vs `completion_cost` to understand cost drivers (input-heavy vs output-heavy models)

### Recipe 6: Find Runs by Metadata and Analyze
```bash
# Filter by metadata when listing runs
langsmith-cli --json runs list \
  --project-name-pattern "prd/*" \
  --grep "Middle_East_Natives" \
  --fields id,name,total_tokens,total_cost \
  --limit 50

# Or filter in usage analysis
langsmith-cli --json runs usage \
  --from-cache \
  --metadata community_name=Middle_East_Natives \
  --breakdown model
```

### Recipe 7: Aggregate Metrics by Tag/Metadata Group
```bash
# Group by a tag dimension and compute metrics
langsmith-cli --json runs analyze \
  --project my-project \
  --group-by tag:length_category \
  --metrics count,error_rate,p95_latency,total_tokens,avg_cost \
  --since "2026-02-17" --before "2026-02-20"
```

## Tag Format and Metadata Filter Behavior

**Tag formats:** Tags are plain strings. Common conventions:
- Simple: `"prod"`, `"critical"`, `"v2"`
- Key-value (colon-separated): `"env:prod"`, `"channel_id:room-A"`, `"team:ml"`

**Metadata filter tag fallback:** When using `--metadata key=value`, the CLI checks in order:
1. Direct metadata match (`run.extra.metadata[key] == value`)
2. Tag match: `value` appears in `run.tags` (e.g., `--metadata channel_id=chat:Foo` matches tag `"chat:Foo"`)
3. Key-value tag match: `"key:value"` appears in `run.tags` (e.g., `--metadata channel_id=room-A` matches tag `"channel_id:room-A"`)
4. Trace context fallback: checks parent/root run metadata

This makes `--metadata` filters work even when data is stored as tags instead of metadata fields.

**Metadata filter pattern matching:**
- Exact: `--metadata channel_id=room-A`
- Wildcard: `--metadata channel_id=room-*` (supports `*` and `?`)
- Regex: `--metadata channel_id=/^room-[0-9]+$/` (slash-delimited)

## Key Flags for Offline Analysis

| Flag | Where | What It Does |
|------|-------|--------------|
| `--from-cache` | `runs usage`, `runs pricing` | Read from local JSONL cache (fast, offline, no API calls) |
| `--tag <tag>` | `runs usage`, `runs pricing` | Filter by tag (repeatable, AND logic). Works with API and `--from-cache` |
| `--metadata key=value` | `runs usage` | Filter by metadata (repeatable). Supports wildcards (`*`/`?`) and regex (`/pattern/`). Falls back to tag check |
| `--group-by metadata:<field>` | `runs usage`, `runs analyze` | Group results by a metadata or tag field |
| `--breakdown model` | `runs usage` | Add model dimension to aggregation |
| `--breakdown project` | `runs usage` | Add project dimension to aggregation |
| `--breakdown provider` | `runs usage` | Add provider dimension (Google, OpenAI, Meta, etc.) |
| `--breakdown gateway` | `runs usage` | Add gateway dimension (groq, cerebras, openai, google_genai, etc.) |
| `--apply-pricing <file>` | `runs usage` | YAML pricing file to fill missing costs (generate with `runs pricing --format yaml`) |
| `--format yaml` | `runs pricing` | Output pricing as YAML file for `--apply-pricing` |
| `--interval hour\|day` | `runs usage` | Time bucket size for distribution |
| `--active-only` | `runs usage` | Only show time buckets with activity |
| `--grep <pattern>` | `runs list`, `runs usage` | Client-side content/metadata search |

## Additional Resources

For complete documentation, see:

- **[Pipes to CLI Reference](../../docs/PIPES_TO_CLI_REFERENCE.md)** - Converting piped commands (jq, grep, loops) to native CLI features
- **[Installation Guide](references/installation.md)** - All installation methods, troubleshooting, and platform notes
- **[Quick Reference](docs/reference.md)** - Fast command lookup
- **[Real-World Examples](docs/examples.md)** - Complete workflows and use cases

**Detailed API References:**
- [Projects](references/projects.md) - Project management
- [Runs](references/runs.md) - Trace inspection and debugging
- [Datasets](references/datasets.md) - Dataset operations
- [Examples](references/examples.md) - Example management
- [Prompts](references/prompts.md) - Prompt templates
- [FQL](references/fql.md) - Filter Query Language
- [Troubleshooting](references/troubleshooting.md) - Error handling & configuration
