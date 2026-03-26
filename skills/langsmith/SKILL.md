---
name: langsmith
description: Inspect and manage LangSmith traces, runs, datasets, and prompts using the 'langsmith-cli'.
---

# LangSmith Tool

Use this tool to debug AI chains, inspect past runs, manage datasets, and analyze token costs in LangSmith.

## Prerequisites

```bash
uv tool install langsmith-cli
/plugin marketplace add gigaverse-app/langsmith-cli
```

See [Installation Guide](references/installation.md) if install fails or for alternative methods.

---

## 🚨 MANDATORY Rules — Read These First

### 1. Always use `--json`

**ALWAYS pass `--json` as the FIRST argument.** Without it you get Rich terminal tables — unparseable, useless to agents.

```bash
# ✅ CORRECT
langsmith-cli --json runs list --project my-project --limit 5

# ❌ WRONG — Rich table output, cannot be parsed
langsmith-cli runs list --project my-project --limit 5
```

### 2. Always use `--output` for data extraction — never shell redirection

```bash
# ✅ CORRECT — atomic write, errors visible, non-zero exit on failure
langsmith-cli --json runs list --project my-project --output runs.jsonl
python3 -c "import json; runs = [json.loads(l) for l in open('runs.jsonl')]"

# ❌ WRONG — errors go to stderr silently, you get empty/corrupt file
langsmith-cli --json runs list --project my-project > runs.json

# ❌ WRONG — heredoc overrides pipe stdin, python3 reads empty stdin
langsmith-cli --json runs get <id> --fields outputs | python3 << 'EOF'
import sys, json; data = json.load(sys.stdin)  # stdin is EMPTY
EOF
```

Use `python3 -c "..."` (no heredoc) if you must pipe inline.

### 3. Cache-First Workflow — ALWAYS check cache before any API call

```
Step 1: langsmith-cli runs cache list
         ↓
Step 2: Is the project listed with recent data?
   YES → Use `runs cache grep` directly. Zero API calls. STOP.
   NO  → Tell user: "Project X is not in cache. Downloading in background."
         Run `langsmith-cli --json runs cache download ...` in background,
         poll TaskOutput(block=false) for progress, use cache grep when done.
```

**Red flags — STOP if you're about to:**
- Query the API (`runs list`, `--fetch N`) when the project is already cached
- Run `runs cache download` without first checking `runs cache list`
- Download a project already listed in `runs cache list`
- Run `runs cache download` **without `--json`** — Rich output is swallowed when captured to a file, leaving you with zero progress visibility
- Use `--fetch N` after a cache download — `--fetch` always hits the API, never the cache

**Background download + progress tracking:**
```bash
# ✅ CORRECT — --json emits {"event":"progress","project":"...","new_runs":N} to stderr per batch
langsmith-cli --json runs cache download --project "dev/my-project" --last 30d
# Run in background, poll TaskOutput(block=false), relay new_runs count to user
# Final stdout: {"event":"download_complete","total_new_runs":N}
```

### 4. Always use `--fields` to reduce token usage

```bash
langsmith-cli --json runs list --fields id,name,status,start_time
langsmith-cli --json runs get <id> --fields inputs,outputs,error
```

---

## Quick Command Reference

| Task | Command |
|------|---------|
| List recent runs | `langsmith-cli --json runs list --project <name> --limit 10 --fields id,name,status` |
| Get a single run | `langsmith-cli --json runs get <id> --fields inputs,outputs,error` |
| Get run + child outputs | `langsmith-cli --json runs get <id> --follow-children --fields id,name,inputs,outputs` |
| Get latest run | `langsmith-cli --json runs get-latest --project <name> --fields inputs,outputs` |
| Get latest error | `langsmith-cli --json runs get-latest --project <name> --failed --fields id,name,error` |
| Search run content | `langsmith-cli --json runs list --grep "pattern" --grep-in outputs --limit 20` |
| Search cached runs | `langsmith-cli runs cache grep "pattern" -E --grep-in outputs --project <name>` |
| Download cache | `langsmith-cli --json runs cache download --project <name> --last 7d` |
| List cache | `langsmith-cli runs cache list` |
| Discover cache schema | `langsmith-cli --json runs cache schema --project <name> --include outputs` |
| Analyze token costs | `langsmith-cli --json runs usage --from-cache --breakdown model --active-only` |
| List projects | `langsmith-cli --json projects list --name-pattern "dev/*" --fields name` |
| Count runs | `langsmith-cli --json runs list --project <name> --count` |
| Run stats | `langsmith-cli --json runs stats --project <name>` |
| List datasets | `langsmith-cli --json datasets list --fields id,name` |
| List prompts | `langsmith-cli --json prompts list --fields repo_handle,description` |
| List feedback for a run | `langsmith-cli --json feedback list --run-id <run-id>` |
| Create feedback | `langsmith-cli --json feedback create <run-id> --key correctness --score 0.9` |
| List annotation queues | `langsmith-cli --json annotation-queues list` |
| Get annotation queue | `langsmith-cli --json annotation-queues get <queue-id>` |
| View experiment results | `langsmith-cli --json experiments results <experiment-name>` |
| Open run in browser | Construct URL manually — see **LangSmith URLs** section below |

---

## 📚 When to Read Reference Files

When your task matches one of the sections below, **you MUST load that reference file before proceeding** — don't load them speculatively for unrelated tasks.

