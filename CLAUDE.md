# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**langsmith-cli** is a context-efficient CLI tool for LangSmith that serves as both a standalone developer tool and a Claude Code plugin. It replaces heavy MCP servers with lightweight, on-demand skills while providing 100% feature parity plus superior features.

**Key Design Philosophy:**
- **Lazy Loading**: Heavy imports (langsmith, rich) only load when commands execute
- **Context Efficiency**: Field pruning (`--fields`) reduces token usage by ~90%
- **Dual UX**: Rich tables for humans, strict JSON for agents
- **Type Safety**: Strict SDK contracts, no stringly-typed logic (in progress)

## Development Commands

### Environment Setup
```bash
# Install dependencies
uv sync

# Install pre-commit hooks
uv run pre-commit install
```

### Testing
```bash
# Run all tests (parallel by default with -n auto)
uv run pytest

# Run with coverage (serial is faster for coverage)
uv run pytest --cov=src --cov-report=term-missing -n 0

# Run specific test file
uv run pytest tests/test_runs.py -v

# Run specific test
uv run pytest tests/test_runs.py::test_runs_list -v

# Run serially (disable parallelization)
uv run pytest -n 0

# Run smoke tests (requires LANGSMITH_API_KEY)
# Smoke tests use in-process CLI invocation and session fixtures for speed
export LANGSMITH_API_KEY="lsv2_..."
uv run pytest tests/test_smoke.py -v

# Run E2E tests (requires LANGSMITH_API_KEY)
uv run pytest tests/test_e2e.py -v
```

### Code Quality
```bash
# Linting and formatting (auto-fix)
uv run ruff check --fix
uv run ruff format

# Type checking
uv run pyright

# Run all pre-commit hooks manually
uv run pre-commit run --all-files
```

### Running the CLI
```bash
# Run locally during development
uv run langsmith-cli --help
uv run langsmith-cli runs list --project default

# Run with JSON output (agent mode)
uv run langsmith-cli --json runs list --limit 5

# Verbosity control (following industry standards: pip, Black, etc.)
uv run langsmith-cli runs list               # Default: INFO (progress + warnings)
uv run langsmith-cli -q runs list            # Quiet: WARNING (warnings only)
uv run langsmith-cli -qq runs list           # Silent: ERROR (errors only)
uv run langsmith-cli -v runs list            # Debug: DEBUG (debug + progress + warnings)
uv run langsmith-cli -vv runs list           # Trace: TRACE (ultra-verbose)

# JSON mode with clean stdout/stderr separation
uv run langsmith-cli --json runs list        # Progress to stderr, JSON to stdout
uv run langsmith-cli --json -qq runs list    # Silent mode, clean JSON only
uv run langsmith-cli --json runs list 2>/dev/null  # Suppress diagnostics
```

### Authentication
```bash
# Set up API key (creates/updates .env file)
uv run langsmith-cli auth login

# Or manually create .env from template
cp .env.example .env
# Edit .env and add your LANGSMITH_API_KEY
```

## Architecture

### Command Structure

The CLI uses Click with a modular command group architecture:

```
main.py (entry point)
├── @cli (root group with --json flag)
├── auth (group)
│   └── login
├── projects (group)
│   ├── list
│   └── create
├── runs (group)
│   ├── list
│   ├── get
│   ├── stats
│   ├── open
│   ├── watch
│   └── search
├── datasets (group)
│   ├── list
│   ├── get
│   ├── create
│   └── push
├── examples (group)
│   ├── list
│   ├── get
│   └── create
└── prompts (group)
    ├── list
    ├── get
    └── push
```

### Key Design Patterns

**1. Lazy Loading Performance Pattern**
```python
# ❌ BAD - Top-level import (loads SDK immediately)
from langsmith import Client

# ✅ GOOD - Import inside command function
@runs.command("list")
def list_runs(...):
    import langsmith  # Only loads when command executes
    client = langsmith.Client()
```

**2. Dual Output Pattern (JSON vs Rich Tables)**
```python
# All commands check ctx.obj.get("json") flag
if ctx.obj.get("json"):
    import json
    click.echo(json.dumps(data, default=str))
else:
    from rich.table import Table
    # ... render table
```

