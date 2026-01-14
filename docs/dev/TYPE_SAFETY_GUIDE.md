# Type Safety Guidelines

## Philosophy: Zero Tolerance for Weak Types

**Strong types catch bugs at development time, not runtime. Weak types defer errors to production.**

This project enforces strict type safety:
- ✅ **Strong types**: `list[str]`, `dict[str, int]`, `langsmith.schemas.Run`
- ❌ **Weak types**: `Any`, `list`, `dict`, `object`

## Core Principles

### 1. Always Use Specific Types

**❌ WRONG: Weak types hide bugs**
```python
from typing import Any, List, Dict

def process_items(items: List[Any]) -> Dict:
    return {"count": len(items)}

def get_field(obj: Any, field: str) -> Any:
    return getattr(obj, field, None)
```

**✅ CORRECT: Strong types catch bugs**
```python
from langsmith.schemas import Run

def process_runs(runs: list[Run]) -> dict[str, int]:
    return {"count": len(runs)}

def get_run_name(run: Run) -> str:
    return run.name  # Type checker verifies .name exists
```

### 2. SDK Models Are Your Friend

The LangSmith SDK provides Pydantic models for everything. **Always use them.**

**Available SDK Models:**
```python
from langsmith.schemas import (
    Run,                    # Traces/runs
    TracerSessionResult,    # Projects/sessions
    Dataset,               # Datasets
    Example,               # Dataset examples
    Prompt,                # Prompts
)
```

**❌ WRONG: Losing type information**
```python
def format_run(run: Any) -> dict:
    return {
        "id": str(run.id),
        "name": getattr(run, "name", "Unknown"),  # Fragile!
    }
```

**✅ CORRECT: Type-safe access**
```python
from langsmith.schemas import Run

def format_run(run: Run) -> dict[str, str]:
    return {
        "id": str(run.id),
        "name": run.name,  # Type checker guarantees .name exists
    }
```

### 3. Parameterize Collections

**❌ WRONG: Weak collections**
```python
from typing import List, Dict, Any

def process_data(items: list) -> dict:
    results: List = []
    mapping: Dict = {}
    return mapping
```

**✅ CORRECT: Parameterized collections**
```python
from langsmith.schemas import Run

def process_runs(runs: list[Run]) -> dict[str, list[str]]:
    results: list[str] = []
    mapping: dict[str, list[str]] = {}
    return mapping
```

### 4. Handle Optional Values Explicitly

**❌ WRONG: Implicit None handling**
```python
def get_error(run: Any) -> str:
    return run.error  # What if error is None?
```

**✅ CORRECT: Explicit Optional**
```python
from langsmith.schemas import Run

def get_error(run: Run) -> str | None:
    return run.error  # Type checker knows this can be None

def get_error_message(run: Run) -> str:
    return run.error or "No error"  # Handle None explicitly
```

### 5. Type Callbacks and Higher-Order Functions

**❌ WRONG: Any in callbacks**
```python
from typing import Callable, Any

def apply_filter(
    items: list,
    predicate: Callable[[Any], bool]
) -> list:
    return [item for item in items if predicate(item)]
```

**✅ CORRECT: Generic types**
```python
from typing import TypeVar, Callable
from langsmith.schemas import Run

T = TypeVar('T')

def apply_filter(
    items: list[T],
    predicate: Callable[[T], bool]
) -> list[T]:
    return [item for item in items if predicate(item)]

# Usage is type-safe:
def is_errored(run: Run) -> bool:
    return run.error is not None

errored_runs = apply_filter(runs, is_errored)  # Type: list[Run]
```

## Common Patterns

### Pattern 1: Sorting with Key Functions

**❌ WRONG:**
```python
from typing import Callable, Any

def sort_items(
    items: list,
    key_func: Callable[[Any], Any]
) -> list:
    return sorted(items, key=key_func)
```

**✅ CORRECT:**
```python
from typing import TypeVar, Callable

T = TypeVar('T')
K = TypeVar('K')

def sort_items(
    items: list[T],
    key_func: Callable[[T], K]
) -> list[T]:
    return sorted(items, key=key_func)
```

### Pattern 2: Filtering by Field

**❌ WRONG:**
```python
from typing import Callable, Any

def filter_by_field(
    items: list,
    field_getter: Callable[[Any], Any],
    value: Any
) -> list:
    return [item for item in items if field_getter(item) == value]
```

**✅ CORRECT:**
```python
from typing import TypeVar, Callable
from langsmith.schemas import Run

T = TypeVar('T')

def filter_runs_by_status(
    runs: list[Run],
    status: str
) -> list[Run]:
    return [run for run in runs if run.status == status]

# Or more generic:
def filter_by_field(
    items: list[T],
    field_getter: Callable[[T], str | None],
    pattern: str
) -> list[T]:
    return [
        item for item in items
        if (field := field_getter(item)) and pattern in field
    ]
```

### Pattern 3: JSON Parsing

**❌ WRONG:**
```python
import json
from typing import Any

def parse_json(json_str: str) -> Any:
    return json.loads(json_str)
```

**✅ CORRECT:**
```python
import json

def parse_json_dict(json_str: str) -> dict[str, Any]:
    """Parse JSON string to dictionary."""
    result = json.loads(json_str)
    if not isinstance(result, dict):
        raise ValueError(f"Expected dict, got {type(result)}")
    return result

def parse_json_list(json_str: str) -> list[dict[str, Any]]:
    """Parse JSON string to list of dictionaries."""
    result = json.loads(json_str)
    if not isinstance(result, list):
        raise ValueError(f"Expected list, got {type(result)}")
    return result
```

