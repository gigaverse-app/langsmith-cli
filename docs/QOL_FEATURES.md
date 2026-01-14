# Quality of Life Features

This document describes the implemented quality-of-life improvements for langsmith-cli.

## Implemented Features

### P0 Features (Phase 1)

### Tag Filtering

Filter runs by tags with intuitive syntax:

```bash
# Single tag
langsmith-cli runs list --tag production

# Multiple tags (AND logic - all must be present)
langsmith-cli runs list --tag production --tag experimental
```

**How it works**: Converts to FQL `has(tags, "value")` under the hood.

### Model Filtering

Filter runs by the model used:

```bash
# Find runs using GPT-4
langsmith-cli runs list --model gpt-4

# Find runs using Claude
langsmith-cli runs list --model claude

# Find specific model versions
langsmith-cli runs list --model gpt-4-turbo
```

**How it works**: Uses FQL `search()` to find the model name across all run fields.

### Status Filtering (Enhanced)

Multiple ways to filter by success/error status:

```bash
# Using --status flag
langsmith-cli runs list --status error
langsmith-cli runs list --status success

# Using convenience flags
langsmith-cli runs list --failed      # Equivalent to --status error
langsmith-cli runs list --succeeded   # Equivalent to --status success
```

**How it works**: Passes `error=True/False` to the LangSmith API.

### Name Pattern Matching

Search runs by name with wildcard or regex support:

```bash
# Wildcard matching - Find runs with "auth" in the name
langsmith-cli runs list --name-pattern "*auth*"

# Wildcard matching - Find runs starting with "test-"
langsmith-cli runs list --name-pattern "test-*"

# Regex matching - Find runs matching pattern "test-auth-v[0-9]+"
langsmith-cli runs list --name-regex "test-auth-v[0-9]+"

# Regex with anchors - Find runs starting with "auth"
langsmith-cli runs list --name-regex "^auth"

# Regex for version patterns
langsmith-cli runs list --name-regex "^prod-.*-v[0-9]+$"
```

