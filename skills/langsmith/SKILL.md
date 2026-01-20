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
# If API fails: you get empty [], no error message, exit code 0
# You won't know anything went wrong!
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

## API Reference

### Projects
- `langsmith-cli --json projects list [OPTIONS]`: List all projects.
  - `--fields <comma-separated>`: Select specific fields (e.g., `id,name`)
  - `--output <file>`: Write to file instead of stdout
- `langsmith-cli --json projects create <name>`: Create a new project.

### Runs (Traces)
- `langsmith-cli --json runs list [OPTIONS]`: List recent runs.
  - `--project <name>`: Filter by project.
  - `--limit <n>`: Max results (default 10, keep it small).
  - `--status <success|error>`: Filter by status.
  - `--filter <string>`: Advanced FQL query string (see FQL examples below).
  - **Content Search Options:**
    - `--query <text>`: Server-side full-text search (fast, but only first ~250 chars indexed).
    - `--grep <pattern>`: Client-side content search (unlimited content, supports regex).
      - `--grep-ignore-case`: Case-insensitive search.
      - `--grep-regex`: Treat pattern as regex (e.g., `[\u0590-\u05FF]` for Hebrew chars).
      - `--grep-in <fields>`: Search only specific fields (e.g., `inputs,outputs,error`).
  - `--fields <comma-separated>`: Reduce output size (e.g., `id,name,status,error`).
  - `--output <file>`: Write to file (JSONL format) instead of stdout.
  - `--no-truncate`: Show full content in table columns (only affects table output, not JSON).
  - `--roots`: Show only root traces (recommended for cleaner output).
- `langsmith-cli --json runs get <id> [OPTIONS]`: Get details of a single run.
  - `--fields <comma-separated>`: Only return specific fields (e.g., `inputs,outputs,error`).
- `langsmith-cli --json runs get-latest [OPTIONS]`: Get the most recent run matching filters.
  - **Eliminates need for piping `runs list` into `jq` and then `runs get`.**
  - Supports all filter options: `--status`, `--failed`, `--succeeded`, `--roots`, `--tag`, `--model`, `--slow`, `--recent`, `--today`, `--min-latency`, `--max-latency`, `--since`, `--last`, `--filter`.
  - Supports `--fields` for context efficiency.
  - Searches across multiple projects if using `--project-name-pattern` or `--project-name-regex`.
  - Example: `langsmith-cli --json runs get-latest --project my-project --fields inputs,outputs`
  - Example: `langsmith-cli --json runs get-latest --project my-project --failed --fields id,name,error`
  - Example: `langsmith-cli --json runs get-latest --project-name-pattern "prd/*" --succeeded --roots`
  - **Before (complex):** `langsmith-cli --json runs list --project X --limit 1 --roots | jq -r '.[0].id' | xargs langsmith-cli --json runs get --fields inputs,outputs`
  - **After (simple):** `langsmith-cli --json runs get-latest --project X --roots --fields inputs,outputs`
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
  - `--filter <string>`: Additional FQL filter to apply.
  - `--format <format>`: Output format (json/table/csv/yaml).
  - Example: `langsmith-cli --json runs analyze --group-by tag:length --metrics count,error_rate,p95_latency`
  - Example: `langsmith-cli --json runs analyze --group-by tag:schema --metrics count,error_rate --sample-size 1000`
- `langsmith-cli --json runs tags [OPTIONS]`: Discover structured tag patterns (key:value format).
  - `--sample-size <n>`: Number of recent runs to sample (default: 1000).
  - Returns: `{"tag_patterns": {"key1": ["val1", "val2"], ...}}`
  - Example: `langsmith-cli --json runs tags --project my-project --sample-size 5000`
- `langsmith-cli --json runs metadata-keys [OPTIONS]`: Discover metadata keys used in runs.
  - `--sample-size <n>`: Number of recent runs to sample (default: 1000).
  - Returns: `{"metadata_keys": ["key1", "key2", ...]}`
  - Example: `langsmith-cli --json runs metadata-keys --project my-project`