**3. Context Efficiency Pattern (Field Pruning)**
```python
# runs get command supports --fields to reduce token usage
# Instead of returning full 20KB run object, return only requested fields
@runs.command("get")
@click.option("--fields", help="Comma-separated field names")
def get_run(ctx, run_id, fields):
    run = client.read_run(run_id)
    if fields:
        # Prune to only requested fields
        field_list = fields.split(",")
        data = {k: v for k, v in run_dict.items() if k in field_list}
```

**4. Logging and Verbosity Pattern (stdout/stderr Separation)**
```python
# All commands use CLILogger for diagnostic output with proper stream separation
@runs.command("list")
def list_runs(ctx, output_format, count, output, ...):
    # Get logger from context
    logger = ctx.obj["logger"]

    # Determine if output is machine-readable (use stderr for diagnostics)
    is_machine_readable = (
        ctx.obj.get("json") or
        output_format in ["csv", "yaml"] or
        count or
        output
    )
    logger.use_stderr = is_machine_readable

    # Use logger methods for all diagnostic output
    logger.debug("API call: POST /runs/query")  # -v: DEBUG level
    logger.info("Fetching 100 runs...")          # Default: INFO level
    logger.warning("Warning: Failed to fetch...")  # Always shown (unless -qq)
    logger.error("Error reading file...")        # Always shown
    logger.success("Wrote N items to file")      # INFO level

    # Data output continues using appropriate stream
    if ctx.obj.get("json"):
        click.echo(json_dumps(data))  # stdout
    else:
        logger.data(table, mode="table")  # stdout
```

**Verbosity Levels** (following pip, Black, and Python CLI standards):
- **Default (no flags)**: INFO level (20) - progress + warnings + errors
- **`-q`**: WARNING level (30) - warnings + errors only, no progress
- **`-qq`**: ERROR level (40) - only errors (cleanest for scripts)
- **`-v`**: DEBUG level (10) - debug details + info + warnings + errors
- **`-vv`**: TRACE level (5) - ultra-verbose (HTTP, timing, raw data)

**Stream Separation:**
- **stdout**: Data output (JSON, CSV, YAML, tables, count results)
- **stderr**: Diagnostic messages (progress, warnings, errors) in machine-readable modes
- **Table mode exception**: Diagnostics stay on stdout (mixing is OK for humans)

### File Organization

```
src/langsmith_cli/
├── __init__.py
├── main.py              # Entry point, CLI group registration
├── logging.py           # CLILogger for verbosity control and stream separation
└── commands/            # Modular command implementations
    ├── auth.py          # Authentication (login)
    ├── projects.py      # Project management
    ├── runs.py          # Runs/traces (largest module)
    ├── datasets.py      # Dataset operations
    ├── examples.py      # Dataset examples
    └── prompts.py       # Prompt management

tests/
├── conftest.py          # Pytest fixtures (CliRunner)
├── test_main.py         # Root CLI tests
├── test_logging.py      # CLILogger tests
├── test_auth.py         # Auth command tests
├── test_projects.py     # Projects command tests
├── test_runs.py         # Runs command tests (largest)
├── test_smoke.py        # Smoke tests (requires API key)
└── test_e2e.py          # End-to-end tests (requires API key)

skills/langsmith/
├── SKILL.md             # Claude Code skill definition
└── bridge.py            # Subprocess wrapper for skill invocation

docs/
├── PRD.md               # Product requirements
├── COMMANDS_DESIGN.md   # Detailed command specifications
└── dev/                 # Development documentation
    ├── SESSION_DIRECTIVES.md  # User preferences and tooling choices
    ├── TYPE_SAFETY_GUIDE.md   # Type safety guidelines
    ├── TESTING_PERFORMANCE.md # Test performance optimization
    └── MCP_PARITY.md          # MCP compatibility mapping
```

## Code Quality Principles

### Rule 1: Always Use SDK's Pydantic Models
**Never create custom response models - always reuse the LangSmith SDK's Pydantic models.**