### Pattern 4: Console/Rich Types

**❌ WRONG:**
```python
from typing import Any

def print_table(console: Any, data: list) -> None:
    console.print("data")
```

**✅ CORRECT:**
```python
from rich.console import Console
from langsmith.schemas import Run

def print_runs_table(console: Console, runs: list[Run]) -> None:
    from rich.table import Table

    table = Table(title="Runs")
    table.add_column("ID")
    table.add_column("Name")

    for run in runs:
        table.add_row(str(run.id), run.name)

    console.print(table)
```

## Refactoring Checklist

When refactoring code for type safety:

- [ ] Replace `Any` with specific types (SDK models, primitives)
- [ ] Parameterize `list` → `list[T]`
- [ ] Parameterize `dict` → `dict[K, V]`
- [ ] Replace `List` with `list` (Python 3.12+)
- [ ] Replace `Dict` with `dict` (Python 3.12+)
- [ ] Replace `Optional[T]` with `T | None`
- [ ] Add explicit type annotations to all function signatures
- [ ] Use TypeVar for generic functions
- [ ] Replace `getattr()` with direct attribute access
- [ ] Run `pyright` and fix all errors
- [ ] Run tests to verify behavior unchanged

## When Is `Any` Acceptable?

**Very rarely.** Only these cases:

### 1. JSON.loads() Result (Must Be Narrowed Immediately)
```python
import json

def parse_metadata(json_str: str) -> dict[str, str | int | bool]:
    raw: Any = json.loads(json_str)  # Any is temporary
    if not isinstance(raw, dict):
        raise ValueError("Expected dict")
    return raw  # Narrowed to dict
```

### 2. Console Object (When Rich Types Are Too Heavy)
```python
from typing import Protocol

class ConsoleProtocol(Protocol):
    def print(self, *args: Any) -> None: ...

def log(console: ConsoleProtocol, message: str) -> None:
    console.print(message)
```

### 3. Click Context Object
```python
import click

def my_command(ctx: click.Context) -> None:
    json_mode: bool = ctx.obj.get("json", False)  # Click's obj is Any
```

**But even then, narrow immediately:**
```python
def get_json_mode(ctx: click.Context) -> bool:
    """Extract json mode from Click context."""
    return bool(ctx.obj.get("json", False))
```

## Type Safety Anti-Patterns

### Anti-Pattern 1: `getattr()` Instead of Direct Access

**❌ WRONG:**
```python
def get_name(obj: Any) -> str:
    return str(getattr(obj, "name", "Unknown"))
```

**✅ CORRECT:**
```python
from langsmith.schemas import Run

def get_run_name(run: Run) -> str:
    return run.name

def get_optional_description(run: Run) -> str:
    return run.description or "No description"
```

### Anti-Pattern 2: String-Based Field Access

**❌ WRONG:**
```python
def get_field(obj: dict, field: str) -> Any:
    return obj.get(field)
```

**✅ CORRECT:**
```python
from langsmith.schemas import Run

def get_run_id(run: Run) -> str:
    return str(run.id)

# If you MUST use dict (e.g., for JSON response):
def get_metadata_value(metadata: dict[str, str], key: str) -> str | None:
    return metadata.get(key)
```

### Anti-Pattern 3: Broad Exception Catching Without Types

**❌ WRONG:**
```python
def safe_get(obj: Any, field: str) -> Any:
    try:
        return getattr(obj, field)
    except Exception:
        return None
```

**✅ CORRECT:**
```python
from langsmith.schemas import Run

def get_error_safely(run: Run) -> str:
    """Get error message, handling None."""
    return run.error or "No error"

# If you truly need dynamic access (rare):
def get_field_dynamic(run: Run, field: str) -> str | None:
    """Get field dynamically with type safety."""
    if field == "name":
        return run.name
    elif field == "status":
        return run.status
    else:
        raise ValueError(f"Unknown field: {field}")
```

## Gradual Typing Strategy

If you inherit weakly-typed code:

1. **Start at the boundaries** (command entry points)
2. **Work inward** toward core logic
3. **Use `# type: ignore` temporarily** for stubborn issues
4. **Document why** you needed type: ignore
5. **File TODOs** to remove them later

```python
# TODO: Remove type: ignore once langsmith SDK exports this type
def process_raw_data(data: Any) -> list[Run]:  # type: ignore[misc]
    """Process raw SDK data into typed Run objects."""
    return [Run(**item) for item in data]
```

## Verification

Always verify type safety with:

```bash
# Type checking (zero errors is the goal)
uv run pyright

# Tests still pass
uv run pytest

# Linting
uv run ruff check .
```

## Benefits of Strong Typing

1. **Catch bugs at development time** - Not in production
2. **Better IDE support** - Autocomplete, go-to-definition
3. **Self-documenting code** - Types are inline documentation
4. **Refactoring confidence** - Type checker finds all usages
5. **Onboarding speed** - New devs understand code faster
6. **Reduced tests** - Type checker eliminates whole classes of tests

## Summary

**Golden Rules:**
1. ❌ Never use `Any` without a documented reason
2. ❌ Never use bare `list` or `dict`
3. ✅ Always use SDK Pydantic models when available
4. ✅ Always parameterize collections: `list[T]`, `dict[K, V]`
5. ✅ Always handle `None` explicitly with `T | None`
6. ✅ Always use direct attribute access over `getattr()`
7. ✅ Always run `pyright` before committing

**Type safety is not optional. It's a requirement.**
