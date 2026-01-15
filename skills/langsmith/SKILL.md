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

## ⚡ Efficient Usage Guidelines (READ THIS)
1. **Machine Output:** ALWAYS add `--json` as the FIRST argument to `langsmith-cli` (e.g. `langsmith-cli --json runs list ...`) to get parseable output. Never use table output for agents.
2. **Context Saving:** Use `--fields` on ALL list/get commands to reduce token usage (~90% reduction).
   - Works on: `runs list`, `runs get`, `projects list`, `datasets list/get`, `examples list/get`, `prompts list`
   - Example: `langsmith-cli --json runs list --fields id,name,status`
   - Example: `langsmith-cli --json runs get <id> --fields inputs,error`
3. **Filter Fast:** Use `--status error` to find failing runs quickly.
4. **Project Scope:** Always specify `--project` (default is "default") if you know it.
5. **File Output:** For data extraction, use built-in file output options (e.g., `runs sample --output file.jsonl`) instead of Unix pipes (`> file.json`). Built-in file writing is more reliable and avoids potential formatting issues.

## API Reference

### Projects
- `langsmith-cli --json projects list [--fields id,name]`: List all projects.
- `langsmith-cli --json projects create <name>`: Create a new project.

### Runs (Traces)
- `langsmith-cli --json runs list [OPTIONS]`: List recent runs.
  - `--project <name>`: Filter by project.
  - `--limit <n>`: Max results (default 10, keep it small).
  - `--status <success|error>`: Filter by status.
  - `--filter <string>`: Advanced LangSmith query string.
  - `--fields <comma-separated>`: Reduce output size (e.g., `id,name,status,error`).
  - `--no-truncate`: Show full content in table columns (only affects table output, not JSON).
  - `--roots`: Show only root traces (recommended for cleaner output).
- `langsmith-cli --json runs get <id> [OPTIONS]`: Get details of a single run.
  - `--fields <comma-separated>`: Only return specific fields (e.g., `inputs,outputs,error`).
- `langsmith-cli runs view-file <pattern> [OPTIONS]`: View runs from JSONL files with table display.
  - `<pattern>`: File path or glob pattern (e.g., `samples.jsonl`, `data/*.jsonl`).
  - `--fields <comma-separated>`: Only show specific fields.
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

### Datasets & Examples
- `langsmith-cli --json datasets list [--fields id,name,data_type]`: List datasets.
- `langsmith-cli --json datasets get <id> [--fields id,name,description]`: Get dataset details.
- `langsmith-cli --json datasets create <name>`: Create a dataset.
- `langsmith-cli --json examples list --dataset <name> [--fields id,inputs,outputs]`: List examples in a dataset.
- `langsmith-cli --json examples get <id> [--fields id,inputs,outputs]`: Get example details.
- `langsmith-cli --json examples create --dataset <name> --inputs <json> --outputs <json>`: Add an example.

### Prompts
- `langsmith-cli --json prompts list [--fields repo_handle,description]`: List prompt repositories.
- `langsmith-cli --json prompts get <name> [--commit <hash>]`: Fetch a prompt template.
- `langsmith-cli --json prompts push <name> <file_path>`: Push a local file as a prompt.

## Additional Resources

For complete documentation, see:

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
