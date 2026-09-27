# 🛠️ LangSmith CLI

<div align="center">

**Keep every LangSmith trace. Query it from your terminal. Hand your agent 100 bytes instead of 34 KB.**

*Parquet trace archive • Offline DuckDB cache • Agent-sized JSON • Rich tables for humans*

[![PyPI](https://img.shields.io/pypi/v/langsmith-cli.svg)](https://pypi.org/project/langsmith-cli/)
[![CI](https://github.com/gigaverse-app/langsmith-cli/actions/workflows/ci.yml/badge.svg)](https://github.com/gigaverse-app/langsmith-cli/actions/workflows/ci.yml)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![uv](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json)](https://github.com/astral-sh/uv)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

[Why](#-why-langsmith-cli) • [Parquet Storage](#-parquet-trace-storage) • [Features](#-features) • [vs. LangChain's CLI](#-which-langsmith-cli-is-this) • [Installation](#-installation) • [Quick Start](#-quick-start) • [Examples](#-examples) • [Documentation](#-documentation)

</div>

---

```bash
uv tool install langsmith-cli    # or: pip install langsmith-cli
```

> [!TIP]
> **Give Claude Code LangSmith superpowers**
>
> ```bash
> claude plugin marketplace add gigaverse-app/langsmith-cli
> claude plugin install langsmith-cli@langsmith-cli
> ```
>
> Or run `/plugin` inside Claude Code and add the marketplace from the UI. The plugin is a
> skill, not an MCP server: it costs your agent no context until the agent actually
> needs LangSmith.

---

## 🎯 Why LangSmith CLI?

**LangSmith's base retention deletes traces after 14 days. langsmith-cli keeps them.**
Traces land in your own S3 bucket as Parquet. The `runs list`, `runs search` and
`runs get` commands you use on live data then query a year of history, or a local
DuckDB cache that works offline.

- 🗄️ **Your traces, your bucket, no expiry.** A daily `archive sync` exports each day before
  LangSmith drops it. `archive backfill` pulls in history through LangSmith Bulk Export.
- 🦆 **Offline, repeatable analysis.** Pull traces into a local Parquet cache once. Every
  query after that runs on your disk through DuckDB, with no API calls or rate limits.
- 🧠 **Agent-sized answers.** `--fields` returns only what you ask for. On 12 real
  production traces, full JSON was 2–34 KB. `--fields id,name,status,error` returned
  108–128 bytes, **94.5–99.7% smaller**.
- 🔌 **Zero context until it's needed.** The Claude Code plugin is an on-demand skill.
  Sessions that never touch LangSmith spend no tokens on tool schemas.
- 📊 **Answers, not dumps.** `runs watch`, `sample`, `analyze`, `usage` and `stats`
  summarize thousands of runs. `fields`, `tags`, `metadata-keys` and `describe` show
  what your traces contain before you query them.
- 🎨 **Pleasant for humans too.** Rich tables, regex and wildcard filters, `--last 24h`,
  plus CSV and YAML export.

---

## 💾 Parquet Trace Storage

One set of commands, three places your traces can live:

| `--source` | What answers | Reach for it when… |
|---|---|---|
| `cloud` (default) | The LangSmith API | You need what is true right now |
| `archive` | Parquet in your organization's S3 bucket | The traces are older than LangSmith retention, or you need org-wide history |
| `local` | A Parquet cache on this machine, queried with DuckDB | You're iterating on the same traces, or you're offline |

`--source` works on `runs list`, `runs search`, `runs get`, `runs get-latest` and
`examples list`, with the same filters and the same JSON, table, CSV and YAML output.
The CLI never switches sources behind your back, and reading never copies data: you
move traces only with explicit commands.

### Archive: keep every trace in S3

```bash
# Daily job: export day D at D+2, then re-export it at D+12 to catch late runs,
# before LangSmith's 14-day retention removes it.
LANGSMITH_ARCHIVE_URI=s3://my-bucket/langsmith \
  langsmith-cli --json archive sync --project prd/my-agent --retention-days 14

# One-time: backfill a year of history through LangSmith Bulk Export (resumable).
langsmith-cli --json archive backfill --config archive.yaml --route production \
  --start-date 2025-08-01 --end-date 2026-08-01 \
  --bulk-export-destination-id <uuid> --import-workers 8

# Query the archive exactly like live data.
langsmith-cli --json runs search "timeout" --source archive \
  --project prd/my-agent --last 365d --fields id,name,status,error
```

Your organization owns the bucket, credentials, encryption and retention policy. The
CLI exports, verifies and publishes sealed daily manifests of canonical Parquet, which
you can also read directly with DuckDB. Re-running is safe: sealed days are skipped and
in-flight export jobs are resumed. See the
[archive operator guide](skills/langsmith/references/archive.md) and the
[archive design](docs/TRACE_ARCHIVE_DESIGN.md).

### Local cache: offline and DuckDB-backed

```bash
# Materialize a week of traces once (from cloud, or --source archive)...
langsmith-cli --json runs pull --source cloud --to local \
  --project prd/my-agent --last 7d

# ...then query them as often as you like, with no API calls.
langsmith-cli --json runs list --source local \
  --project prd/my-agent --fields id,name,status
langsmith-cli --json runs search "timeout" --source local \
  --project prd/my-agent --fields id,name,error
langsmith-cli runs cache schema --project prd/my-agent    # what fields are in there?
```

Pulls are additive and idempotent, and `runs cache repair` validates every Parquet
fragment. Datasets work the same way: `datasets pull my-dataset --to local` freezes an
exact dataset version, then `examples list --dataset my-dataset --source local` reads
it offline. See the [trace sources design](docs/TRACE_SOURCES_DESIGN.md).

---

## ✨ Features

### 🧠 **Agent Optimized**
```bash
# Everything: the full run object, often tens of KB
langsmith-cli --json runs get-latest --project my-agent --failed

# Just what the agent needs: ~110 bytes
langsmith-cli --json runs get-latest --project my-agent --failed --fields id,name,status,error
```
Strict `--json` output on stdout, diagnostics on stderr, and small default limits keep
agent context clean.

### 🎨 **Human Friendly**
- Beautiful Rich tables with syntax highlighting
- Color-coded statuses (🟢 success, 🔴 error, 🟡 pending)
- Smart column truncation for readability
- Export to CSV/YAML for spreadsheets

### 🔍 **Power User Features**
```bash
# Regex filtering
langsmith-cli runs list --name-regex "^prod-.*-v[0-9]+"

# Wildcard patterns
langsmith-cli runs list --name-pattern "*auth*"

# Smart filters
langsmith-cli runs list --slow --failed --today

# Live dashboard
langsmith-cli runs watch
```

### 📦 **Complete Coverage**
Every LangSmith resource at your fingertips:
- ✅ **Projects**: list, get, create, update, delete
- ✅ **Runs**: search, stats, watch, sample, analyze, usage, pricing, export, field discovery
- ✅ **Trace storage**: S3 Parquet archive, local Parquet cache
- ✅ **Datasets**: CRUD, bulk JSONL uploads, exact-version pulls
- ✅ **Examples**: full lifecycle, including creating one from a run
- ✅ **Prompts**: list, get, push, pull, version history
- ✅ **Experiments, feedback and annotation queues**
- ✅ **Self**: installation detection and auto-update

---

## 🆚 Which `langsmith-cli` is this?

LangChain also publishes a Go CLI, `langsmith`, from a repository that is likewise
named [langchain-ai/langsmith-cli](https://github.com/langchain-ai/langsmith-cli). It
is a separate project, and the two install side by side (`langsmith-cli` and
`langsmith`). On **PyPI**, `langsmith-cli` is this project. On **Homebrew**, **Scoop**
and `cli.langsmith.com`, it is LangChain's.

What you get here that LangChain's CLI doesn't document (per its README as of September 2026):

| | **langsmith-cli** (this project) | LangChain's `langsmith` |
|---|---|---|
| Traces older than LangSmith retention | ✅ S3 Parquet archive, same query commands | ❌ |
| Offline trace cache | ✅ Local Parquet + DuckDB | ❌ |
| Choose exactly which fields come back | ✅ `--fields id,name,error` | Preset tiers (`--include-io`, `--full`) |
| Run name filters | ✅ Regex, wildcards, exact | Exact name, or raw filter DSL |
| Human time filters | ✅ `--last 24h`, `--today`, `--recent` | `--last-n-minutes`, ISO `--since` |
| Live dashboard | ✅ `runs watch` | ❌ |
| Stratified sampling | ✅ `runs sample` | ❌ |
| Group-by analytics and token usage | ✅ `runs analyze`, `runs usage`, `runs pricing` | ❌ |
| Field and tag discovery | ✅ `runs fields`, `tags`, `metadata-keys`, `describe` | ❌ |
| Output formats | ✅ Table, JSON, JSONL, CSV, YAML | Table, JSON, JSONL |
| Claude Code | ✅ On-demand skill that teaches your agent this CLI | Plugin that sends Claude Code's own traces to LangSmith |

Reach for LangChain's CLI when you need evaluator rules, insight reports, thread views
or Hub repos. Plenty of teams will want both.

---

## 🚀 Installation

### Quick Install (Recommended)

**Linux/macOS:**
```bash
curl -sSL https://raw.githubusercontent.com/gigaverse-app/langsmith-cli/main/scripts/install.sh | sh
```

**Windows:**
```powershell
iwr -useb https://raw.githubusercontent.com/gigaverse-app/langsmith-cli/main/scripts/install.ps1 | iex
```

This standalone installer:
- Creates an isolated environment (no conflicts)
- Automatically adds `langsmith-cli` to your PATH
- Works without manually installing Python packages
- Requires Python 3.12+

### Using `uv`
```bash
uv tool install langsmith-cli
```

### Using `pip`
```bash
pip install langsmith-cli
```

### For Claude Code Users
After installing the CLI above, add the skill:
```bash
/plugin marketplace add gigaverse-app/langsmith-cli
```

### From Source
```bash
git clone https://github.com/gigaverse-app/langsmith-cli.git
cd langsmith-cli
uv sync
uv run langsmith-cli --help
```

---

## 🔑 Quick Start

### 1️⃣ Authenticate
```bash
langsmith-cli auth login
# Creates .env with your LANGSMITH_API_KEY
```

### 2️⃣ Explore Your Projects
```bash
langsmith-cli projects list
```
```
┏━━━━━━━━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━━━━━━━┓
┃ Project       ┃ Run Count  ┃ Last Run     ┃
┡━━━━━━━━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━━━━━━━┩
│ production    │ 12,450     │ 2 mins ago   │
│ staging       │ 3,241      │ 15 mins ago  │
│ development   │ 892        │ 1 hour ago   │
└───────────────┴────────────┴──────────────┘
```

### 3️⃣ Inspect Recent Runs
```bash
langsmith-cli runs list --project production --limit 5
```

### 4️⃣ Debug Errors Fast
```bash
langsmith-cli runs list --failed --recent --fields error
```

---

## 💡 Examples

### 🔍 Advanced Filtering

**Find authentication runs that failed in the last hour:**
```bash
langsmith-cli runs list \
  --name-pattern "*auth*" \
  --failed \
  --recent
```

**Search for specific versioned services:**
```bash
langsmith-cli runs list \
  --name-regex "^prod-api-v[0-9]+" \
  --min-latency 5s \
  --today
```

**Multi-tag filtering (AND logic):**
```bash
langsmith-cli runs list \
  --tag production \
  --tag experimental \
  --slow
```

### 📊 Aggregated Insights

```bash
langsmith-cli runs stats --project production
```
```json
{
  "run_count": 12450,
  "error_rate": 0.023,
  "latency_p50": 0.234,
  "latency_p99": 1.892,
  "total_cost": 45.67,
  "last_run_time": "2026-01-14T20:15:30Z"
}
```

### 🔴 Live Monitoring

```bash
langsmith-cli runs watch --project production
```
```
🔴 Live Dashboard (Ctrl+C to exit)

┏━━━━━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━━┓
┃ Run              ┃ Status ┃ Latency  ┃ Time     ┃
┡━━━━━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━━┩
│ ChatOpenAI       │ 🟢     │ 0.234s   │ Just now │
│ RetrievalChain   │ 🟢     │ 1.456s   │ 2s ago   │
│ AuthService      │ 🔴     │ 5.123s   │ 5s ago   │
│ WebScraper       │ 🟡     │ -        │ 8s ago   │
└──────────────────┴────────┴──────────┴──────────┘
```

### 💾 Bulk Dataset Uploads

```bash
# Export examples to JSONL (using --output for reliable file writing)
langsmith-cli examples list --dataset my-dataset --output examples.jsonl

# Freeze an exact dataset version locally, then work offline through the same facade
langsmith-cli --json datasets pull my-dataset --to local
langsmith-cli --json examples list --dataset my-dataset --source local

# Upload to new dataset
langsmith-cli datasets push examples.jsonl --dataset production-eval
```

### 🌐 Open in Browser

```bash
langsmith-cli runs open <run-id>
# Opens trace in LangSmith UI
```

### 📤 Export for Analysis

```bash
# Export to CSV for Excel
langsmith-cli runs list --format csv > runs.csv

# Export to YAML for configs
langsmith-cli projects list --format yaml > projects.yml
```

### 🔊 Verbosity Control

Control diagnostic output with industry-standard flags (following pip, Black, etc.):

```bash
# Default: Progress messages + warnings
langsmith-cli runs list --project production

# Quiet: Warnings only, no progress
langsmith-cli -q runs list --project production

# Silent: Errors only (cleanest for scripts)
langsmith-cli -qq runs list --project production

# Debug: Show API calls and processing details
langsmith-cli -v runs list --project production

# Trace: Ultra-verbose with HTTP requests and timing
langsmith-cli -vv runs list --project production
```

**Clean JSON piping with `-qq`:**
```bash
# Diagnostics on stderr, JSON on stdout
langsmith-cli --json runs list | jq

# Silent mode for cleanest piping
langsmith-cli --json -qq runs list | jq '.[] | .name'

# Suppress diagnostics with stderr redirection
langsmith-cli --json runs list 2>/dev/null | jq
```

---

## 🤖 AI Agent Integration

### As a Claude Code Skill

> [!NOTE]
> **Prerequisites:** Install CLI first (`uv tool install langsmith-cli`), then add skill (`/plugin marketplace add gigaverse-app/langsmith-cli`)

The CLI is optimized for Claude Code agents:

```bash
# In Claude Code, agents can run:
langsmith-cli --json runs list --project default --limit 10 --fields inputs,outputs,error

# Returns clean JSON without Rich formatting
# Uses --fields to minimize context usage
# Filters with --failed, --slow, --recent for targeted debugging
```

**Key Agent Patterns:**
- Always use `--json` as first argument
- Use `--fields` to reduce tokens by 90%+
- Combine smart filters: `--failed --recent --slow`
- Keep `--limit` small (default: 10)

### Example Agent Usage
```python
import subprocess
import json

# Agent efficiently fetches only errored runs
result = subprocess.run(
    ["langsmith-cli", "--json", "runs", "list",
     "--failed", "--limit", "5", "--fields", "error,inputs"],
    capture_output=True,
    text=True
)

runs = json.loads(result.stdout)
# Analyze errors with minimal context usage
```

---

## 📚 Documentation

### Command Reference

```bash
langsmith-cli --help

# Auth and projects
auth login               # Authenticate with LangSmith
projects list            # List projects
projects get|create|update|delete

# Runs: query (add --source cloud|archive|local to list/search/get/get-latest)
runs list                # Search and filter runs
runs search              # Full-text search across runs
runs get <id>            # Inspect a specific run
runs get-latest          # Most recent run matching filters
runs open <id>           # Open trace in the LangSmith UI
runs export              # Write runs as individual JSON files
runs view-file           # View runs from JSONL files

# Runs: analyze
runs stats               # Aggregate statistics
runs watch               # Live run dashboard
runs sample              # Stratified sampling by tags/metadata
runs analyze             # Group runs and compute metrics
runs usage               # Token usage over time, with grouping
runs pricing             # Model pricing coverage check
runs tags                # Discover tag patterns
runs metadata-keys       # Discover metadata keys
runs fields              # Discover field paths and types
runs describe            # Detailed field statistics

# Parquet storage
archive sync             # Daily export to your S3 archive (primary + reconciliation)
archive backfill         # One-time historical export through Bulk Export
archive status           # List published archive manifests
runs pull                # Add cloud/archive traces to the local Parquet cache
runs cache list|schema|repair|clear|dir

# Datasets, examples and prompts
datasets list|get|create|delete
datasets push            # Bulk upload from JSONL
datasets pull            # Replicate an exact dataset version (e.g. --to local)
datasets versions|status
examples list|get|create|update|delete
examples from-run        # Create an example from a run's inputs/outputs
prompts list|get|push|pull|create|delete
prompts commits          # Prompt version history

# Evaluation and review
experiments results      # Run stats and feedback scores for an experiment
feedback list|get|create|delete
annotation-queues list|get|create|update|delete

# Installation
self detect              # Show installation details
self skill               # Print the agent usage guide
self update              # Update to latest version
```

### Global Flags

```bash
--json              # Machine-readable JSON output (no Rich formatting)
--format {json|csv|yaml}  # Export format
--help              # Show help
--version           # Show version
```

### Runs Filtering

| Option | Description | Example |
|--------|-------------|---------|
| `--project` | Filter by project name | `--project production` |
| `--project-id` | Filter by project UUID | `--project-id abc-123...` |
| `--status` | Filter by status | `--status error` |
| `--failed` | Only failed runs | `--failed` |
| `--succeeded` | Only successful runs | `--succeeded` |
| `--slow` | Runs >5s latency | `--slow` |
| `--recent` | Last hour | `--recent` |
| `--today` | Today's runs | `--today` |
| `--min-latency` | Min latency | `--min-latency 2s` |
| `--max-latency` | Max latency | `--max-latency 10s` |
| `--since` | Since time | `--since "1 hour ago"` |
| `--last` | Last duration (or window with --since) | `--last 24h` or `--since 2026-02-17 --last 72h` |
| `--tag` | Filter by tag (repeatable) | `--tag prod --tag beta` |
| `--name-pattern` | Wildcard match | `--name-pattern "*auth*"` |
| `--name-regex` | Regex match | `--name-regex "^prod-.*"` |
| `--model` | Filter by model | `--model gpt-4` |

---

## 🏗️ Architecture

### Design Principles

**1. Lazy Loading Performance**
```python
# ❌ Don't import heavy libraries at top level
from langsmith import Client

# ✅ Import inside command functions
@runs.command("list")
def list_runs(...):
    import langsmith  # Only loads when command executes
    client = langsmith.Client()
```

**2. Context Efficiency**
- Default JSON output is "sparse" (only essential fields)
- Field pruning with `--fields` for targeted data extraction
- No full trace blobs unless explicitly requested

**3. Dual UX Pattern**
```python
# Check JSON flag for output mode
if ctx.obj.get("json"):
    click.echo(json.dumps(data))  # Strict JSON for agents
else:
    console.print(rich_table)      # Beautiful tables for humans
```

**4. DRY Utilities**
- Shared helpers in `utils.py` (100% test coverage)
- Consistent error handling with field context
- Reusable filtering, sorting, and formatting functions

---

## 🧪 Development

### Setup
```bash
# Clone and setup
git clone https://github.com/gigaverse-app/langsmith-cli.git
cd langsmith-cli
uv sync

# Install pre-commit hooks
uv run pre-commit install
```

### Testing
```bash
# Run all tests
uv run pytest

# With coverage
uv run pytest --cov=src --cov-report=term-missing

# E2E tests (requires LANGSMITH_API_KEY)
export LANGSMITH_API_KEY="lsv2_..."
uv run pytest tests/test_e2e.py -v
```

### Code Quality
```bash
# Linting and formatting
uv run ruff check --fix
uv run ruff format

# Type checking
uv run pyright
```

### Project Stats
- **1,400+ tests**
- **Zero Pyright errors**

---

## 🤝 Contributing

Contributions welcome! Please:

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/awesome`)
3. Make your changes with tests
4. Ensure 100% coverage for new code
5. Run `uv run pre-commit run --all-files`
6. Submit a pull request

---

## 📝 License

MIT License - see [LICENSE](LICENSE) for details.

---

## 🙏 Acknowledgments

Built with:
- [Click](https://click.palletsprojects.com/) - CLI framework
- [Rich](https://rich.readthedocs.io/) - Terminal formatting
- [LangSmith SDK](https://github.com/langchain-ai/langsmith-sdk) - Official Python client
- [uv](https://github.com/astral-sh/uv) - Fast Python package installer

---

<div align="center">

**Made with ❤️ for the LangChain community**

[Report Bug](https://github.com/gigaverse-app/langsmith-cli/issues) • [Request Feature](https://github.com/gigaverse-app/langsmith-cli/issues) • [Documentation](https://github.com/gigaverse-app/langsmith-cli/blob/main/docs/)

</div>
