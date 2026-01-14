# Quality of Life Improvements - Analysis

## User's Specific Requests

### 1. Wildcards/Regex for Projects and Runs
**Current**: Must use exact names or FQL filter strings
```bash
# Current - exact match only
langsmith-cli runs list --project "my-project"

# Desired - pattern matching
langsmith-cli runs list --project "*auth*"
langsmith-cli runs list --name-regex "test-.*-v[0-9]+"
```

**Implementation Options**:
- A) Add `--name-pattern` flag (shell-style wildcards: `*`, `?`)
- B) Add `--name-regex` flag (full regex)
- C) Use FQL `search()` function server-side
- D) Client-side filtering after fetch

**Recommendation**: Option A + C hybrid
- Convert wildcards to FQL `search()` for server-side filtering
- Falls back to client-side if FQL doesn't support the pattern

### 2. Downloading Schemas of Runs
**Current**: Get full run data, manually inspect inputs/outputs
```bash
# Current - full run dump
langsmith-cli runs get abc-123 --fields inputs,outputs
```

**Desired**: Extract and format schemas
```bash
# Schema extraction
langsmith-cli runs schema abc-123                    # JSON Schema
langsmith-cli runs schema abc-123 --format typescript # TypeScript types
langsmith-cli runs schema abc-123 --format pydantic  # Pydantic models
```

**Use Cases**:
- Generate types for strongly-typed agent development
- Understand data structures without example values
- Documentation generation
- Contract validation

**Implementation**:
- Read run inputs/outputs
- Infer schema using type introspection
- Generate format-specific output (JSON Schema, TS, Pydantic)

### 3. Searching by Tags
**Current**: Must use FQL `has(tags, "value")`
```bash
# Current - FQL required
langsmith-cli runs list --filter 'has(tags, "production")'
```

**Desired**: Simple tag flag
```bash
# Desired - intuitive tag search
langsmith-cli runs list --tag production
langsmith-cli runs list --tag production --tag experimental  # AND
langsmith-cli runs list --tags production,staging            # OR?
```

**Implementation**:
- Add `--tag` flag (multiple allowed)
- Convert to FQL behind the scenes
- Default to AND logic (all tags must match)
- Add `--tag-match any` for OR logic

### 4. Searching by Field Values in Schema
**Current**: No easy way to search within inputs/outputs
```bash
# Current - not possible or requires complex FQL
langsmith-cli runs list --filter 'search("password reset")'  # searches everywhere
```

**Desired**: Targeted field search
```bash
# Desired - search specific fields
langsmith-cli runs search "password reset" --in inputs
langsmith-cli runs search "error" --in outputs.error
langsmith-cli runs list --input-contains "user_id=12345"
langsmith-cli runs list --output-has-key "api_key"  # find runs with this field
```

**Use Cases**:
- Find runs handling specific user IDs
- Debug by searching error messages
- Find runs with certain input patterns
- Audit for sensitive data

**Implementation Approaches**:
- A) FQL `search()` function (limited to full-text)
- B) Client-side JSON traversal (flexible but slow)
- C) Hybrid: fetch with FQL, refine client-side

## Additional High-Value Improvements

### 5. Smart Filters (Pre-defined Useful Queries)
```bash
langsmith-cli runs list --slow              # p95+ latency
langsmith-cli runs list --expensive         # high cost
langsmith-cli runs list --recent            # last hour
langsmith-cli runs list --today             # today only
langsmith-cli runs list --failures          # errors only (shorthand for --status error)
```

**Value**: Common debugging scenarios become one flag

### 6. Run Comparison
```bash
langsmith-cli runs diff abc-123 def-456
langsmith-cli runs diff abc-123 def-456 --fields inputs,outputs,latency
```

**Output**: Side-by-side comparison showing differences
- Inputs diff
- Outputs diff
- Metadata (latency, cost, tokens)

**Use Cases**:
- Compare before/after optimization
- Debug regression
- A/B test analysis

### 7. Full-Text Search Command
```bash
langsmith-cli runs search "authentication failed"
langsmith-cli runs search "timeout" --last 24h
langsmith-cli runs search "user_123" --in inputs --project prod
```

**Better than `list --filter`**: Natural language query, searches across all text fields

### 8. Export Formats
```bash
langsmith-cli runs export abc-123 --format yaml
langsmith-cli runs list --project prod --format csv > report.csv
langsmith-cli datasets export my-dataset --format parquet
```

**Use Cases**:
- Share runs with non-technical stakeholders (CSV)
- Version control (YAML)
- Data analysis (Parquet, CSV)

