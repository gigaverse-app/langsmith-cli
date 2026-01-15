# Pipes to CLI Reference

**Quick Reference Guide: Converting Piped Commands to Native CLI Features**

This document provides side-by-side comparisons of common piping patterns (using `jq`, `grep`, nested commands) and their equivalent built-in CLI features. The CLI eliminates the need for external tools and makes commands more reliable and readable.

## Quick Reference Cheat Sheet

| What You Want | Old (Piped) | New (Native CLI) |
|--------------|-------------|------------------|
| **Case-insensitive search** | `\| grep -i "actions"` | `--name-regex "(?i)actions"` |
| **Multi-keyword OR** | `\| grep -E "word1\|word2\|word3"` | `--name-regex "(word1\|word2\|word3)"` |
| **Exclude single pattern** | `\| grep -v "smoke-test"` | `--exclude "smoke-test"` |
| **Exclude multiple patterns** | `\| grep -v "test1" \| grep -v "test2"` | `--exclude "test1" --exclude "test2"` |
| **Count results** | `\| jq '. \| length'` | `--count` |
| **Limit results** | `\| head -20` | `--limit 20` |
| **Get latest run** | `runs get $(runs list ... \| jq ...)` | `runs get-latest` |
| **Get run details** | `runs list \| jq \| while read id; runs get $id` | `runs list --fields id,inputs,outputs` (1 API call) |
| **Filter projects by prefix** | `\| grep "^prd/"` | `--name-regex "^prd/"` or `--name-pattern "prd/*"` |
| **Field selection** | `\| jq '{id, name}'` | `--fields id,name` |
| **Multi-project search** | `for project in ...; do ...; done` | `--project-name-pattern "prd/*"` |

## Table of Contents

