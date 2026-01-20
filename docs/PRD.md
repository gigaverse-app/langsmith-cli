Here is the complete repository specification. This setup positions the project not just as a "script," but as a serious developer tool that happens to work perfectly with Claude.

### 1. Repository Identity

**Repository Name:** `langsmith-cli`
*(Alternative if taken: `langsmith-ops-cli` or `smith-cli`)*

**Short Description:**

> "A context-efficient, feature-complete CLI for LangSmith. Packaged as a Claude Code plugin to replace heavy MCP servers with lightweight, on-demand skills."

**GitHub Topics:**
`langsmith` `claude-code` `mcp-alternative` `llmops` `cli` `observability` `python`

**License:** `MIT License` (Best for maximizing adoption in the AI ecosystem)

---

### 2. File Structure

This structure satisfies modern Python tooling standards (using `pyproject.toml`) while adhering to the Claude Plugin layout.

```text
langsmith-cli/
├── .github/                # CI/CD for testing the CLI
├── src/
│   └── langsmith_cli/
│       ├── __init__.py
│       ├── main.py         # Entry point (Click/Argparse)
│       └── commands/       # Modular command logic
│           ├── projects.py
│           ├── runs.py
│           └── datasets.py
├── skills/
│   └── langsmith/          # The Claude Skill Definition
│       └── SKILL.md
├── .claude-plugin/
│   └── plugin.json         # Plugin Manifest
├── pyproject.toml          # Python Package Metadata
├── README.md
└── LICENSE

```

---

### 3. Core Metadata Files

#### A. `.claude-plugin/plugin.json` (The Manifest)

This file defines the package for Claude Code.

```json
{
  "schema_version": "1.0",
  "name": "langsmith-cli",
  "version": "0.1.0",
  "description": "Professional CLI tool for LangSmith. Provides low-context, high-performance access to traces, runs, and datasets.",
  "skills": [
    "skills/langsmith"
  ]
}

```

#### B. `pyproject.toml` (The Python Package)

This allows users to install it as a standalone tool (`pip install .`) which is "The Simon Willison Way"—tools should be useful *outside* the agent too.

```toml
[build-system]
requires = ["setuptools", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "langsmith-cli"
version = "0.1.0"
description = "A fantastic CLI for LangSmith and Claude Code"
readme = "README.md"
requires-python = ">=3.9"
dependencies = [
    "langsmith>=0.1.0",
    "rich",          # For beautiful terminal tables (like gh cli)
    "click"          # For robust command handling (like llm tool)
]

[project.scripts]
langsmith-cli = "langsmith_cli.main:cli"

```

---

### 4. The README.md (The Pitch)

**# langsmith-cli**