### 9. Batch Operations
```bash
langsmith-cli runs delete --filter 'gt(age, "30d")' --dry-run
langsmith-cli runs tag production --filter 'eq(status, "success")'
langsmith-cli examples update --dataset test --split train --filter '...'
```

**Value**: Bulk operations without writing scripts

### 10. Interactive/TUI Mode
```bash
langsmith-cli runs browse
langsmith-cli runs tui --project prod
```

**Features**:
- Arrow keys to navigate runs
- Press Enter to see details
- Filter/search with `/`
- Real-time updates

**Use Cases**:
- Exploratory debugging
- Monitoring production
- Better UX than repeatedly running `list` command

### 11. Presets/Templates
```yaml
# ~/.langsmith-cli/presets.yaml
presets:
  prod-errors:
    command: runs list
    options:
      project: production
      status: error
      limit: 50
      order-by: "-start_time"

  expensive-llm-calls:
    command: runs list
    options:
      run-type: llm
      filter: 'gt(total_cost, "0.01")'
```

```bash
langsmith-cli runs list --preset prod-errors
langsmith-cli runs list --preset expensive-llm-calls --last 7d
```

### 12. Feedback Integration
```bash
langsmith-cli runs feedback abc-123 --score 1.0 --comment "Perfect response"
langsmith-cli runs feedback abc-123 --key correctness --value true
```

**Use Cases**:
- Add feedback from CLI during testing
- Annotation workflows
- Dataset curation

### 13. Run Replay/Clone
```bash
langsmith-cli runs replay abc-123
langsmith-cli runs clone abc-123 --modify-input '{"temperature": 0.9}'
```

**Value**: Re-run for debugging, A/B testing

## Prioritization Matrix

| Feature | Value | Complexity | Priority |
|---------|-------|------------|----------|
| Tag search (`--tag`) | High | Low | **P0** |
| Smart filters (`--slow`, `--recent`) | High | Low | **P0** |
| Wildcard patterns | High | Low | **P0** |
| Field value search | High | Medium | **P1** |
| Full-text search | High | Low | **P1** |
| Export formats | High | Low | **P1** |
| Run comparison/diff | Medium | Medium | **P2** |
| Schema extraction | Medium | Medium | **P2** |
| Batch operations | High | Medium | **P2** |
| Presets/templates | Medium | Low | **P2** |
| Feedback integration | Low | Low | **P3** |
| Interactive TUI | High | High | **P3** |
| Run replay | Low | High | **P3** |

## Implementation Strategy

### Phase 1: Low-Hanging Fruit (P0)
1. Add `--tag` flag to runs/examples list
2. Add smart filters: `--slow`, `--expensive`, `--recent`, `--today`
3. Add `--name-pattern` for wildcard matching
4. All use FQL under the hood - no client-side filtering

### Phase 2: Search & Export (P1)
1. New `runs search` command for full-text
2. Add `--format csv/yaml/json` to all list commands
3. Field value search in inputs/outputs

### Phase 3: Advanced Features (P2)
1. `runs diff` command
2. `runs schema` command
3. Batch operations with `--dry-run` safety
4. Presets configuration file

### Phase 4: Interactive (P3)
1. TUI mode using Rich/Textual
2. Feedback commands
3. Run replay

## Technical Considerations

### SDK Support Check
Need to verify what's possible with current LangSmith SDK:
- ✅ FQL filtering (supported)
- ✅ Tag filtering (via FQL `has(tags, "value")`)
- ❓ Full-text search (check if `search()` works)
- ❓ Bulk operations (may need REST API)
- ❓ Feedback via SDK (check methods)

### Design Principles
1. **SDK-first**: Use SDK whenever possible
2. **FQL translation**: Convert user-friendly flags to FQL
3. **No breaking changes**: Add options, don't modify existing
4. **Context-efficient**: New commands respect `--json` and `--fields`
5. **Type-safe**: Use SDK Pydantic models
6. **Lazy loading**: Import heavy libraries only when needed

### Backwards Compatibility
- All new flags are optional
- Existing commands work unchanged
- New commands don't conflict with existing ones

## Recommended Starting Point

Implement Phase 1 (P0 features) first:
1. `--tag` flag
2. Smart filters
3. Wildcard patterns

These provide immediate value, low implementation risk, and establish patterns for Phase 2/3.

## Questions for User

1. **Tag Logic**: Should multiple `--tag` flags be AND or OR by default?
2. **Smart Filter Thresholds**: What defines "slow" or "expensive"? Configurable?
3. **Search Scope**: Should `runs search` be a new command or extend `runs list --search`?
4. **Export Priority**: Which formats matter most? CSV, YAML, JSON, or others?
5. **TUI vs CLI**: Worth building interactive mode or keep pure CLI?