- [Project Filtering](#project-filtering)
- [Real-World Examples](#real-world-examples)
- [Getting Latest Run](#getting-latest-run)
- [Finding Latest Errors](#finding-latest-errors)
- [Cross-Project Searches](#cross-project-searches)
- [Field Selection and Pruning](#field-selection-and-pruning)
- [Complex Filtering](#complex-filtering)

---

## Project Filtering

### Filter Projects by Substring

**❌ OLD (requires piping):**
```bash
langsmith-cli --json projects list | jq -r '.[].name' | grep "prod"
```

**✅ NEW (built-in filter):**
```bash
langsmith-cli --json projects list --name "prod" --fields name
```

**Explanation:**
- `--name "prod"`: Substring/contains match (finds "production", "dev-prod", etc.)
- `--fields name`: Only return project names (reduces context usage)

---

### Filter Projects by Regex Pattern

**❌ OLD (requires piping):**
```bash
langsmith-cli --json projects list | jq -r '.[].name' | grep -E "^(prd|stg)/"
```

**✅ NEW (built-in regex):**
```bash
langsmith-cli --json projects list --name-regex "^(prd|stg)/" --fields name
```

**Explanation:**
- `--name-regex "^(prd|stg)/"`: Full regex support (anchors, groups, character classes)
- Matches: "prd/api", "stg/worker", but not "dev/prd-test"

---

### Filter Projects by Wildcard Pattern

**❌ OLD (requires shell expansion or piping):**
```bash
langsmith-cli --json projects list | jq -r '.[].name' | grep "dev/.*"
```

**✅ NEW (built-in wildcards):**
```bash
langsmith-cli --json projects list --name-pattern "dev/*" --fields name
```

**Explanation:**
- `--name-pattern "dev/*"`: Wildcard pattern using `*` (any chars) and `?` (single char)
- Matches: "dev/api", "dev/web", "dev/worker"

---

### Filter Projects by Exact Match

**❌ OLD (requires piping with exact match):**
```bash
langsmith-cli --json projects list | jq -r '.[] | select(.name == "production-api") | .name'
```

**✅ NEW (exact match filter):**
```bash
langsmith-cli --json projects list --name-exact "production-api" --fields name
```

**Explanation:**
- `--name-exact "production-api"`: Only matches exactly this project name
- Returns empty list if project doesn't exist

---

## Real-World Examples

These are actual patterns from production usage showing how to eliminate piping.

### Example 1: Find Projects Matching Multiple Keywords (Case-Insensitive)

**❌ OLD (requires piping with grep):**
```bash
langsmith-cli --json projects list --fields name | jq -r '.[].name' | grep -i actions
```

**✅ NEW (built-in case-insensitive regex):**
```bash
langsmith-cli --json projects list --name-regex "(?i)actions" --fields name
```

**Or for multiple keywords:**

**❌ OLD (requires piping with grep -E):**
```bash
langsmith-cli --json projects list --fields name | jq -r '.[].name' | grep -E "action|simulate|reply" | head -20
```

**✅ NEW (built-in regex with alternation):**
```bash
langsmith-cli --json projects list --name-regex "(action|simulate|reply)" --fields name --limit 20
```

**Explanation:**
- Use regex alternation `(keyword1|keyword2|keyword3)` for OR logic
- `(?i)` makes regex case-insensitive
- `--limit 20` replaces `| head -20`

---

### Example 2: Find Stream/Recap Projects (Multiple Patterns)

**❌ OLD (requires piping with grep -iE):**
```bash
langsmith-cli --json projects list --fields name | jq -r '.[].name' | grep -iE "(summar|recap|transcript|stream)" | head -20
```

**✅ NEW (case-insensitive regex):**
```bash
langsmith-cli --json projects list --name-regex "(?i)(summar|recap|transcript|stream)" --fields name --limit 20
```

**Explanation:**
- `(?i)` at the start makes the entire regex case-insensitive
- Matches: "stream", "STREAM", "Stream", "summary", "Summarize", etc.

---

### Example 3: Get Latest Run Inputs (Nested Commands)

**❌ OLD (requires nested command substitution with jq):**
```bash
langsmith-cli --json runs get $(
  langsmith-cli --json runs list --project "prd/chat_message_quality_service" --limit 1 --fields id --roots |
  jq -r '.[0].id'
) --fields inputs,outputs
```

**✅ NEW (single command):**
```bash
langsmith-cli --json runs get-latest --project "prd/chat_message_quality_service" --roots --fields inputs,outputs
```

**Explanation:**
- Eliminates nested command substitution `$(...)`
- No need for `jq` to extract ID
- Single API-efficient operation

---

### Example 4: Exclude Projects by Pattern

**❌ OLD (requires piping with grep -v):**
```bash
langsmith-cli --json projects list --fields name | jq -r '.[].name' | grep -v "smoke-test" | grep -v "metamind" | head -50
```

**✅ NEW (universal --exclude flag):**
```bash
langsmith-cli --json projects list --exclude "smoke-test" --exclude "metamind" --fields name --limit 50
```

**Alternative (regex for complex patterns):**
```bash
langsmith-cli --json projects list --name-regex "^(?!.*(smoke-test|metamind)).*" --fields name --limit 50
```

**Explanation:**
- `--exclude` flag can be repeated for multiple exclusion patterns
- Simple substring matching (case-sensitive)
- Available on ALL list commands (projects, runs, datasets, examples, prompts)
- For complex patterns, negative lookahead regex is still available

---

### Example 5: Find Production Projects (Specific Prefix)

**❌ OLD (requires piping):**
```bash
langsmith-cli --json projects list --fields name | jq -r '.[].name' | grep -E "^prd/"
```

**✅ NEW (anchored regex):**
```bash
langsmith-cli --json projects list --name-regex "^prd/" --fields name
```

**Or using wildcard pattern:**
```bash
langsmith-cli --json projects list --name-pattern "prd/*" --fields name
```

**Explanation:**
- `--name-regex "^prd/"`: Matches projects starting with "prd/"
- `--name-pattern "prd/*"`: Wildcard equivalent (easier syntax)
- Both return: "prd/api", "prd/worker", etc.

---

### Example 6: Filter Projects by Path Pattern (Multiple Levels)

**❌ OLD (requires piping with grep -E):**
```bash
langsmith-cli --json projects list --fields name | jq -r '.[].name' | grep -E "/(stream|recap)"
```

**✅ NEW (regex with path separator):**
```bash
langsmith-cli --json projects list --name-regex "/(stream|recap)" --fields name
```

**Explanation:**
- Matches any project with "/stream" or "/recap" in the name
- Examples: "prd/stream_service", "local/recap_worker"

---

### Example 7: Fetch All Data in One Call (Avoid N+1 API Calls)

**❌ OLD (N+1 API calls - very slow):**
```bash
# First API call to get IDs
langsmith-cli --json runs list --project "dev/task-factcheck-summarize" --limit 10 --fields id --roots | \
  jq -r '.[].id' | \
  while read run_id; do
    # N additional API calls - one per run!
    langsmith-cli --json runs get "$run_id" --fields inputs
  done
```

**✅ NEW (1 API call - much faster):**
```bash
# Get ALL data you need in ONE API call using --fields
langsmith-cli --json runs list --project "dev/task-factcheck-summarize" --limit 10 --fields id,inputs,outputs --roots
```

**Explanation:**
- **Key insight:** `runs list` supports `--fields` for ANY field from the run object
- Fetch `inputs`, `outputs`, `error`, or any other field directly with `runs list`
- **Massive performance improvement:** 1 API call instead of N+1 calls
- No need to loop through IDs and call `runs get` separately
- The `--fields` option works on both `runs list` and `runs get`

**Fields you can request:**
- Basic: `id`, `name`, `status`, `run_type`, `start_time`, `end_time`
- Content: `inputs`, `outputs`, `error`
- Metadata: `tags`, `metadata`, `extra`
- Performance: `latency`, `total_tokens`, `prompt_tokens`, `completion_tokens`

---

### Example 8: Count Results (Universal --count Flag)

**❌ OLD (requires piping through jq):**
```bash
langsmith-cli --json projects list --name-pattern "prd/*" | jq '. | length'
```

**✅ NEW (universal --count flag):**
```bash
langsmith-cli --json projects list --name-pattern "prd/*" --count
```

**Explanation:**
- `--count` flag returns only the count as a single integer
- Available on ALL list commands (projects, runs, datasets, examples, prompts)
- No JSON parsing needed - just a number
- Combines with all other filters (--name-pattern, --exclude, --status, etc.)

**More examples:**
```bash
# Count all failed runs in a project
langsmith-cli --json runs list --project my-project --failed --count

# Count datasets excluding test datasets
langsmith-cli --json datasets list --exclude "test" --count

# Count production runs with specific tag
langsmith-cli --json runs list --project-name-pattern "prd/*" --tag important --count
```

---

## Getting Latest Run

### Get Latest Run from a Project

**❌ OLD (requires nested commands with jq):**
```bash
langsmith-cli --json runs get $(
  langsmith-cli --json runs list --project my-project --limit 1 --fields id --roots |
  jq -r '.[0].id'
) --fields inputs,outputs
```

**✅ NEW (single command):**
```bash
langsmith-cli --json runs get-latest --project my-project --roots --fields inputs,outputs
```

**Explanation:**
- `runs get-latest`: New command that combines list + get in one operation
- No need for nested command substitution or jq extraction
- Searches until it finds a run (useful with project patterns)

---

### Get Latest Successful Run

**❌ OLD (requires multiple pipes):**
```bash
langsmith-cli --json runs list --project my-project --status success --limit 1 --roots | \
  jq -r '.[0].id' | \
  xargs langsmith-cli --json runs get --fields inputs,outputs
```

**✅ NEW (built-in status filter):**
```bash
langsmith-cli --json runs get-latest --project my-project --succeeded --roots --fields inputs,outputs
```

**Explanation:**
- `--succeeded`: Shorthand for `--status success`
- Also supports `--failed` as shorthand for `--status error`

---

### Get Latest Run from Last Hour

**❌ OLD (requires date calculation and piping):**
```bash
langsmith-cli --json runs list --project my-project --since "$(date -u -d '1 hour ago' +%Y-%m-%dT%H:%M:%SZ)" --limit 1 | \
  jq -r '.[0].id' | \
  xargs langsmith-cli --json runs get
```

**✅ NEW (natural time filters):**
```bash
langsmith-cli --json runs get-latest --project my-project --recent
```

**Or with custom duration:**
```bash
langsmith-cli --json runs get-latest --project my-project --last "1h"
```

**Explanation:**
- `--recent`: Smart flag for runs from last hour
- `--last "1h"`: Custom duration (supports "1h", "24h", "7d", "30m")
- `--today`: Runs from today (midnight to now)

---

## Finding Latest Errors

### Get Latest Error from Production Projects

**❌ OLD (requires loop with pipes):**
```bash
for project in $(langsmith-cli --json projects list | jq -r '.[].name' | grep "prd/"); do
  langsmith-cli --json runs list --project "$project" --failed --limit 1
done | jq -s '.[0]'
```

**✅ NEW (single command with project pattern):**
```bash
langsmith-cli --json runs get-latest --project-name-pattern "prd/*" --failed --fields id,name,error
```

**Explanation:**
- `--project-name-pattern "prd/*"`: Searches across all matching projects
- `--failed`: Only error runs
- Searches projects in order until it finds an error
- No shell loops or array slicing needed

---

### Get Latest Error with Specific Tag

**❌ OLD (requires complex filtering):**
```bash
langsmith-cli --json runs list --project my-project --status error --limit 100 | \
  jq -r '.[] | select(.tags | contains(["critical"])) | .id' | \
  head -1 | \
  xargs langsmith-cli --json runs get
```

**✅ NEW (built-in tag filtering):**
```bash
langsmith-cli --json runs get-latest --project my-project --failed --tag critical
```

**Explanation:**
- `--tag critical`: Filter by tag (can specify multiple: `--tag critical --tag prod`)
- Combined with `--failed` for error filtering
- No need to over-fetch and filter client-side

---

## Cross-Project Searches

### Find Latest Run Across Multiple Projects (Regex)

**❌ OLD (requires loop with pipes):**
```bash
for project in $(langsmith-cli --json projects list | jq -r '.[].name' | grep -E "^(prd|stg)/"); do
  langsmith-cli --json runs list --project "$project" --limit 1 --roots
done | jq -s 'sort_by(.start_time) | reverse | .[0]'
```

**✅ NEW (built-in project regex + get-latest):**
```bash
langsmith-cli --json runs get-latest --project-name-regex "^(prd|stg)/" --roots
```

**Explanation:**
- `--project-name-regex "^(prd|stg)/"`: Searches all matching projects
- Searches in order by project name, returns first match
- No shell loops or manual sorting

---

### Find Latest Successful Run from Production

**❌ OLD (requires nested loops):**
```bash
for project in $(langsmith-cli --json projects list | jq -r '.[].name' | grep "^prd/"); do
  langsmith-cli --json runs list --project "$project" --status success --limit 1 --roots
done | jq -s 'sort_by(.start_time) | reverse | .[0]' | jq -r '.id' | \
  xargs langsmith-cli --json runs get --fields inputs,outputs
```

**✅ NEW (single command with all filters):**
```bash
langsmith-cli --json runs get-latest --project-name-pattern "prd/*" --succeeded --roots --fields inputs,outputs
```

**Explanation:**
- Combines project filtering, status filtering, and field selection
- No nested commands, loops, or array manipulation
- Single API-efficient operation

---

## Field Selection and Pruning

### Get Run with Only Specific Fields

**❌ OLD (fetch full object, prune with jq):**
```bash
langsmith-cli --json runs get <run-id> | jq '{id, name, inputs, outputs}'
```

**✅ NEW (server-side field filtering):**
```bash
langsmith-cli --json runs get <run-id> --fields id,name,inputs,outputs
```

**Explanation:**
- `--fields`: Server-side filtering reduces network transfer and context usage (~90% reduction)
- Works on: `runs get`, `runs get-latest`, `runs list`, `datasets list/get`, `examples list/get`
- Much more efficient than client-side jq filtering

---

### List Runs with Minimal Fields

**❌ OLD (fetch full data, filter client-side):**
```bash
langsmith-cli --json runs list --project my-project --limit 10 | jq '[.[] | {id, name, status}]'
```

**✅ NEW (specify fields upfront):**
```bash
langsmith-cli --json runs list --project my-project --limit 10 --fields id,name,status
```

**Explanation:**
- Reduces payload size from ~20KB per run to ~1KB
- Critical for AI agent contexts (token usage)
- Same output format, but efficient at the source

---

## Complex Filtering

### Find Slow Runs from Today

**❌ OLD (requires date calculation and latency filtering):**
```bash
langsmith-cli --json runs list --project my-project \
  --since "$(date -u +%Y-%m-%dT00:00:00Z)" \
  --filter 'gt(latency, "5s")' \
  --limit 10
```

**✅ NEW (smart flags):**
```bash
langsmith-cli --json runs list --project my-project --today --slow --limit 10
```

**Or with custom latency:**
```bash
langsmith-cli --json runs list --project my-project --today --min-latency "5s" --limit 10
```

**Explanation:**
- `--today`: Smart flag for today's date range
- `--slow`: Shorthand for latency > 5s
- `--min-latency "5s"`: Custom latency threshold (supports "500ms", "2s", "10s")

---

### Find Runs with Specific Model

**❌ OLD (requires FQL knowledge and manual filtering):**
```bash
langsmith-cli --json runs list --project my-project \
  --filter 'or(has(extra.invocation_params.model_name, "gpt-4"), has(extra.metadata.ls_model_name, "gpt-4"))' \
  --limit 10
```

**✅ NEW (smart model filter):**
```bash
langsmith-cli --json runs list --project my-project --model "gpt-4" --limit 10
```

**Explanation:**
- `--model "gpt-4"`: Searches both invocation_params and metadata automatically
- Supports partial matching: `--model "gpt"` matches "gpt-4", "gpt-3.5-turbo", etc.
- No need to understand internal run structure

---

### Combine Multiple Smart Filters

**❌ OLD (requires complex FQL strings):**
```bash
langsmith-cli --json runs list --project my-project \
  --filter 'and(gt(latency, "5s"), has(tags, "production"), has(tags, "critical"), gte(start_time, "2024-01-01T00:00:00Z"))' \
  --status error \
  --limit 10
```

**✅ NEW (readable flags):**
```bash
langsmith-cli --json runs list --project my-project \
  --slow \
  --tag production \
  --tag critical \
  --since "2024-01-01" \
  --failed \
  --limit 10
```

**Explanation:**
- Smart flags are composed with AND logic
- Much more readable and maintainable
- Still supports custom `--filter` for advanced cases

---

## Summary: When to Use Each Feature

| Old Pattern | New CLI Feature | When to Use |
|------------|----------------|-------------|
| `\| jq -r '.[].name' \| grep "pattern"` | `--name-regex "pattern"` | Project name filtering |
| `\| jq -r '.[].name' \| grep -i "word"` | `--name-regex "(?i)word"` | Case-insensitive project search |
| `\| grep -E "word1\|word2\|word3"` | `--name-regex "(word1\|word2\|word3)"` | Multi-keyword OR search |
| `\| grep -v "exclude"` | `--name-regex "^(?!.*exclude).*"` | Exclude projects by pattern |
| `\| head -N` | `--limit N` | Limit results |
| `\| jq -r '.[].id' \| while read id; runs get $id` | `runs list --fields id,inputs,outputs` | Fetch all data in one call |
| `$(...jq -r '.[0].id')` in nested command | `runs get-latest` | Get latest run (no nesting) |
| `\| jq -r '.[0].id' \| xargs langsmith-cli runs get` | `runs get-latest` | Get latest run from project |
| `for project in ... do ... done` | `--project-name-pattern "glob"` | Search across multiple projects |
| `\| jq '{id, name}'` | `--fields id,name` | Reduce output size |
| `--filter 'gt(latency, "5s")'` | `--slow` or `--min-latency "5s"` | Find slow runs |
| `--since "$(date ...)"` | `--today`, `--recent`, `--last "1h"` | Time-based filtering |
| Multiple filter clauses | Multiple smart flags | Readable combinations |

---

## Best Practices

1. **Always use `--json` as first flag** when using CLI programmatically:
   ```bash
   langsmith-cli --json runs get-latest --project X
   ```

2. **Always use `--fields` to reduce context** (especially for AI agents):
   ```bash
   langsmith-cli --json runs get-latest --project X --fields id,name,error
   ```

3. **Prefer smart flags over FQL** for common cases:
   ```bash
   # Good - readable
   --slow --failed --tag prod

   # Less readable (but valid)
   --filter 'and(gt(latency, "5s"), eq(status, "error"), has(tags, "prod"))'
   ```

4. **Use project patterns for multi-project searches**:
   ```bash
   # Search across all production projects
   --project-name-pattern "prd/*"

   # Or with regex
   --project-name-regex "^prod-.*-v[0-9]+$"
   ```

5. **Use `runs get-latest` instead of nested commands**:
   ```bash
   # Good - single command
   langsmith-cli --json runs get-latest --project X --fields inputs,outputs

   # Bad - requires nested command + jq extraction
   langsmith-cli --json runs get $(langsmith-cli --json runs list ... | jq -r '.[0].id')
   ```

6. **Fetch all data in one API call using `--fields`**:
   ```bash
   # Bad - N+1 API calls (slow, inefficient)
   for id in $(langsmith-cli --json runs list --fields id | jq -r '.[].id'); do
     langsmith-cli --json runs get $id --fields inputs
   done

   # Good - Single API call with all needed fields
   langsmith-cli --json runs list --fields id,inputs,outputs --roots
   ```

---

## Migration Checklist

When converting piped commands to native CLI features:

- [ ] Replace `jq` extraction with `runs get-latest`
- [ ] Replace `grep` filtering with `--name-regex` or `--name-pattern`
- [ ] Replace `grep -i` (case-insensitive) with `--name-regex "(?i)pattern"`
- [ ] Replace `grep -E "word1|word2"` with `--name-regex "(word1|word2)"`
- [ ] Replace `grep -v "exclude"` with `--name-regex "^(?!.*exclude).*"`
- [ ] Replace `| head -N` with `--limit N`
- [ ] Replace `| jq -r '.[].id' | while read` loops with `runs list --fields id,inputs,outputs`
- [ ] Replace nested `$(...)` commands with `runs get-latest`
- [ ] Replace `for project in ...` loops with `--project-name-pattern`
- [ ] Add `--fields` for context efficiency - works on `runs list`, `runs get`, `runs get-latest`
- [ ] Use smart flags (`--slow`, `--today`, `--failed`) instead of FQL
- [ ] Use `--json` as first flag (not at the end)
- [ ] Remove `jq` field filtering (use `--fields` instead for server-side filtering)
- [ ] Replace date calculations with `--today`, `--recent`, or `--last`
- [ ] Avoid N+1 API calls - fetch all data in one call with `runs list --fields`