**How it works**:
- `--name-pattern`: Converts wildcards to FQL `search()` function for server-side filtering
- `--name-regex`: Uses full Python regex with client-side filtering (FQL doesn't support full regex)

### Smart Filters (Quick Presets)

Common debugging scenarios as single flags:

```bash
# Find slow runs (latency > 5s)
langsmith-cli runs list --slow

# Recent runs (last hour)
langsmith-cli runs list --recent

# Today's runs only
langsmith-cli runs list --today
```

**How it works**: Each flag generates appropriate FQL filters (e.g., `gt(latency, "5s")`).

### Flexible Duration Filters

Custom latency/duration thresholds:

```bash
# Runs taking more than 2 seconds
langsmith-cli runs list --min-latency 2s

# Runs taking less than 10 seconds
langsmith-cli runs list --max-latency 10s

# Runs in a specific latency range (1-5 seconds)
langsmith-cli runs list --min-latency 1s --max-latency 5s

# Other duration formats
langsmith-cli runs list --min-latency 500ms   # milliseconds
langsmith-cli runs list --min-latency 1.5s    # decimal seconds
langsmith-cli runs list --min-latency 5m      # minutes
```

**Supported units**: `ms` (milliseconds), `s` (seconds), `m` (minutes), `h` (hours), `d` (days)

### Flexible Time Filters

Custom time ranges:

```bash
# Last 24 hours
langsmith-cli runs list --last 24h

# Last 7 days
langsmith-cli runs list --last 7d

# Last 30 minutes
langsmith-cli runs list --last 30m

# Since a specific ISO timestamp
langsmith-cli runs list --since "2024-01-14T10:00:00Z"

# Since a relative time (same as --last)
langsmith-cli runs list --since 48h
```

**Time formats**:
- **Relative**: `30m`, `24h`, `7d` (minutes, hours, days)
- **ISO**: `2024-01-14T10:00:00Z` or `2024-01-14T10:00:00+00:00`

### Combining Filters

All filters can be combined together:

```bash
# Production runs that are slow
langsmith-cli runs list --tag production --slow

# Recent API-related runs with errors
langsmith-cli runs list --recent --name-pattern "*api*" --status error

# Complex combination with flexible filters
langsmith-cli runs list \
  --tag staging \
  --min-latency 2s \
  --max-latency 10s \
  --last 48h \
  --name-pattern "*checkout*"

# Find moderately slow LLM runs from last week
langsmith-cli runs list \
  --run-type llm \
  --min-latency 1s \
  --max-latency 5s \
  --last 7d
```

**How it works**: Multiple filters are combined with FQL `and()` operator.

## Table Sorting

Sort runs and projects by different fields:

### Runs Sorting

```bash
# Sort by name (ascending)
langsmith-cli runs list --sort-by name

# Sort by latency (descending)
langsmith-cli runs list --sort-by -latency

# Sort by status
langsmith-cli runs list --sort-by status

# Sort by start time
langsmith-cli runs list --sort-by -start_time
```

**Available sort fields**: `name`, `status`, `latency`, `start_time`

### Projects Sorting

```bash
# Sort by name (ascending)
langsmith-cli projects list --sort-by name

# Sort by run count (descending)
langsmith-cli projects list --sort-by -run_count
```

**Available sort fields**: `name`, `run_count`

**How it works**: Client-side sorting using Python's `sorted()` function. Prefix with `-` for descending order.

## Projects Support

Projects support pattern, regex filtering, activity filtering, and sorting:

```bash
# Wildcard matching - Find projects with "prod" in the name
langsmith-cli projects list --name-pattern "*prod*"

# Regex matching - Find projects matching versioned pattern
langsmith-cli projects list --name-regex "^prod-.*-v[0-9]+$"

# Find projects starting with "staging"
langsmith-cli projects list --name-regex "^staging"

# Show only active projects (with runs)
langsmith-cli projects list --has-runs

# Sort by activity level
langsmith-cli projects list --has-runs --sort-by -run_count
```

**How it works**: Wildcards, regex, and activity filtering use client-side filtering after fetching from API.

### P1 Features (Phase 2)

#### Export Formats

Export runs and projects in multiple formats for analysis, sharing, and integration:

```bash
# Export runs as CSV for spreadsheet analysis
langsmith-cli runs list --format csv > runs.csv

# Export as YAML for configuration/documentation
langsmith-cli runs list --format yaml > runs.yaml

# Export as JSON (default when using --json flag)
langsmith-cli runs list --format json > runs.json
langsmith-cli --json runs list > runs.json  # Alternative

# Export projects in different formats
langsmith-cli projects list --format csv > projects.csv
langsmith-cli projects list --format yaml > projects.yaml

# Combine with filters
langsmith-cli runs list --failed --last 24h --format csv > errors.csv
langsmith-cli projects list --has-runs --format yaml > active_projects.yaml
```

**Supported formats**: `table` (default), `json`, `csv`, `yaml`

**Use Cases**:
- Share run data with non-technical stakeholders (CSV for Excel)
- Export for data analysis pipelines
- Documentation and version control (YAML)
- Integration with other tools (JSON)

#### Enhanced Full-Text Search

Natural language search across all run fields with targeted field filtering:

```bash
# Basic full-text search
langsmith-cli runs search "authentication failed"

# Search with field-specific filters
langsmith-cli runs search "error" --input-contains "user_id"
langsmith-cli runs search "timeout" --output-contains "retry"

# Combine with other filters
langsmith-cli runs search "gpt-4" --limit 20 --format csv

# Search specific content areas
langsmith-cli runs search "password reset" --in inputs
langsmith-cli runs search "timeout" --in error
```

**How it works**: Uses FQL `search()` for server-side full-text search, combines multiple search terms with AND logic.

**Benefits over `runs list --filter`**:
- Simpler syntax (no FQL required)
- Positional argument instead of flag
- Field-specific search options
- Natural language queries

#### Field Value Search

Search for specific content in inputs and outputs:

```bash
# Search for user IDs in inputs
langsmith-cli runs search "process" --input-contains "user_123"

# Find runs with specific error messages
langsmith-cli runs search "failed" --output-contains "connection timeout"

# Combine multiple field searches
langsmith-cli runs search "api" --input-contains "endpoint" --output-contains "status"
```

**Use Cases**:
- Find runs handling specific user IDs
- Debug by searching error messages in outputs
- Audit for sensitive data in inputs/outputs
- Trace data flow through the system

## Implementation Details

### FQL Translation

All user-friendly flags are translated to LangSmith Filter Query Language (FQL):

| Flag | FQL Translation |
|------|----------------|
| `--tag foo` | `has(tags, "foo")` |
| `--name-pattern "*auth*"` | `search("auth")` |
| `--model gpt-4` | `search("gpt-4")` |
| `--failed` | `error=True` |
| `--succeeded` | `error=False` |
| `--slow` | `gt(latency, "5s")` |
| `--recent` | `gt(start_time, "<1-hour-ago>")` |
| `--today` | `gt(start_time, "<midnight>")` |
| `--min-latency 2s` | `gt(latency, "2s")` |
| `--max-latency 10s` | `lt(latency, "10s")` |
| `--last 24h` | `gt(start_time, "<24-hours-ago>")` |
| `--since "2024-01-14T10:00:00Z"` | `gt(start_time, "2024-01-14T10:00:00...")` |
| `--name-regex "^test.*"` | Client-side regex filtering |
| `--sort-by name` | Client-side sorting |

### Multiple Filters

When multiple flags are used, they're combined with AND logic:

```python
# Input: --tag prod --slow --name-pattern "*api*"
# FQL: and(has(tags, "prod"), gt(latency, "5s"), search("api"))
```

### Custom Filters

The `--filter` flag still works and is combined with new flags:

```bash
langsmith-cli runs list --tag prod --filter 'eq(run_type, "llm")'
# FQL: and(has(tags, "prod"), eq(run_type, "llm"))
```

## Testing

All features have unit test coverage:

- `test_runs_list_with_tags()` - Tag filtering
- `test_runs_list_with_name_pattern()` - Wildcard matching
- `test_runs_list_with_smart_filters()` - Smart filter flags
- `test_runs_list_combined_filters()` - Multiple filters together

Run tests with:
```bash
uv run pytest tests/test_runs.py -v
```

## Examples

### Debug Production Issues

```bash
# Find recent errors in production
langsmith-cli runs list --tag production --failed --recent

# Find slow production runs, sorted by latency
langsmith-cli runs list --tag production --slow --sort-by -latency --limit 50

# Find failed GPT-4 runs in production
langsmith-cli runs list --tag production --model gpt-4 --failed
```

### Performance Analysis

```bash
# Find runs with specific latency characteristics
langsmith-cli runs list --min-latency 2s --max-latency 5s --run-type llm

# Moderately slow runs from last 24 hours
langsmith-cli runs list --min-latency 1s --max-latency 3s --last 24h

# Export latency data for analysis
langsmith-cli --json runs list --min-latency 2s --last 7d \
  | jq -r '.[] | [.id, .name, .latency] | @csv'
```

### Time-Based Analysis

```bash
# Compare runs from different time periods
langsmith-cli runs list --since "2024-01-10T00:00:00Z" --last 24h

# Weekly performance review
langsmith-cli runs list --last 7d --min-latency 5s

# Find issues from specific deployment
langsmith-cli runs list --since "2024-01-14T15:30:00Z" --status error
```

### Pattern-Based Debugging

```bash
# Find authentication-related runs with errors (wildcard)
langsmith-cli runs list --name-pattern "*auth*" --failed

# Search for checkout flows with specific latency (wildcard)
langsmith-cli runs list --name-pattern "*checkout*" --min-latency 3s

# Find versioned test runs with errors (regex)
langsmith-cli runs list --name-regex "^test-.*-v[0-9]+$" --failed

# Find specific service endpoints (regex)
langsmith-cli runs list --name-regex "^(api|web)-service" --recent
```

### Model-Specific Analysis

```bash
# Compare GPT-4 vs Claude performance
langsmith-cli runs list --model gpt-4 --sort-by -latency --limit 10
langsmith-cli runs list --model claude --sort-by -latency --limit 10

# Find slow Claude runs
langsmith-cli runs list --model claude --min-latency 3s

# Find failed runs for specific model version
langsmith-cli runs list --model gpt-4-turbo --failed --last 24h
```

### Project Management

```bash
# Find active projects sorted by activity
langsmith-cli projects list --has-runs --sort-by -run_count

# Find production projects with runs
langsmith-cli projects list --name-pattern "*prod*" --has-runs

# List projects alphabetically
langsmith-cli projects list --sort-by name

# Export active projects for reporting
langsmith-cli projects list --has-runs --format csv > active_projects.csv
```

### Data Export and Analysis

```bash
# Export failed runs for analysis
langsmith-cli runs list --failed --last 7d --format csv > errors_last_week.csv

# Export all runs with specific model
langsmith-cli runs list --model gpt-4 --format yaml > gpt4_runs.yaml

# Export search results
langsmith-cli runs search "timeout" --format csv > timeout_issues.csv

# Export projects for documentation
langsmith-cli projects list --format yaml > projects_inventory.yaml
```

### Advanced Search Scenarios

```bash
# Find authentication errors
langsmith-cli runs search "authentication" --output-contains "failed"

# Search for specific user activity
langsmith-cli runs search "user_id" --input-contains "12345"

# Find API timeout issues
langsmith-cli runs search "timeout" --output-contains "api" --last 24h

# Search and export results
langsmith-cli runs search "error" --input-contains "payment" --format csv > payment_errors.csv
```

## Future Enhancements

See [QOL_IMPROVEMENTS.md](QOL_IMPROVEMENTS.md) for planned Phase 2 and Phase 3 features:

- Full-text search command
- Export formats (CSV, YAML, JSON)
- Run comparison/diff
- Schema extraction
- Batch operations
- Interactive TUI mode

## Design Principles

1. **Server-side filtering**: Use FQL for efficiency - don't fetch everything and filter client-side
2. **Composability**: All flags work together naturally
3. **Backwards compatible**: Existing `--filter` flag still works
4. **Type-safe**: All FQL generation is type-checked
5. **Tested**: Every feature has unit test coverage

## Related Documentation

- [MCP_PARITY.md](dev/MCP_PARITY.md) - Feature parity with LangSmith MCP server
- [QOL_IMPROVEMENTS.md](QOL_IMPROVEMENTS.md) - Full analysis of QoL improvements
- [COMMANDS_DESIGN.md](COMMANDS_DESIGN.md) - Command design principles
