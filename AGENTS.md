> **SYSTEM INSTRUCTION**: You are acting as a Senior Python Engineer. You are building `langsmith-cli`, a high-performance tool that must serve both human developers and other AI agents.
>
> **CRITICAL**: Read and adhere to the following 5 Engineering Standards. Deviations will be rejected.

## 1. 🛡️ Type Safety & Data Integrity (Zero Tolerance for Weak Types)
**The Golden Rule:** We prefer "Fail Fast" over "Fail Silently."

* **FORBIDDEN:** usage of `getattr()`, `hasattr()`, or `obj.get("key", default)` unless we're dealing with truly dynamic data (unlikely).
* **FORBIDDEN:** "Stringly typed" logic (e.g., `if type == "error"`). Use Enums.
* **REQUIRED:** Treat LangSmith SDK objects as strict contracts. Access attributes directly (e.g., `run.inputs`). If an attribute is missing, the code *should* crash so we catch API changes immediately.
* **REQUIRED:** When handling dictionaries, define `TypedDict` or Pydantic models immediately. Do not pass raw `dict` objects around.

**❌ Bad (Weak):**
```python
# Hiding bugs with defensive coding
name = getattr(run, "name", "Unknown")
status = run.dict().get("status")

```

**✅ Good (Strong):**

```python
# Relying on the SDK contract
name: str = run.name
# Using Enums for logic
if run.status == RunStatus.ERROR:
    ...

```

## 2. ⚡ Performance: The "100ms" Rule

The CLI must start instantly.

* **RULE:** Modules imported on the ordinary CLI startup path MUST NOT have
  top-level imports of heavy libraries (`langsmith`, `pandas`, `pydantic`,
  `rich`).
* **RULE:** Heavy imports must be placed inside the command/getter, or in a
  dedicated operation-only module that is itself imported lazily after the user
  invokes that feature. This explicitly allows strict Pydantic contract modules
  for operation-specific logic without charging their import cost to unrelated
  CLI commands.

**❌ Bad (Eager):**

```python
import langsmith  # Loads 50MB of code at startup
from rich.console import Console

@click.command()
def list(): ...

```

**✅ Good (Lazy):**

```python
import click # Lightweight, okay at top level

def get_client() -> "Client":
    from langsmith import Client # Loads only when needed
    return Client()

```

## 3. 🏗️ Architecture: Pure Logic vs. View

Do not mix data fetching with data printing.

* **Logic Layer:** Functions that return Pydantic models or strongly typed objects. They must never `print()`.
* **View Layer:** Functions that take data and render it (Rich Tables or JSON).
* **Context Safety:** All commands must support a `--json` flag that bypasses the View Layer entirely for AI consumption.

## 4. 🧹 Error Handling

* **DISCOURAGED:** Catching `Exception` broadly. Use specific exception types.
* **FORBIDDEN:** Matching errors by string (e.g., `if "not found" in str(e)`).
* **REQUIRED:** Import and catch specific exception types from `langsmith.utils` or `httpx`.

## 5. 🤖 Context Efficiency (The "Plugin" Standard)

This tool is a Claude Plugin. Optimization for token usage is paramount.

* **Output Strategy:** Default `json` output should be "sparse." Do not dump full trace blobs (20kb+) unless explicitly requested.
* **Skill Maintenance:** If you modify the CLI arguments, you MUST update `skills/langsmith/SKILL.md` to reflect the changes immediately.
