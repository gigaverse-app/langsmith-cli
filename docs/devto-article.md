---
title: "I Replaced My LangSmith MCP Server with a CLI That Only Loads When You Need It"
published: false
description: "How langsmith-cli gives you 100% MCP parity, 96% less context per query, and features the MCP server doesn't have — all in a single pip install."
tags: langsmith, llmops, cli, claude
cover_image:
---

If you're using LangSmith with Claude Code (or any AI coding agent), you're probably running the official MCP server. It works. But every session, it injects **~5,000 tokens** of tool schemas into your context window — whether you touch LangSmith or not.

I built [langsmith-cli](https://github.com/gigaverse-app/langsmith-cli) to fix that. It's a standalone CLI *and* a Claude Code plugin that replaces the always-on MCP server with an **on-demand skill** that only loads when your agent actually needs to talk to LangSmith.

And it does more than the MCP server does.

## The Problem with MCP Servers

MCP servers are always-on. The moment your agent session starts, every tool definition gets loaded into context. For LangSmith's MCP server, that's 66 parameters across multiple tools — around 5,000 tokens of JSON schema sitting in your context window whether you ever query a trace or not.

For agents that need to do many things — write code, run tests, debug, *and occasionally* check LangSmith — this is wasteful. Context is your agent's working memory. Every token of schema is a token not available for reasoning.

## The Fix: On-Demand Skills Instead of Always-On Schemas

`langsmith-cli` takes a different approach. Instead of an MCP server that injects schemas at session start, it's a CLI tool with a skill file that **only loads when the agent actually invokes it**:

```bash
# Install the CLI
uv tool install langsmith-cli

# Add as Claude Code plugin
claude plugin marketplace add gigaverse-app/langsmith-cli
claude plugin install langsmith-cli@langsmith-cli
```

Sessions that never touch LangSmith pay **zero context tokens**. When the agent *does* need observability data, it invokes the skill and gets a comprehensive reference for the full CLI — every command, every flag, with usage patterns and examples. Then it runs shell commands:

```bash
# Get the latest failed run with only the fields you need
langsmith-cli --json runs get-latest --project my-app \
  --failed --fields id,name,error
```

No always-on server. No startup schema tax. The skill loads on-demand, and `--fields` keeps the *response* data lean too.

## 96% Token Reduction with `--fields`

This is the feature that matters most for agents. A typical LangSmith run object is **20KB** — easily 1,000+ tokens. With `--fields`, you get only what you asked for:

```bash
# Full run object: ~1000 tokens
langsmith-cli --json runs get abc-123

# Just what you need: ~40 tokens
langsmith-cli --json runs get abc-123 --fields name,status,error
```

`--fields` works on every list and get command: runs, projects, datasets, examples, prompts. Your agent stays lean.

## Built for Two Audiences

Most developer tools pick one audience. `langsmith-cli` serves both:

**For humans** — rich terminal tables with color-coded statuses, smart column truncation, syntax highlighting:

```bash
langsmith-cli runs list --project my-app --status error --last 24h
```

```
┏━━━━━━━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━━━━━┓
┃ Name         ┃ Status     ┃ Tokens ┃ Latency  ┃ Error       ┃
┡━━━━━━━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━━━━━┩
│ extractor    │ error      │ 2,340  │ 3.2s     │ Rate limit  │
│ classifier   │ error      │ 1,102  │ 12.4s    │ Timeout     │
└──────────────┴────────────┴────────┴──────────┴─────────────┘
```

**For agents** — add `--json` as the first flag and everything switches: strict JSON to stdout, diagnostics to stderr, zero formatting noise:

```bash
langsmith-cli --json runs list --project my-app --status error --limit 5
```

One flag. Two completely different UX modes.

## Features the MCP Server Doesn't Have

`langsmith-cli` has 100% parity with the official MCP server (all 66 parameters mapped). But it also has features the MCP server can't offer:

### Live Monitoring with `runs watch`

A real-time streaming dashboard in your terminal:

```bash
langsmith-cli runs watch --project my-app
```

### One-Command Debugging with `runs get-latest`

No more `list | jq | get` pipelines:

```bash
# Before: three commands piped together
langsmith-cli --json runs list --project X --limit 1 \
  | jq -r '.[0].id' \
  | xargs langsmith-cli --json runs get

# After: one command
langsmith-cli --json runs get-latest --project X --fields inputs,outputs,error
```

### Stratified Sampling with `runs sample`

Build statistically sound eval datasets:

```bash
langsmith-cli runs sample \
  --stratify-by tag:length,tag:content_type \
  --dimension-values "short|long,news|gaming" \
  --samples-per-combination 5 \
  --output eval_samples.jsonl
```

### Aggregate Analytics with `runs analyze`

Group-by metrics without leaving the terminal:

```bash
langsmith-cli --json runs analyze \
  --group-by tag:model \
  --metrics count,error_rate,p50_latency,avg_cost
```

### Schema Discovery with `runs fields` / `runs describe`

Don't know what fields your runs have? Discover them:

```bash
langsmith-cli --json runs fields --include inputs,outputs
# Returns field paths, types, presence rates, even language distribution
```

### Tag & Metadata Discovery

```bash
langsmith-cli runs tags --project my-app
langsmith-cli runs metadata-keys --project my-app
```

### Bulk Export with Pattern Filenames

```bash
langsmith-cli runs export ./traces \
  --project my-app --roots --limit 1000 \
  --filename-pattern "{name}-{run_id}"
```

### Production Run to Eval Example in One Command

```bash
langsmith-cli --json examples from-run <run-id> --dataset my-eval-set
```

## Smart Filtering That Translates to FQL

Nobody wants to write raw Filter Query Language. The CLI translates human-friendly flags automatically:

```bash
# These flags...
langsmith-cli runs list --tag summarizer --failed --last 24h --slow

# ...become this FQL:
# and(has(tags, "summarizer"), eq(error, true),
#     gt(start_time, "2026-03-03T..."), gt(latency, "5s"))
```

Time presets like `--recent` (last hour), `--today`, `--last 7d`, and `--since 2026-01-01` all work. Content search with `--grep` supports regex and field-specific matching. Everything composes.

## What's New in v0.4.0

The v0.4.0 release focused on type safety and code quality:

- **Zero pyright errors** — every function has proper type annotations. `client: langsmith.Client`, not `client: Any`. Return types are real SDK Pydantic models, not `object`.
- **`datasets delete`** command with confirmation prompts and JSON mode support
- **Improved error handling** across prompts and runs commands using specific SDK exception types (`LangSmithNotFoundError`, `LangSmithConflictError`) instead of broad `except Exception`
- **702 unit tests** passing with real Pydantic model instances (no MagicMock for test data)

## Getting Started

```bash
# Install
uv tool install langsmith-cli
# or: pip install langsmith-cli

# Authenticate
export LANGSMITH_API_KEY="lsv2_..."
# or: langsmith-cli auth login

# Start exploring
langsmith-cli runs list --project my-app --last 24h
langsmith-cli --json runs get-latest --failed --fields name,error
```

If you're using Claude Code, add the plugin for the best agent experience:

```bash
claude plugin marketplace add gigaverse-app/langsmith-cli
claude plugin install langsmith-cli@langsmith-cli
```

---

The code is MIT licensed and on GitHub: [gigaverse-app/langsmith-cli](https://github.com/gigaverse-app/langsmith-cli)

If you're building with LangSmith and tired of context-heavy MCP servers, give it a try. Happy to hear feedback in the issues.