- ✅ Use `langsmith.schemas.Dataset`, `Example`, `Run`, `Prompt`, `TracerSessionResult`
- ✅ For JSON output, use `.model_dump(include={...}, mode="json")` to select fields
- ✅ Access model fields directly: `project.name`, `dataset.id`
- ❌ Don't use `getattr(obj, "field", default)` - the fields are guaranteed to exist
- ❌ Don't create duplicate models in a separate `models.py` file

**Why:** SDK models are type-safe, validated, and the source of truth. Creating duplicates causes maintenance burden and drift.

### Rule 2: Always Use the LangSmith SDK
**Use the LangSmith SDK for all functionality - only use REST API if SDK doesn't provide the capability.**

- ✅ Use `langsmith.Client()` methods
- ✅ Explore SDK first: `dir(client)`, inspect source code, read SDK docs
- ❌ Don't make raw HTTP requests if SDK provides the method

**Why:** SDK handles auth, retry logic, rate limiting, and provides type-safe interfaces.

### Rule 3: Use Proper Exception Types
**Never match errors by string - always use SDK's exception types from `langsmith.utils`.**

- ✅ `from langsmith.utils import LangSmithConflictError, LangSmithNotFoundError`
- ✅ `except LangSmithConflictError:` for handling conflicts
- ❌ `if "already exists" in str(e):` - string matching is brittle

**Why:** Exception types are semantic contracts that won't break with message changes.

## Type Safety Guidelines

**For comprehensive type safety guidelines, see [docs/dev/TYPE_SAFETY_GUIDE.md](docs/dev/TYPE_SAFETY_GUIDE.md)**

### Philosophy: Zero Tolerance for Weak Types

Strong types catch bugs at development time, not runtime. Weak types defer errors to production.

**Golden Rules:**
1. ❌ Never use `Any` without a documented reason
2. ❌ Never use bare `list` or `dict` - always parameterize: `list[T]`, `dict[K, V]`
3. ✅ Always use SDK Pydantic models when available (Run, Dataset, Example, Prompt, TracerSessionResult)
4. ✅ Always handle `None` explicitly with `T | None`
5. ✅ Always use direct attribute access over `getattr()`
6. ✅ Always run `pyright` before committing (must be 0 errors)
7. ✅ Use Python 3.12+ syntax: `list[T]` not `List[T]`, `dict[K, V]` not `Dict[K, V]`, `T | None` not `Optional[T]`

### Common Type Patterns

**Parameterize Collections:**
```python
# ❌ WRONG
def process_data(items: list) -> dict:
    results: List = []
    return {}

# ✅ CORRECT
from langsmith.schemas import Run

def process_runs(runs: list[Run]) -> dict[str, list[str]]:
    results: list[str] = []
    return {"names": results}
```

**Use SDK Models:**
```python
# ❌ WRONG
def format_run(run: Any) -> dict:
    return {"name": getattr(run, "name", "Unknown")}

# ✅ CORRECT
from langsmith.schemas import Run

def format_run(run: Run) -> dict[str, str]:
    return {"name": run.name}  # Type checker guarantees .name exists
```

**Generic Functions:**
```python
# ❌ WRONG
from typing import Callable, Any

def sort_items(items: list, key_func: Callable[[Any], Any]) -> list:
    return sorted(items, key=key_func)

# ✅ CORRECT
from typing import TypeVar, Callable

T = TypeVar('T')
K = TypeVar('K')

def sort_items(items: list[T], key_func: Callable[[T], K]) -> list[T]:
    return sorted(items, key=key_func)
```

**Handle Optional Values:**
```python
# ❌ WRONG
def get_error(run: Any) -> str:
    return run.error  # What if error is None?

# ✅ CORRECT
from langsmith.schemas import Run

def get_error(run: Run) -> str | None:
    return run.error  # Type checker knows this can be None

def get_error_message(run: Run) -> str:
    return run.error or "No error"  # Handle None explicitly
```

### When Is `Any` Acceptable?

