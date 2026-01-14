# LangSmith CLI Testing Strategy

## Overview

Since LangSmith only retains traces for 400 days, we need a two-tier testing strategy:

1. **Permanent Tests** - Use mocked data, will work indefinitely
2. **E2E Tests** - Use real API, graceful handling of data expiry

## Permanent Tests (Will Always Work)

### Unit Tests with Mocked Data

These tests use `unittest.mock` to create fake objects and don't depend on real data existing in LangSmith. They test the CLI logic, output formatting, and filtering logic without requiring live API access.

**Location:** `tests/test_*.py` (non-E2E files)

**Test Files:**
- `test_projects.py` - Projects list, filtering, sorting, formats
- `test_datasets.py` - Datasets list, filtering, pagination
- `test_examples.py` - Examples list, filtering, pagination, splits
- `test_prompts.py` - Prompts list, filtering
- `test_auth.py` - Authentication logic
- `test_main.py` - CLI entry point and global options
- `test_config.py` - Configuration handling
- `test_utils.py` - Utility functions

**Key Characteristics:**
- ✅ Use `patch.object()` to mock `langsmith.Client`
- ✅ Create mock objects with fake but realistic data
- ✅ Test invariants (what should always be true)
- ✅ Cover filtering, pagination, output formats
- ✅ Work without API key
- ✅ Run in CI/CD pipelines

**Running Permanent Tests:**
```bash
pytest tests/ -k "not e2e" --cov=src --cov-report=term-missing
```

## Temporary E2E Tests (Data May Expire)

### End-to-End Tests with Real API

These tests use real LangSmith API and real data. They verify CLI integration with the actual service.

**Location:** `tests/test_e2e.py`

**Key Characteristics:**
- ✅ Requires `LANGSMITH_API_KEY` environment variable
- ✅ Uses real LangSmith API
- ✅ Tests structural aspects (exit codes, output format)
- ❌ Does NOT assert on specific data content
- ❌ Does NOT depend on permanent traces
- ⏳ May fail if datasets/traces expire after 400 days

**Examples of Good E2E Tests:**
```python
def test_projects_list_e2e():
    """Test that projects list returns valid JSON."""
    result = run_cli_cmd(["--json", "projects", "list"])
    assert result.returncode == 0
    data = json.loads(result.stdout)
    assert isinstance(data, list)
    # Don't assert: assert data[0]["name"] == "specific-project"
```

**Examples of Bad E2E Tests (Will Break):**
```python
def test_runs_list_specific_run():
    """BAD: Depends on specific run data."""
    result = run_cli_cmd(["runs", "list", "--project", "local/metamind/..."])
    assert "019bbb66-f237-7170-9be8-f31b0b1f0b4c" in result.stdout
    # This will fail after 400 days when the trace expires
```

**Running E2E Tests:**
```bash
# Only if API key is available
export LANGSMITH_API_KEY="lsv2_..."
pytest tests/test_e2e.py -v
```

## Data That Persists (Safe for Testing)

### Permanent Datasets

These datasets have been verified to persist and can be used in E2E tests:
- `ds-soundbites-baseset` - 111 examples (created 2024-07-03)
- `ds-factcheck-summarize-baseset` - 6 examples
- `ds-factcheck-scoring` - 4 examples
- `ds-factcheck-summaries` - 3 examples
- `ds-factcheck-search-queries` - 4 examples

### Temporary Data (Expires After 400 Days)

Avoid depending on these in tests:
- Runs in `smoke-test-*` projects
- Traces in `local/metamind/*` projects
- Any data in projects created recently

## Testing Patterns

### Pattern 1: Mocked Unit Test

```python
def test_examples_list_with_split_filter(runner):
    """INVARIANT: --splits filter should be passed to API."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value

        ex = MagicMock()
        ex.id = "test-id"
        ex.inputs = {"text": "test"}
        ex.outputs = {"result": "test"}
        ex.metadata = {"dataset_split": ["train"]}

        mock_client.list_examples.return_value = iter([ex])

        result = runner.invoke(
            cli, ["examples", "list", "--dataset", "test", "--splits", "train"]
        )
        assert result.exit_code == 0

        # Verify API was called correctly
        call_kwargs = mock_client.list_examples.call_args[1]
        assert call_kwargs.get("splits") == "train"
```

### Pattern 2: E2E Structural Test

```python
def test_datasets_list_e2e():
    """E2E test - verify structure, not specific data."""
    if not os.getenv("LANGSMITH_API_KEY"):
        pytest.skip("LANGSMITH_API_KEY not set")

    result = run_cli_cmd(["--json", "datasets", "list"])
    assert result.returncode == 0

    data = json.loads(result.stdout)
    assert isinstance(data, list)

    # Verify structure if results exist
    if data:
        assert "id" in data[0]
        assert "name" in data[0]
        assert "data_type" in data[0]
        # Don't assert: assert data[0]["name"] == "specific-dataset"
```

### Pattern 3: E2E with Known Stable Data

```python
def test_examples_list_stable_dataset_e2e():
    """E2E test using stable dataset that persists."""
    if not os.getenv("LANGSMITH_API_KEY"):
        pytest.skip("LANGSMITH_API_KEY not set")

    # Use dataset we know persists
    result = run_cli_cmd([
        "--json", "examples", "list",
        "--dataset", "ds-soundbites-baseset",
        "--limit", "5"
    ])
    assert result.returncode == 0

    data = json.loads(result.stdout)
    assert isinstance(data, list)
    # Verify we got some examples (should have 111)
    assert len(data) > 0
    # Don't assert specific IDs
```

## CI/CD Integration

### GitHub Actions / Local CI

**Permanent Tests (Always Run):**
```yaml
- name: Run unit tests
  run: pytest tests/ -k "not e2e" --cov=src
```

**E2E Tests (Only with API Key):**
```yaml
- name: Run E2E tests
  if: secrets.LANGSMITH_API_KEY != ''
  env:
    LANGSMITH_API_KEY: ${{ secrets.LANGSMITH_API_KEY }}
  run: pytest tests/test_e2e.py -v
```

## Coverage Goals

### Unit Tests (Permanent)
- **Target:** 100% coverage of CLI logic
- **Must test:**
  - Command invocation
  - Option parsing
  - Output formatting (JSON, table, CSV, YAML)
  - Filtering logic (patterns, regex, splits)
  - Pagination (limit, offset)
  - Error handling
  - Empty results

### E2E Tests (Temporary)
- **Target:** Verify integration works
- **Should test:**
  - Exit codes are correct
  - Output format is valid (JSON parses, tables display)
  - Filtering doesn't cause errors
  - Unknown options are rejected
  - Large responses are handled

## Maintenance

### When Tests Break

1. **Unit Tests Break:** Bug in CLI code - fix the implementation
2. **E2E Tests Break Due to Missing API Key:** Skip them gracefully (already done)
3. **E2E Tests Break Due to Expired Data:**
   - Update to use stable datasets
   - Or remove test if not critical
   - Don't add time-dependent assertions

### Regular Maintenance Tasks

- Quarterly: Review E2E tests for data dependency issues
- Annually: Verify stable datasets still exist and contain expected data
- After API changes: Update mocked client calls to match new SDK

## References

- **CLAUDE.md:** Project testing standards and Pydantic model usage
- **test_projects.py:** Example of well-structured unit tests with mocks
- **test_e2e.py:** Example of resilient E2E tests
