# Questions from Partner Team on Stratified Sampling & Analytics Implementation

## Context

We (the langsmith-cli team) are implementing Approach A (metadata-based stratified sampling and analytics) which will be incredibly valuable for metamind and the entire LangSmith ecosystem.

This document contains clarification questions received from the partner team about our implementation details.

## 1. Stratified Sampling Command

**Question:** Will the stratified sampling command support both tag-based and metadata-based stratification?

### Tag-based syntax (from proposal):
```bash
langsmith-cli --json runs sample \
  --stratify-by "tag:length" \
  --values "short,medium,long" \
  --samples-per-stratum 10
```

### Metadata-based alternative:
```bash
langsmith-cli --json runs sample \
  --stratify-by "metadata:length_category" \
  --values "short,medium,long" \
  --samples-per-stratum 10
```

**Which syntax are you planning? Or both?**

## 2. Multi-Dimensional Stratification

**Question:** Will the command support Cartesian product sampling across multiple dimensions?

### Example:
```bash
# Sample across length × content_type combinations
langsmith-cli --json runs sample \
  --stratify-by "tag:length,tag:content_type" \
  --samples-per-combination 2
```

**Expected Result:** 2 samples for each (length, content_type) pair:
- (short, news)
- (short, gaming)
- (medium, news)
- (medium, gaming)
- (long, news)
- (long, gaming)

**Is this in scope for the initial implementation?**

## 3. Analytics Command - Supported Metrics

**Question:** For the group-by analytics command, which metrics will be supported?

```bash
langsmith-cli --json runs analyze \
  --group-by "tag:length" \
  --metrics "error_rate,p50_latency,p95_latency,count"
```

### Proposed metrics:
- ✅ Error rate - Confirmed?
- ✅ Latency percentiles (p50, p95, p99) - Confirmed?
- ✅ Count - Confirmed?
- ❓ Total tokens?
- ❓ Average cost?
- ❓ Success rate?
- ❓ Custom metrics via feedback scores?

**Which of these will be supported in the initial release?**

## 4. Output Format

**Question:** What will the output format be for both commands?

### For `runs sample`:

**Option A: JSONL with stratum annotation**
```jsonl
{"run_id": "abc", "stratum": "short", "inputs": {...}, "outputs": {...}}
{"run_id": "def", "stratum": "medium", "inputs": {...}, "outputs": {...}}
```

**Option B: Structured JSON**
```json
{
  "strata": {
    "short": [{"run_id": "abc", ...}, ...],
    "medium": [{"run_id": "def", ...}, ...],
    "long": [...]
  }
}
```

**Which format are you choosing?**

### For `runs analyze`:

**Option A: Array of group stats**
```json
[
  {"group": "short", "count": 650, "error_rate": 0.02, "p95_latency": 2.8},
  {"group": "medium", "count": 280, "error_rate": 0.05, "p95_latency": 6.7}
]
```

**Option B: Nested structure**
```json
{
  "groups": {
    "short": {"count": 650, "metrics": {"error_rate": 0.02, "p95_latency": 2.8}},
    "medium": {"count": 280, "metrics": {"error_rate": 0.05, "p95_latency": 6.7}}
  }
}
```

**Which format are you choosing?**

## 5. Filtering Integration

**Question:** Will these commands support existing FQL filters for additional filtering?

### Example: Stratify within a filtered subset
```bash
langsmith-cli --json runs sample \
  --filter 'and(has(tags, "schema:v2_4_6"), gte(start_time, "2026-01-01"))' \
  --stratify-by "tag:length" \
  --samples-per-stratum 10
```

### Example: Analyze only recent successful runs
```bash
langsmith-cli --json runs analyze \
  --filter 'and(eq(status, "success"), gte(start_time, "2026-01-01"))' \
  --group-by "tag:length" \
  --metrics "p95_latency,avg_cost"
```

**Will this filtering integration be supported?**

## 6. Metadata Key Discovery

**Question:** Will there be a way to discover available metadata keys/tags in a project?

### Proposed command:
```bash
# List all tag patterns in a project
langsmith-cli --json runs tags --project "prd/my-service"
```

### Example output:
```json
{
  "tag_patterns": {
    "length": ["short", "medium", "long"],
    "content_type": ["news", "gaming", "other"],
    "schema": ["v2_4_5", "v2_4_6", "v3"]
  }
}
```

### Alternative: List all metadata keys
```bash
langsmith-cli --json runs metadata-keys --project "prd/my-service"
```

**Is metadata key/tag discovery in scope? If so, what format?**

## Summary of Questions

1. **Stratification syntax:** Tag-based, metadata-based, or both?
2. **Multi-dimensional stratification:** Cartesian product sampling supported?
3. **Analytics metrics:** Which metrics will be available initially?
4. **Output formats:** JSONL vs structured JSON for both commands?
5. **Filter integration:** Support for combining `--filter` with stratification/grouping?
6. **Discovery:** Commands to list available tags/metadata keys?

## Use Case Context

These questions are driven by the Metamind use case where we need to:
- Sample diverse inputs (short/medium/long transcripts)
- Analyze failure patterns by input characteristics
- Combine stratification with time-based or schema-based filtering
- Discover which metadata dimensions are available for analysis

Having clarity on these questions will help us design the client-side integration and documentation.