- `langsmith-cli --json runs fields [OPTIONS]`: Discover all field paths, types, presence rates, and language distribution.
  - `--sample-size <n>`: Number of recent runs to sample (default: 100).
  - `--include <paths>`: Only include fields starting with these paths (comma-separated, e.g., `inputs,outputs`).
  - `--exclude <paths>`: Exclude fields starting with these paths (comma-separated, e.g., `extra,events`).
  - `--no-language`: Skip language detection (faster).
  - Returns: `{"fields": [{"path": "inputs.query", "type": "string", "present_pct": 98.0, "languages": {"en": 80.0, "he": 15.0}, "sample": "..."}, ...], "total_runs": 100}`
  - Example: `langsmith-cli --json runs fields --project my-project --include inputs,outputs`
  - Example: `langsmith-cli --json runs fields --no-language --sample-size 50`
- `langsmith-cli --json runs describe [OPTIONS]`: Detailed field statistics with length/numeric stats.
  - `--sample-size <n>`: Number of recent runs to sample (default: 100).
  - `--include <paths>`: Only include fields starting with these paths (comma-separated).
  - `--exclude <paths>`: Exclude fields starting with these paths (comma-separated).
  - `--no-language`: Skip language detection (faster).
  - Returns: `{"fields": [{"path": "inputs.query", "type": "string", "present_pct": 98.0, "length": {"min": 5, "max": 500, "avg": 89}, "languages": {"en": 80.0}}, ...], "total_runs": 100}`
  - Example: `langsmith-cli --json runs describe --include inputs,outputs`
  - Example: `langsmith-cli --json runs describe --project my-project --no-language`

### Datasets & Examples
- `langsmith-cli --json datasets list [OPTIONS]`: List datasets.
  - `--fields <comma-separated>`: Select fields (e.g., `id,name,data_type`)
  - `--output <file>`: Write to file instead of stdout
- `langsmith-cli --json datasets get <id> [--fields id,name,description]`: Get dataset details.
- `langsmith-cli --json datasets create <name>`: Create a dataset.
- `langsmith-cli --json examples list --dataset <name> [OPTIONS]`: List examples in a dataset.
  - `--fields <comma-separated>`: Select fields (e.g., `id,inputs,outputs`)
  - `--output <file>`: Write to file instead of stdout
- `langsmith-cli --json examples get <id> [--fields id,inputs,outputs]`: Get example details.
- `langsmith-cli --json examples create --dataset <name> --inputs <json> --outputs <json>`: Add an example.

### Prompts
- `langsmith-cli --json prompts list [OPTIONS]`: List prompt repositories.
  - `--fields <comma-separated>`: Select fields (e.g., `repo_handle,description`)
  - `--output <file>`: Write to file instead of stdout
- `langsmith-cli --json prompts get <name> [--commit <hash>]`: Fetch a prompt template.
- `langsmith-cli --json prompts push <name> <file_path>`: Push a local file as a prompt.

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

### Pattern 2: Get Latest Run Without Nested Commands
```bash
# ❌ BAD (requires jq + nested command)
langsmith-cli --json runs get $(
  langsmith-cli --json runs list --project X --limit 1 --fields id --roots |
  jq -r '.[0].id'
) --fields inputs,outputs

# ✅ GOOD (use get-latest)
langsmith-cli --json runs get-latest --project X --roots --fields inputs,outputs
```

### Pattern 3: Get Latest Error from Production
```bash
# ❌ BAD (complex piping)
for project in $(langsmith-cli --json projects list | jq -r '.[].name' | grep "prd/"); do
  langsmith-cli --json runs list --project "$project" --failed --limit 1
done | jq -s '.[0]'

# ✅ GOOD (use project patterns + get-latest)
langsmith-cli --json runs get-latest --project-name-pattern "prd/*" --failed --fields id,name,error
```

### Pattern 4: Filter Projects by Pattern
```bash
# Filter by substring
langsmith-cli --json projects list --name "production" --fields name

# Filter by wildcard pattern
langsmith-cli --json projects list --name-pattern "*prod*" --fields name

# Filter by regex
langsmith-cli --json projects list --name-regex "^(prd|stg)/.*" --fields name
```

### Pattern 5: Get Latest Successful Run from Multiple Projects
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
