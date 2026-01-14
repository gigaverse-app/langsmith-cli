# 🛠️ langsmith-cli

**Context-efficient CLI for LangSmith. Built for humans and agents.**

[![uv](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json)](https://github.com/astral-sh/uv)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

## ✨ Features

- **🚀 Performance**: Lazy-loads the LangSmith SDK. Fast startup.
- **🧠 Agent Optimized**: Strict `--json` mode and `--fields` pruning saves 90% of token context.
- **🎨 Human Friendly**: Beautiful `rich` tables and color-coded statuses.
- **🔌 Watch Mode**: Live dashboard of incoming runs.
- **📂 Full Parity**: Projects, Runs, Datasets, Examples, and Prompts.

## 📦 Installation

```bash
# Using uv
uv tool install langsmith-cli

# Or pip
pip install langsmith-cli
```

## 🔑 Setup

```bash
langsmith-cli auth login
```

## 📖 Usage

### Projects
```bash
langsmith-cli projects list
```

### Runs
```bash
# List recent runs
langsmith-cli runs list --project default --limit 5

# Inspect a run with field pruning (Save Tokens!)
langsmith-cli runs get <id> --fields inputs,outputs,error --json

# Aggregated Stats
langsmith-cli runs stats

# Watch incoming runs
langsmith-cli runs watch
```

### Datasets & Prompts
```bash
langsmith-cli datasets list
langsmith-cli prompts list
```

## 🤖 Claude Code Plugin

This tool is optimized for use as a Claude Code skill. To use it, add this directory as a skill in your Claude environment.
