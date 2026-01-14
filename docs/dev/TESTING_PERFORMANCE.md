# Testing Performance Guide

This document explains how to run tests efficiently during development and CI/CD.

## Test Categories

Tests are organized by speed and purpose:

| Category | Marker | Count | Speed | Description |
|----------|--------|-------|-------|-------------|
| **Unit Tests** | (default) | 86 | 0.05s | Fast pure function tests (utils) |
| **Integration Tests** | (default) | 108 | ~1-2s each | CLI tests with mocked API |
| **Smoke Tests** | `@pytest.mark.slow` `@pytest.mark.smoke` | 25 | ~5-10s each | Real API calls via subprocess |
| **E2E Tests** | `@pytest.mark.e2e` | 11 | N/A | Skipped (require specific data) |

## Quick Reference

```bash
# Fast development workflow (unit + integration only)
pytest -m "not slow"                    # ~90 seconds, 194 tests

# Full test suite (includes smoke tests) - requires API key
export LANGSMITH_API_KEY="lsv2_..."
pytest                                  # ~5 minutes, 219 tests

# Only smoke tests
pytest -m "smoke"                       # ~2-3 minutes, 25 tests

# Specific test categories
pytest -m "slow"                        # Smoke + slow tests
pytest -m "not (slow or e2e)"          # Fast tests only

# Ultra-fast: unit tests only
pytest tests/test_utils.py              # 0.05 seconds, 86 tests
```

## Performance Optimizations Implemented

### 1. **Pytest Markers** (✅ Implemented)
All slow tests are marked with `@pytest.mark.slow` and `@pytest.mark.smoke` for easy filtering.

**Configuration** (`pyproject.toml`):
```toml
[tool.pytest.ini_options]
markers = [
    "slow: marks tests as slow (integration tests with real API calls)",
    "smoke: marks tests as smoke tests (E2E validation)",
    "e2e: marks tests as end-to-end tests",
]
```

### 2. **Subprocess Optimization** (✅ Implemented)
Smoke tests now use `python -m langsmith_cli.main` instead of `uv run langsmith-cli`.

**Before:**
```python
cmd = ["uv", "run", "langsmith-cli", *args]  # ~1-2s overhead per call
```

**After:**
```python
cmd = [sys.executable, "-m", "langsmith_cli.main", *args]  # ~0.1s overhead
```

**Impact:** ~10x faster subprocess startup for smoke tests.

### 3. **Fast Unit Tests** (✅ Already exist)
86 unit tests for utils functions run in 0.05 seconds - no CliRunner overhead.

## Test Speed Breakdown

### Why are tests slow?

1. **CliRunner overhead** (1-2s per test)
   - Argument parsing
   - Command execution
   - Output rendering (Rich tables)
   - Module imports

2. **Real API calls** (5-10s per test)
   - Network latency
   - LangSmith API response time
   - Multiple subprocess calls per test

3. **Lazy imports** (minor)
   - Each CLI invocation imports langsmith, rich, etc.

### What makes tests fast?

✅ **Unit tests** - Pure functions, no I/O
✅ **Mocked API** - No network calls
✅ **Direct imports** - No subprocess overhead

## Parallel Execution (pytest-xdist)

⚠️ **Warning:** Parallel execution is **slower** for this test suite.

```bash
# Sequential (faster)
pytest                                  # 5:48

# Parallel with auto workers (slower)
pytest -n auto                          # 6:14
```

**Why?** Worker spawning overhead outweighs benefits for small test suites.

**When to use `-n auto`:** Only if you have >500 tests or very slow individual tests (>30s each).

## CI/CD Recommendations

### Development (Local)
```bash
# Run only fast tests during development
pytest -m "not slow" -q
# Takes ~90 seconds
```

### Pre-Commit
```bash
# Same as development - no slow tests
pytest -m "not slow"
```

### CI Pipeline (GitHub Actions)
```yaml
# Fast check (PR validation)
- name: Run fast tests
  run: pytest -m "not slow" --cov=src

# Full validation (main branch, nightly)
- name: Run all tests
  env:
    LANGSMITH_API_KEY: ${{ secrets.LANGSMITH_API_KEY }}
  run: pytest
```

## Measuring Test Performance

### Find slowest tests:
```bash
pytest --durations=20
```

### Profile a specific test:
```bash
pytest tests/test_smoke.py::TestRunsSkill::test_runs_list_with_name_regex -v --durations=1
```

### Compare before/after optimization:
```bash
# Before
time pytest tests/test_smoke.py

# After
time pytest tests/test_smoke.py
```

## Future Optimization Ideas

### 1. **Test Fixtures with Scope**
Convert expensive fixtures to module scope:
```python
@pytest.fixture(scope="module")
def langsmith_client():
    return Client()  # Created once per test file
```

### 2. **Cached API Responses**
Cache smoke test API responses for deterministic testing:
```python
@pytest.mark.vcr()  # Using pytest-vcr
def test_runs_list():
    ...
```

### 3. **Direct CLI Imports**
Replace subprocess calls with direct imports:
```python
# Instead of subprocess
from langsmith_cli.main import cli
result = runner.invoke(cli, ["runs", "list"])
```

### 4. **Test Data Factories**
Parametrize similar tests to reduce duplication:
```python
@pytest.mark.parametrize("filter_pattern,expected", [
    ("*prod*", ["prod-api", "prod-web"]),
    ("*staging*", ["staging-api"]),
])
def test_name_filter(filter_pattern, expected):
    ...
```

## Summary

**Current Performance:**
- Fast tests (no slow marker): **90 seconds** ✅
- Full suite (with smoke): **5 minutes** ⏱️
- Unit tests only: **0.05 seconds** 🚀

**Best Practice:**
```bash
# During development
pytest -m "not slow"

# Before commit (full validation)
pytest
```

**Key Takeaway:** The test suite is well-optimized for its architecture. The slowness comes from intentional E2E validation with real API calls, which is valuable but should be run selectively.