### → Read [references/runs.md](references/runs.md) when:
- You need the full flag list for `runs list`, `runs get`, `runs get-latest`, `runs search`, `runs sample`, `runs analyze`, `runs tags`, `runs fields`, `runs export`
- You need to use `--trace-filter`, `--tree-filter`, `--sort-by`, `--roots`, `--run-type`, `--tag`, `--model`, `--min-latency`, `--max-latency`
- You need to filter runs by metadata: `--metadata key=value` (supports wildcards `key=val*` and regex `key=/pattern/`)
- You need to paginate, export to files, or watch live runs

### → Read [references/search.md](references/search.md) when:
- You need to choose between `--query` (server-side, fast, first 250 chars) vs `--grep` (client-side, all content, regex)
- You need to write FQL filter expressions (`eq`, `gt`, `has`, `and`, `search`, `metadata_key`/`metadata_value`)
- You need to search by content with regex, Hebrew/Unicode characters, or multi-field patterns
- You need common search+filter patterns (find errors, filter by tag, combine filters)
- You want to avoid writing inline scripts — the CLI often handles it natively

### → Read [references/cost-analysis.md](references/cost-analysis.md) when:
- You need to analyze token usage or costs for a project or event window
- You need to break down costs by model, provider, or gateway
- You need to find missing model pricing and apply a pricing YAML
- You need hourly/daily cost distribution or cost attribution for a channel/community
- You need to use `runs usage`, `runs pricing`, `runs cache download` + `--from-cache`
- You need the Key Flags for offline analysis (`--from-cache`, `--group-by`, `--breakdown`, `--apply-pricing`)

### → Read [references/projects.md](references/projects.md) when:
- You need to create, update, or delete projects
- You need to filter projects by name pattern/regex across environments

### → Read [references/datasets.md](references/datasets.md) and [references/examples.md](references/examples.md) when:
- You need to create or manage evaluation datasets
- You need to add, update, or delete examples
- You need to push JSONL to a dataset or create examples from runs

### → Read [references/prompts.md](references/prompts.md) when:
- You need to pull, push, or version prompt templates
- You need to list prompt commits or compare versions

### → Use `feedback` commands when:
- You need to list, get, create, or delete feedback scores on runs
- Commands: `feedback list [--run-id <id>] [--key <key>] [--limit N]`, `feedback get <id>`, `feedback create <run-id> --key <key> [--score N] [--comment <str>]`, `feedback delete <id> [--confirm]`

### → Use `annotation-queues` commands when:
- You need to manage human review queues (list, create, update, delete)
- Commands: `annotation-queues list`, `annotation-queues get <id>`, `annotation-queues create <name> [--description <str>]`, `annotation-queues update <id> [--name <str>] [--description <str>]`, `annotation-queues delete <id> [--confirm]`

### → Use `experiments` commands when:
- You need to view run stats and feedback scores for a named experiment (project)
- Commands: `experiments results <experiment-name>`

### → Read [references/fql.md](references/fql.md) when:
- You need to write a complex `--filter` expression and want operator reference
- You need `metadata_key`/`metadata_value` filter syntax

### → Read [docs/examples.md](docs/examples.md) when:
- You want end-to-end workflow examples (debugging, dataset management, production monitoring)
- You want common patterns without having to piece together flags yourself
- You need to **search for recognized entities in extraction chain outputs** (e.g. find all runs where "Niklas" was recognized as a known entity in `extracted_entities`) — there's a complete recipe covering cache download, Python JSONL scanning, deduplication of sub-runs, and `llm_recognition` filtering

### → Read [references/cache-recipes.md](references/cache-recipes.md) when:
- You need to discover the nested structure of cached run data (inputs/outputs schema)
- You want to query cached JSONL data with Python one-liners or DuckDB SQL
- You need to extract, sort, or aggregate structured outputs from cached runs
- You need to find specific entities, values, or patterns in nested output fields

### → Read [references/troubleshooting.md](references/troubleshooting.md) when:
- CLI commands fail, return unexpected results, or produce authentication errors
- You see rate limit errors and want strategies to work around them

---

## LangSmith URLs

**`runs open` generates broken URLs.** Build trace URLs manually using the project's `id` and `tenant_id`:

```bash
# Step 1: Get org ID (tenant_id) and project ID
langsmith-cli --json projects get "dev/my-project" --fields id,tenant_id

# Step 2: Build the URL
# https://smith.langchain.com/o/{tenant_id}/projects/p/{project_id}?peek={run_id}&peeked_trace={trace_id}
```

**Example:**
```python
org_id = "b658ea18-0431-42c0-8d03-337d43fed8cf"   # tenant_id from projects get
proj_id = "730acc6c-ec97-4f08-915e-7d3f7f775300"  # id from projects get

url = f"https://smith.langchain.com/o/{org_id}/projects/p/{proj_id}?peek={run_id}&peeked_trace={trace_id}"
```

- `peek` = the specific **run** ID to open in the side panel
- `peeked_trace` = the **trace** (root run) ID it belongs to
- Both IDs come from your search results (`run.id` and `run.trace_id`)

---

## Key Flags Cheat Sheet

```bash
# Multi-project matching
--project-name-pattern "prd/*"    # wildcard
--project-name-regex "^(prd|stg)" # regex

# Time windows (combinable)
--since 2026-01-15 --before 2026-01-29
--last 7d
--since 2026-01-15 --last 14d     # forward window

# Content search
--query "text"                    # server-side, fast, first ~250 chars only
--grep "pattern" --grep-regex --grep-in inputs,outputs  # client-side, all content

# Metadata filter (server-side, supports wildcards and regex)
--metadata channel_id=Gigaverse_Daily_Standup*
--metadata channel_id=/^Gigaverse/

# Reduce output size
--fields id,name,status,start_time
--roots                           # root traces only (cleaner)
--limit 10 --fetch 500            # fetch 500 from API, return top 10 matches
```
