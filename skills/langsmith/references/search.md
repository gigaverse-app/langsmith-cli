# Content Search & Filtering Reference

## --query vs --grep: When to Use Which

| Use `--query` when | Use `--grep` when |
|--------------------|-------------------|
| Content is short (< 250 chars) | Content is long (inputs/outputs > 250 chars) |
| Simple substring match is enough | You need regex patterns |
| Server-side speed matters | You need case-insensitive search |
| | You need to restrict to specific fields |

`--query` is server-side (fast, limited to first ~250 indexed chars).
`--grep` is client-side (downloads runs first, searches all content, supports regex).

## Content Search Examples

```bash
# Server-side text search (fast, first ~250 chars)
langsmith-cli --json runs list --project "prd/factcheck" --query "druze" --fields id,inputs

# Client-side substring search (unlimited content)
langsmith-cli --json runs list --project "prd/community_news" --grep "druze" --fields id,inputs

# Case-insensitive
langsmith-cli --json runs list --grep "druze" --grep-ignore-case --fields id,inputs

# Search only specific fields
langsmith-cli --json runs list --grep "error" --grep-in error,outputs --fields id,name,error

# Regex: find Hebrew characters
langsmith-cli --json runs list --grep "[\u0590-\u05FF]" --grep-regex --grep-in inputs --fields id,inputs

# Combined with other filters
langsmith-cli --json runs list --project "prd/*" --grep "pattern" --succeeded --roots --output results.jsonl
```

## Cache Search (Preferred When Cache Exists)

```bash
# Search cached runs — instant, no API calls
langsmith-cli runs cache grep "Jacob|Niklas" -E --grep-in outputs --project "dev/namedrop_service"

# Case-insensitive cache search
langsmith-cli runs cache grep "error" -i --project my-project --limit 50

# Count matches only
langsmith-cli runs cache grep "pattern" --count --project my-project

# With time filter
langsmith-cli runs cache grep "pattern" --project my-project --since 7d
```

## FQL Filter Expressions

FQL is used in `--filter` on `runs list` and `runs cache download`.

### Operators

| Operator | Example |
|----------|---------|
| `eq(field, value)` | `eq(name, "extractor")` |
| `neq(field, value)` | `neq(error, null)` |
| `gt(field, value)` | `gt(latency, "5s")` |
| `gte`, `lt`, `lte` | `lte(total_tokens, 5000)` |
| `has(tags, value)` | `has(tags, "production")` |
| `search("text")` | `search("database connection")` |
| `and(...)` | `and(neq(error, null), gt(latency, "5s"))` |
| `or(...)` | `or(eq(name, "a"), eq(name, "b"))` |
| `not(...)` | `not(has(tags, "test"))` |
| `eq(metadata_key, k)` + `eq(metadata_value, v)` | Filter by metadata field |

### FQL Examples

```bash
# Errored runs
--filter 'neq(error, null)'

# Slow runs
--filter 'gt(latency, "5s")'

# Specific run name
--filter 'eq(name, "task-name-extraction-chain")'

# Tag filter
--filter 'has(tags, "namedrop_service")'

# Metadata filter (exact match only — no wildcards in FQL)
--filter 'and(eq(metadata_key, "channel_id"), eq(metadata_value, "Gigaverse_Daily_Standup-abc123"))'

# Combined
--filter 'and(neq(error, null), gt(latency, "10s"), gt(total_tokens, 5000))'

# Full-text search (first ~250 chars)
--filter 'search("Jacob")'
```

### Metadata Filtering (Better Alternative to FQL)

For `runs list` and `runs usage`, use `--metadata` instead of FQL — it supports wildcards and regex:

```bash
# Exact match
--metadata channel_id=Gigaverse_Daily_Standup-abc123

# Wildcard — matches all sessions of this channel
--metadata channel_id=Gigaverse_Daily_Standup*

# Regex
--metadata channel_id=/^Gigaverse_Daily_Standup/

# Multiple metadata filters (AND logic)
--metadata channel_id=Gigaverse* --metadata run_name=namedrop*
```

Note: `--metadata` is NOT available on `runs cache download` — only `--filter` (FQL, exact match only).

## Avoid Writing Inline Scripts — Use CLI Commands Instead

Before writing a Python script to process results, check if the CLI already handles it:

| You were about to write... | Use this instead |
|---------------------------|-----------------|
| Loop to sum tokens | `runs usage --breakdown model` |
| Group by metadata | `runs usage --group-by metadata:channel_id` |
| Hourly distribution | `runs usage --interval hour` |
| Filter by metadata | `--metadata key=value*` |
| Filter by content | `--grep "pattern" --grep-regex` |
| Get only roots | `--roots` |
| Get latest matching run | `runs get-latest --failed` |

See [docs/examples.md](../docs/examples.md) for complete workflow recipes.
