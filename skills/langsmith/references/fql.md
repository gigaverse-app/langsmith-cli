## Filter Query Language (FQL)

Advanced filtering for `runs list` and `examples list` using FQL expressions.

### Comparison Operators

- `eq(field, value)` - Equal to
- `neq(field, value)` - Not equal to
- `gt(field, value)` - Greater than
- `gte(field, value)` - Greater than or equal
- `lt(field, value)` - Less than
- `lte(field, value)` - Less than or equal

### String Operators

- `search(text)` - Full-text search
- `has(field, value)` - Contains (for arrays/tags)

### Logical Operators

- `and(expr1, expr2, ...)` - Logical AND
- `or(expr1, expr2, ...)` - Logical OR
- `not(expr)` - Logical NOT

### Common Fields

Runs:
- `latency` - Run duration
- `total_tokens` - Token count
- `error` - Error field
- `tags` - Run tags
- `feedback_key`, `feedback_score` - Feedback data
- `metadata_key`, `metadata_value` - Metadata entries
- `start_time`, `end_time` - Timestamps

Examples:
- `metadata_key`, `metadata_value` - Metadata entries
- `created_at` - Creation timestamp

### Examples

```bash
# Runs with latency > 5 seconds
--filter 'gt(latency, "5s")'

# Errored runs
--filter 'neq(error, null)'

# Runs with specific tag
--filter 'has(tags, "production")'

# Complex: errored AND slow
--filter 'and(neq(error, null), gt(latency, "5s"))'

# Runs with positive feedback
--filter 'and(eq(feedback_key, "user_score"), eq(feedback_score, 1))'

# Recent runs (last hour)
--filter 'gt(start_time, "2024-01-01T00:00:00Z")'

# Full-text search in run data
--filter 'search("database connection")'

# Examples with metadata
--filter 'and(eq(metadata_key, "difficulty"), eq(metadata_value, "hard"))'
```

