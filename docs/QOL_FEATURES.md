# Quality of Life Features

This document describes the implemented quality-of-life improvements for langsmith-cli.

## Implemented Features (P0)

### Tag Filtering

Filter runs by tags with intuitive syntax:

```bash
# Single tag
langsmith-cli runs list --tag production

# Multiple tags (AND logic - all must be present)
langsmith-cli runs list --tag production --tag experimental
```

**How it works**: Converts to FQL `has(tags, "value")` under the hood.

### Name Pattern Matching

Search runs by name with wildcard support:

```bash
# Find runs with "auth" in the name
langsmith-cli runs list --name-pattern "*auth*"

# Find runs starting with "test-"
langsmith-cli runs list --name-pattern "test-*"
```

**How it works**: Converts wildcards to FQL `search()` function for server-side filtering.

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

## Implementation Details

### FQL Translation

All user-friendly flags are translated to LangSmith Filter Query Language (FQL):

| Flag | FQL Translation |
|------|----------------|
| `--tag foo` | `has(tags, "foo")` |
| `--name-pattern "*auth*"` | `search("auth")` |
| `--slow` | `gt(latency, "5s")` |
| `--recent` | `gt(start_time, "<1-hour-ago>")` |
| `--today` | `gt(start_time, "<midnight>")` |
| `--min-latency 2s` | `gt(latency, "2s")` |
| `--max-latency 10s` | `lt(latency, "10s")` |
| `--last 24h` | `gt(start_time, "<24-hours-ago>")` |
| `--since "2024-01-14T10:00:00Z"` | `gt(start_time, "2024-01-14T10:00:00...")` |

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
langsmith-cli runs list --tag production --status error --recent

# Find slow production runs
langsmith-cli runs list --tag production --slow --limit 50
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
# Find authentication-related runs with errors
langsmith-cli runs list --name-pattern "*auth*" --status error

# Search for checkout flows with specific latency
langsmith-cli runs list --name-pattern "*checkout*" --min-latency 3s
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

- [MCP_PARITY.md](../MCP_PARITY.md) - Feature parity with LangSmith MCP server
- [QOL_IMPROVEMENTS.md](QOL_IMPROVEMENTS.md) - Full analysis of QoL improvements
- [COMMANDS_DESIGN.md](COMMANDS_DESIGN.md) - Command design principles