Only in these rare cases:
1. **JSON data being serialized/deserialized** - `dict[str, Any]` for JSON output where values can be str, int, bool, datetime, nested dicts, etc.
2. **Click Context object** (Click's obj is Any - extract and narrow immediately)

**For everything else, use strong types:**
- **Generic functions**: Use `TypeVar` instead of `Any`
  ```python
  # ❌ WRONG
  def sort_items(items: list, key_func: Callable[[Any], Any]) -> list:
      ...

  # ✅ CORRECT
  T = TypeVar('T')
  K = TypeVar('K')
  def sort_items(items: list[T], key_func: Callable[[T], K]) -> list[T]:
      ...
  ```

- **Console objects**: Use Protocol or actual type, not `Any`
  ```python
  # ❌ WRONG
  def print_msg(console: Any) -> None:
      ...

  # ✅ CORRECT (Protocol)
  class ConsoleProtocol(Protocol):
      def print(self, *args: Any, **kwargs: Any) -> None: ...

  def print_msg(console: ConsoleProtocol) -> None:
      ...

  # ✅ CORRECT (Actual type)
  from rich.console import Console
  def print_msg(console: Console) -> None:
      ...
  ```

**Document `Any` when you must use it:**
```python
def output_json(data: list[dict[str, Any]]) -> None:
    """Output JSON data.

    Args:
        data: JSON data (Any is acceptable - values can be str, int, bool, nested dicts, etc.)
    """
```

### Verification

Always verify type safety with:
```bash
# Type checking (zero errors is the goal)
uv run pyright

# Tests still pass
uv run pytest

# Linting
uv run ruff check .
```

## Testing Patterns

### Current Test Approach
Tests use Click's `CliRunner` with unittest.mock patches:

```python
def test_runs_list(runner):
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        mock_client.list_runs.return_value = [mock_run]
        result = runner.invoke(cli, ["runs", "list"])
        assert result.exit_code == 0
```

**Known Improvement Opportunity:** Tests currently use `patch()` with string paths. Per project standards (docs/AGENTS.md), should migrate to `patch.object()` for better refactor-safety.

### Test Data: Always Use Pydantic Models, Never MagicMock

**Critical Rule:** Always create test data using real LangSmith Pydantic model instances, not MagicMock objects.

❌ **BAD - Using MagicMock for test data:**
```python
def test_datasets_list(runner):
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        d = MagicMock()
        d.name = "test-dataset"  # This doesn't validate schema
        d.model_dump.return_value = {...}  # Manually mocking model_dump
        mock_client.list_datasets.return_value = iter([d])
```

✅ **GOOD - Using real Pydantic models:**
```python
from langsmith.schemas import Dataset
from uuid import UUID
from datetime import datetime, timezone

def test_datasets_list(runner):
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        # Create real Dataset instance with validation
        d = Dataset(
            name="test-dataset",
            description="Test",
            data_type="kv",
            id=UUID("ae99b6fa-a6db-4f1c-8868-bc6764f4c29e"),
            created_at=datetime(2024, 7, 3, tzinfo=timezone.utc),
            modified_at=datetime(2024, 7, 3, tzinfo=timezone.utc),
            example_count=10,
        )
        mock_client.list_datasets.return_value = iter([d])
```

**Why:**
- Real models validate field types and required fields
- `model_dump()` works correctly without mocking
- Catches schema mismatches before production
- Tests are more realistic and reveal integration issues
- Pydantic field validators run, catching bad data early

**Helper Pattern:**
Create reusable factory functions in `tests/conftest.py`:
```python
def create_dataset(name="test", example_count=10) -> Dataset:
    return Dataset(
        name=name,
        data_type="kv",
        id=UUID("ae99b6fa-a6db-4f1c-8868-bc6764f4c29e"),
        created_at=datetime(2024, 7, 3, tzinfo=timezone.utc),
        modified_at=datetime(2024, 7, 3, tzinfo=timezone.utc),
        example_count=example_count,
    )
```

### Test Coverage Requirements
- All new commands must have unit tests
- Critical paths need integration tests
- E2E tests validate full SDK interaction (require API key)
- Aim for 100% coverage of new code

## Engineering Standards (from docs/AGENTS.md)

### 1. Type Safety (Zero Tolerance for Weak Types)
- ❌ Prohibit: `getattr()`, `hasattr()`, stringly-typed logic
- ❌ **NEVER use `# type: ignore` comments** - Fix the underlying type issue instead
- ✅ Require: Direct attribute access, strict SDK contracts, Enums for logic
- ✅ Write safe code with proper type guards and None checks
- ✅ Always use LangSmith SDK's Pydantic models directly (Dataset, Example, Run, Prompt, TracerSessionResult)
- ✅ For context efficiency, use `.model_dump(include={...}, mode="json")` to select specific fields
- ✅ Never create duplicate response models - reuse SDK models with field selection
- **Current Status**: Fully implemented - all commands use SDK Pydantic models with type-safe attribute access

### 2. Performance (100ms Rule)
- ❌ NO top-level imports of heavy libraries (langsmith, rich, pandas)
- ✅ Libraries imported inside command functions (lazy loading)
- **Current Status**: Properly implemented

### 3. Architecture (Pure Logic vs View)
- Logic Layer: Returns Pydantic models/typed objects, never prints
- View Layer: Handles Rich tables or JSON output
- Context Safety: All commands support `--json` flag
- **Current Status**: Mixed - some logic calls `console.print()` directly (opportunity for refactor)

### 4. Error Handling
- ❌ **NEVER silently swallow errors** with bare `except: pass` or `except Exception: pass`
- ❌ Discourage broad `Exception` catching without logging
- ❌ Forbidden: Matching errors by string
- ✅ Use specific SDK exceptions: `LangSmithConflictError`, `LangSmithNotFoundError`, `LangSmithAuthError`, etc.
- ✅ Import exceptions from `langsmith.utils`
- ✅ Choose appropriate error handling based on severity:
  - **Errors**: Use `raise` or `click.ClickException()` for fatal issues that should stop execution
  - **Stack traces**: Use `console.print_exception()` or `logger.exception()` for debugging unexpected failures
  - **Warnings**: Use `console.print("[yellow]Warning:...")` for recoverable issues users should know about
  - **Debug messages**: Use `logger.debug()` or `console.print("[dim]...")` for diagnostic information
- ✅ When catching broad exceptions, always log what went wrong before continuing
- **Current Status**: Fully implemented - all commands use proper exception types and log failures

### 5. Context Efficiency (Plugin Standard)
- Default JSON output should be "sparse"
- No full trace blobs (20kb+) unless requested
- Skill maintenance: Update SKILL.md when CLI arguments change
- **Current Status**: Field pruning implemented; SKILL.md up-to-date

## Feature Parity with LangSmith MCP

The CLI provides 100% feature parity with the official LangSmith MCP server. See [docs/dev/MCP_PARITY.md](docs/dev/MCP_PARITY.md) for detailed command mappings and parameter coverage.

## Claude Code Plugin Integration

### Skill Definition
The plugin is defined in `skills/langsmith/SKILL.md` which teaches Claude how to use the CLI efficiently:
- Always use `--json` as first argument for parseable output
- Use `--fields` to reduce context size
- Use `--status error` for quick debugging
- Keep `--limit` small (default 10)

### Bridge Script
`skills/langsmith/bridge.py` is a thin subprocess wrapper that:
1. Receives command arguments from Claude
2. Executes `langsmith-cli` via subprocess
3. Returns stdout/stderr to Claude

### Plugin Manifests
The repository contains two manifest files:
- `.claude-plugin/marketplace.json` - Registers the repository as an installable marketplace
- `.claude-plugin/plugin.json` - Defines the plugin itself

### Installation

**Method 1: Using Terminal Commands**
```bash
# Add the marketplace
claude plugin marketplace add gigaverse-app/langsmith-cli

# Install the plugin
claude plugin install langsmith-cli@langsmith-cli
```

**Method 2: Using Claude Code Interactive UI**

Inside Claude Code, run `/plugin` (without arguments) to open the interactive plugin manager:
1. Navigate to the "Marketplaces" tab
2. Add marketplace: `gigaverse-app/langsmith-cli`
3. Navigate to the "Discover" tab
4. Install the `langsmith-cli` plugin

**Local Development**
```bash
# Add local marketplace
claude plugin marketplace add /home/aviadr1/projects/langsmith-cli

# Install the plugin
claude plugin install langsmith-cli@langsmith-cli
```

## Pydantic Model Usage Pattern

### Always Use SDK's Pydantic Models

The LangSmith SDK provides Pydantic models for all entities:
- `langsmith.schemas.TracerSessionResult` - Projects/Sessions
- `langsmith.schemas.Dataset` - Datasets
- `langsmith.schemas.Example` - Dataset examples
- `langsmith.schemas.Run` - Runs/traces
- `langsmith.schemas.Prompt` - Prompts

**Pattern:**
```python
# ✅ GOOD - Use SDK models with focused field selection
def list_items(ctx, limit):
    client = langsmith.Client()
    items_list = list(client.list_items(limit=limit))

    if ctx.obj.get("json"):
        # Select only needed fields for context efficiency
        data = [
            item.model_dump(
                include={"id", "name", "created_at"},
                mode="json",
            )
            for item in items_list
        ]
        click.echo(json.dumps(data, default=str))
        return

    # For table display, access fields directly
    for item in items_list:
        table.add_row(item.name, str(item.id))

# ❌ BAD - Don't use getattr() or create custom models
def list_items_bad(ctx, limit):
    items = list(client.list_items(limit=limit))
    data = [
        {
            "name": getattr(i, "name", "Unknown"),  # Don't do this
            "id": str(getattr(i, "id", "")),  # Don't do this
        }
        for i in items
    ]
```

**Benefits:**
1. Type safety - IDE autocomplete and type checking work
2. Context efficiency - Only return fields you need
3. No duplicate models - SDK models are the source of truth
4. Validation - Pydantic validates data automatically

## Common Patterns for New Commands

### Adding a New Command

1. **Create command file** in `src/langsmith_cli/commands/`
2. **Define Click group and commands**:
```python
import click
from rich.console import Console

console = Console()

@click.group()
def mycommand():
    """Description of command group."""
    pass

@mycommand.command("list")
@click.pass_context
def list_items(ctx):
    """List items."""
    # Lazy import SDK
    import langsmith
    client = langsmith.Client()

    items = client.list_items()

    # Handle JSON vs Rich output
    if ctx.obj.get("json"):
        import json
        click.echo(json.dumps([i.dict() for i in items], default=str))
    else:
        from rich.table import Table
        table = Table(title="Items")
        table.add_column("Name")
        for item in items:
            table.add_row(item.name)
        console.print(table)
```

3. **Register in main.py**:
```python
from langsmith_cli.commands.mycommand import mycommand
cli.add_command(mycommand)
```

4. **Write tests** in `tests/test_mycommand.py`:
```python
from langsmith_cli.main import cli
from unittest.mock import patch

def test_mycommand_list(runner):
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        mock_client.list_items.return_value = []
        result = runner.invoke(cli, ["mycommand", "list"])
        assert result.exit_code == 0
```

5. **Update SKILL.md** with new command usage

## Known Issues & Improvement Opportunities

### Type Safety
- ✅ **FIXED**: Removed all `getattr()` usage - now uses direct attribute access
- ✅ **FIXED**: Removed string-based error matching - now uses proper SDK exception types
- Pyright reports 4 type warnings (non-critical but fixable)

### Architecture
- Some logic mixed with view code (console.print() calls in command functions)
- Could benefit from separating data fetching from rendering
- Mock usage could be upgraded to `patch.object` instead of `patch`

### Security
- `.mcp.json` contains hardcoded API key (should use environment variable)

## Dependencies

**Runtime:**
- click >=8.3.1 - CLI framework
- langsmith >=0.6.2 - LangSmith SDK
- pydantic >=2.12.5 - Data validation
- python-dotenv >=1.2.1 - .env file support
- rich >=14.2.0 - Terminal formatting

**Development:**
- pytest >=9.0.2 - Testing framework
- ruff >=0.14.11 - Linting/formatting
- pyright >=1.1.408 - Type checking
- pre-commit >=4.5.1 - Git hooks

**Python Version:** >=3.12

## Git Workflow

Per docs/dev/SESSION_DIRECTIVES.md:
- Use `uv` for all dependency management
- Commit often with clear messages
- Use feature branches and PRs for larger features
- Ensure `git status` is clean before commits
- Run pre-commit hooks before committing
- Never use `git commit --amend` or force push (per user's global CLAUDE.md)
