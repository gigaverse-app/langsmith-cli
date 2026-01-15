# 🛠️ LangSmith CLI

<div align="center">

**The Modern CLI for LangSmith**
*Lightning-fast • Context-efficient • Built for humans and AI agents*

[![uv](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json)](https://github.com/astral-sh/uv)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

[Features](#-features) • [Installation](#-installation) • [Quick Start](#-quick-start) • [Examples](#-examples) • [Documentation](#-documentation)

</div>

---

> [!TIP]
> **Install as Claude Code Plugin**
>
> **Step 1:** Install the CLI tool
> ```bash
> # Quick install (Linux/macOS)
> curl -sSL https://raw.githubusercontent.com/langchain-ai/langsmith-cli/main/scripts/install.sh | sh
>
> # Or use uv
> uv tool install langsmith-cli
> ```
>
> **Step 2:** Add the plugin to Claude Code
>
> **Option A: Terminal Command**
> ```bash
> claude plugin marketplace add gigaverse-app/langsmith-cli
> claude plugin install langsmith-cli@langsmith-cli
> ```
>
> **Option B: Interactive UI**
> Inside Claude Code, run `/plugin` (without arguments) and use the UI to add the marketplace
>
> This gives Claude Code instant access to all LangSmith commands via skills.

---

![context is all you need](https://media.licdn.com/dms/image/v2/D4D22AQFkt4EsgXsQVw/feedshare-shrink_800/B4DZu.8A41KQAg-/0/1768434962262?e=1770249600&v=beta&t=WhjBTpbVG-guRZWem5Qie3vMD6g1PqmhXB7SC4TRkMM)

## 🎯 Why LangSmith CLI?

Traditional tools are slow, verbose, and waste tokens. **LangSmith CLI** is different:

| Feature | LangSmith CLI | Official MCP Server |
|---------|---------------|---------------------|
| **Startup Time** | < 100ms (lazy loading) | ~2s (heavy imports) |
| **Context Usage** | 96% reduction with `--fields` | Full objects always |
| **Human UX** | Rich tables with colors | JSON only |
| **Agent UX** | Strict `--json` mode | Mixed output |
| **Live Dashboard** | `runs watch` real-time monitoring | ❌ |
| **Advanced Filters** | Regex, wildcards, time presets | Basic only |
| **Stratified Sampling** | `runs sample` by tag/metadata | ❌ |
| **Aggregate Analytics** | `runs analyze` group & metrics | ❌ |
| **Tag Discovery** | `runs tags` auto-discover patterns | ❌ |
| **File Operations** | View/analyze offline with globs | ❌ |
| **Export Formats** | JSON, CSV, YAML | JSON only |

**100% Feature Parity** + **10x Better QoL** 🚀

---

## ✨ Features

### 🏎️ **Performance First**
- **<100ms startup** via lazy-loaded imports
- Streams large datasets without memory bloat
- Async-ready architecture

### 🧠 **Agent Optimized**
```bash
# Traditional: Returns 20KB trace object (1000+ tokens)
langsmith-cli runs get abc123

# Agent Mode: Returns only what you need (40 tokens)
langsmith-cli --json runs get abc123 --fields inputs,outputs,error
```
**96% context savings** on large traces!

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
- ✅ **Projects** - List, create, inspect
- ✅ **Runs** - Search, stats, watch, open in browser
- ✅ **Datasets** - CRUD + bulk JSONL uploads
- ✅ **Examples** - Full lifecycle management
- ✅ **Prompts** - Version control your prompts

---

## 🚀 Installation

### Quick Install (Recommended)

**Linux/macOS:**
```bash
curl -sSL https://raw.githubusercontent.com/langchain-ai/langsmith-cli/main/scripts/install.sh | sh
```

**Windows:**
```powershell
iwr -useb https://raw.githubusercontent.com/langchain-ai/langsmith-cli/main/scripts/install.ps1 | iex
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
# Export examples to JSONL
langsmith-cli --json examples list --dataset my-dataset > examples.jsonl

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

# Core Commands
auth login              # Authenticate with LangSmith
projects list           # List all projects
runs list              # Search and filter runs
runs get <id>          # Inspect a specific run
runs stats             # Aggregate statistics
runs watch             # Live run dashboard
runs open <id>         # Open trace in browser
datasets list          # List datasets
datasets create        # Create new dataset
datasets push          # Bulk upload from JSONL
examples list          # List dataset examples
examples create        # Add example to dataset
prompts list           # List prompt repositories
prompts get            # Pull a prompt template
prompts push           # Push local prompt to LangSmith
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
| `--project` | Filter by project | `--project production` |
| `--status` | Filter by status | `--status error` |
| `--failed` | Only failed runs | `--failed` |
| `--succeeded` | Only successful runs | `--succeeded` |
| `--slow` | Runs >5s latency | `--slow` |
| `--recent` | Last hour | `--recent` |
| `--today` | Today's runs | `--today` |
| `--min-latency` | Min latency | `--min-latency 2s` |
| `--max-latency` | Max latency | `--max-latency 10s` |
| `--since` | Since time | `--since "1 hour ago"` |
| `--last` | Last duration | `--last 24h` |
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
- **79% Test Coverage** (116 tests)
- **100% Utils Coverage** (47 tests for helpers)
- **Zero Type Errors** (Pyright clean)
- **100% MCP Parity** (13/13 tools)

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