A fantastic CLI for interacting with [LangSmith](https://smith.langchain.com/), inspired by the UX of `gh` and `llm`.

Designed to be **Context Efficient**. Unlike the standard MCP server which loads massive schemas into your agent's context window, this tool is "lazy-loaded." Claude only sees the CLI help text until it executes a command.

**Features**

* **Zero-Context Overhead:** Keeps your token usage low.
* **Human Readable:** Beautiful tables via `rich` for human users.
* **Machine Readable:** Strict JSON output mode for Claude/Agents.
* **Full Parity:** Supports Projects, Runs, Traces, and Datasets.

**Installation**

*As a Human:*

```bash
pip install langsmith-cli

```

*As a Claude Plugin:*

```bash
claude plugin install https://github.com/your-username/langsmith-cli

```

**Usage**

```bash
# List projects
langsmith-cli projects list

# Get last 5 errors from production
langsmith-cli runs list --project prod --status error --limit 5

# Get raw JSON for a specific run (optimized for piping)
langsmith-cli runs get <run_id> --json

```

---

### 5. The "Skill" Strategy (`skills/langsmith/SKILL.md`)

This is the "prompt engineering" layer that replaces the MCP schema.

```markdown
---
name: langsmith
description: Interaction with LangSmith traces, runs, and datasets via CLI.
---

# LangSmith CLI

Use this tool to debug chains, inspect traces, or manage datasets.
This tool wraps a Python CLI.

## Guiding Principles
1. **Be Specific:** Do not list *all* runs. Always filter by project or status (e.g., `--status error`).
2. **Save Tokens:** Use `--fields id,error,inputs` if you don't need the full trace.
3. **JSON Mode:** Always append `--json` when you need to parse the output programmatically.

## Common Commands

### runs
`langsmith-cli --json runs list --project <name>`
`langsmith-cli --json runs get <id>`

### projects
`langsmith-cli --json projects list`

### datasets
`langsmith-cli --json datasets list`

```

### Next Step

This package is ready to be initialized. Would you like me to generate the **`src/langsmith_cli/main.py`** entry point code using `click` and `rich` to establish that "premium CLI" feel right from the start?


---

Here is the formal ticket to execute this plan. This specification outlines the roadmap from exploring the codebase to delivering a high-quality, "fantastic" CLI packaged as a Claude Code plugin.

---

# 🎫 Ticket: Design & Build `langsmith-cli` Plugin (Context-Efficient MCP Replacement)

**Status:** Proposed
**Priority:** High
**Assignee:** Claude & User
**Goal:** Create a context-efficient replacement for the LangSmith MCP server by building a robust CLI tool (`langsmith-cli`) and wrapping it as a Claude Code Plugin.

## 1. 🔍 Phase 1: Exploration & Reverse Engineering

**Objective:** Map parity between the MCP server capabilities and the native SDK, while adopting UX patterns from top-tier CLI tools.

* **Audit `langchain-ai/langsmith-mcp-server`:**
* Review `services/tools/` to identify all exposed methods (e.g., `search_traces`, `read_run`, `list_datasets`).
* Document input schemas/arguments used by the MCP to ensure the CLI supports identical query capabilities.


* **Audit `langsmith` SDK:**
* Identify the corresponding Python methods for each MCP tool.
* Locate "hidden" utility features in the SDK (e.g., URL generation, batching) that the MCP might have missed but the CLI should have.


* **UX Research (The "Fantastic CLI" Standard):**
* **From `gh`:** Hierarchy (e.g., `langsmith run list` vs `langsmith project list`).
* **From `llm` (Simon Willison):** usage of `click` (or robust `argparse`) for clean help messages, and `--system` flags.
* **From `aws`:** Robust `--output json` vs `--output table` toggles (Critical for Claude interactions vs. Human debugging).



## 2. 🛠️ Phase 2: The `langsmith-cli` Specification

**Objective:** Build a single-file, portable Python script (or minimal package) that feels like a mature product.

**Core Principles:**

1. **Zero-Context Start:** The tool must not load the SDK until the specific command executes.
2. **Machine-Readable by Default:** Output must be optimized for LLM consumption (sparse JSON) but readable for humans when needed.
3. **Idempotency:** Operations like "create dataset" should handle duplicates gracefully.

**Proposed Command Structure:**

```bash
# Project Management
langsmith projects ls                   # List projects
langsmith projects create <name>        # Create new

# Traces & Runs (The heavy lifters)
langsmith runs ls --project <p> --limit 10   # Fetch recent traces
langsmith runs get <id>                      # Get full trace details
langsmith runs search "query"                # Filter runs by content

# Datasets (Evaluation data)
langsmith datasets ls
langsmith datasets push <file.jsonl>         # Upload data

```

**Key Feature Requirements:**

* **`--output json` Flag:** Forces strict JSON output for Claude to parse reliably.
* **`--fields` Filter:** Allows asking for only specific keys (e.g., `id,inputs,error`) to save context tokens.
* **Smart Defaults:** Defaults to the most active project if none specified.

## 3. 📦 Phase 3: The Claude Plugin Package

**Objective:** Bundle the CLI into a portable `langsmith-plugin` structure.

**Directory Structure:**

```text
langsmith-plugin/
├── .claude-plugin/
│   └── plugin.json             # Manifest v1.0
├── requirements.txt            # minimal deps (langsmith, maybe rich/click)
└── skills/
    └── langsmith-tool/
        ├── SKILL.md            # The "Brain" (Instructions)
        └── scripts/
            └── langsmith-cli.py # The "Brawn" (Logic)

```

**The Skill Definition (`SKILL.md`):**
Must explicitly teach Claude how to use the CLI efficiently:

> *"When looking for error logs, first list runs with `--status error`. Do not fetch full details for all runs at once. Use `--fields` to limit token usage."*

## 4. 📝 Acceptance Criteria

* [ ] `langsmith-cli.py` supports 100% of the read/write features found in the official MCP server.
* [ ] The Plugin installs via `claude plugin install ./langsmith-plugin`.
* [ ] Latency check: The tool executes `list_projects` in <1s (cold start).
* [ ] Context check: The `SKILL.md` consumes <500 tokens of context, whereas the original MCP consumed >5k tokens.

---

### **Immediate Next Step**

I can begin **Phase 2** immediately by generating the scaffolding for the "Fantastic CLI" using standard library `argparse` (for zero-dependency portability) or `Click` (for better UX).

**Would you like me to generate the `langsmith-cli.py` skeleton code using `argparse` (standard lib) or `Click` (better DX)?**
