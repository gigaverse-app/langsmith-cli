# Cost & Token Analysis Reference

## Key Flags for Offline Analysis

| Flag | Commands | What It Does |
|------|----------|--------------|
| `--from-cache` | `runs usage`, `runs pricing` | Read from local JSONL cache (fast, no API) |
| `--group-by metadata:<field>` | `runs usage`, `runs analyze` | Group results by metadata or tag field |
| `--breakdown model` | `runs usage` | Add model dimension to aggregation |
| `--breakdown provider` | `runs usage` | Add provider (Google, OpenAI, Anthropic, etc.) |
| `--breakdown gateway` | `runs usage` | Add gateway (groq, cerebras, openai, google_genai, etc.) |
| `--breakdown project` | `runs usage` | Add project dimension |
| `--apply-pricing <file>` | `runs usage` | YAML pricing file to fill missing costs |
| `--format yaml` | `runs pricing` | Output pricing as YAML for `--apply-pricing` |
| `--interval hour\|day` | `runs usage` | Time bucket size for distribution |
| `--active-only` | `runs usage` | Only show time buckets with activity |
| `--tag <tag>` | `runs usage`, `runs pricing` | Filter by tag (repeatable, AND logic) |
| `--metadata key=value` | `runs usage` | Filter by metadata (wildcards/regex supported) |

## Recipe 1: Basic Cost Breakdown by Model

```bash
# Ensure cache is fresh first
langsmith-cli --json runs cache download --project-name-pattern "prd/*" --last 7d

# Analyze costs broken down by model, provider, gateway
langsmith-cli --json runs usage \
  --from-cache \
  --project-name-pattern "prd/*" \
  --breakdown model --breakdown provider --breakdown gateway \
  --active-only --last 7d
```

## Recipe 2: Cost for a Specific Channel/Event

```bash
# Lower bound: only runs tagged with this channel
langsmith-cli --json runs usage \
  --from-cache \
  --project-name-pattern "prd/*" \
  --metadata channel_id=chat:MyChannel-abc123 \
  --since "2026-02-19T14:00:00Z" --before "2026-02-19T20:30:00Z" \
  --breakdown model --breakdown project \
  --active-only

# Upper bound: all runs in the time window (to estimate unattributed cost)
langsmith-cli --json runs usage \
  --from-cache \
  --project-name-pattern "prd/*" \
  --since "2026-02-19T14:00:00Z" --before "2026-02-19T20:30:00Z" \
  --breakdown model --breakdown project \
  --active-only
```

**Interpreting bounds:**
- `run_count` attributed / `run_count` total = coverage % per project
- Projects at 0% run shared pipelines (can't assign cost to single channel)
- True cost is between attributed (lower) and all (upper)

## Recipe 3: Hourly Activity Distribution

```bash
# Spot broadcast windows, peak usage hours
langsmith-cli --json runs usage \
  --from-cache \
  --project-name-pattern "prd/*" \
  --interval hour \
  --active-only \
  --last 3d
```

## Recipe 4: Fix Missing Model Pricing

```bash
# Step 1: Generate pricing YAML (auto-fills from LangSmith + OpenRouter)
langsmith-cli runs pricing \
  --from-cache --project-name-pattern "prd/*" \
  --format yaml > pricing.yaml

# Step 2: Look up missing prices (showing 0.0) from provider pages:
# Groq:        https://groq.com/pricing
# Cerebras:    https://www.cerebras.ai/pricing
# Perplexity:  https://docs.perplexity.ai/guides/pricing
# OpenRouter:  https://openrouter.ai/models
# xAI:         https://docs.x.ai/docs/models
# Edit pricing.yaml: set input_per_million and output_per_million

# Step 3: Apply filled pricing
langsmith-cli --json runs usage \
  --from-cache \
  --project-name-pattern "prd/*" \
  --apply-pricing pricing.yaml \
  --breakdown provider --active-only
```

## Recipe 5: Group by Community / Channel

```bash
langsmith-cli runs usage \
  --from-cache \
  --group-by metadata:community_name \
  --breakdown project \
  --interval day
```

## Recipe 6: Aggregate Metrics by Tag

```bash
langsmith-cli --json runs analyze \
  --project my-project \
  --group-by tag:length_category \
  --metrics count,error_rate,p95_latency,total_tokens,avg_cost \
  --since "2026-02-17" --before "2026-02-20"
```

## Tag Format Notes

Tags are plain strings. Common conventions:
- Simple: `"prod"`, `"critical"`, `"v2"`
- Key-value (colon-separated): `"env:prod"`, `"channel_id:room-A"`

`--metadata key=value` checks in order:
1. Direct metadata match (`run.extra.metadata[key] == value`)
2. Tag match (`value` in `run.tags`)
3. Key-value tag match (`"key:value"` in `run.tags`)
4. Trace context fallback (parent/root run metadata)

This means `--metadata` works even when data is stored as tags instead of metadata fields.
