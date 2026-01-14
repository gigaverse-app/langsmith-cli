# LangSmith CLI Real-World Examples

This document provides practical workflows and use cases for common LangSmith operations.

## Table of Contents

1. [Debugging Workflows](#debugging-workflows)
2. [Dataset Management](#dataset-management)
3. [Prompt Engineering](#prompt-engineering)
4. [Production Monitoring](#production-monitoring)
5. [Advanced Filtering](#advanced-filtering)
6. [Batch Operations](#batch-operations)
7. [Integration Patterns](#integration-patterns)

## Debugging Workflows

### Find and Inspect Recent Errors

**Scenario:** Your application is failing and you need to quickly identify the issue.

```bash
# Step 1: Find recent errors
langsmith-cli --json runs list \
  --project production-app \
  --status error \
  --limit 5 \
  --order-by -start_time

# Step 2: Inspect specific failure (context-efficient)
langsmith-cli --json runs get <run-id> \
  --fields inputs,outputs,error,start_time,end_time,name

# Step 3: Check if it's a pattern (find similar errors)
langsmith-cli --json runs search "ConnectionTimeout" \
  --project production-app \
  --limit 10
```

### Debug a Specific Trace Tree

**Scenario:** A user reported an issue with request ID `abc-123`. Find all spans in that trace.

```bash
# Step 1: Find the root trace
langsmith-cli --json runs list \
  --project production-app \
  --filter 'search("abc-123")' \
  --is-root true

# Step 2: Get all runs in the trace tree
langsmith-cli --json runs list \
  --trace-id <trace-uuid> \
  --limit 100

# Step 3: Focus on errors in the trace
langsmith-cli --json runs list \
  --trace-id <trace-uuid> \
  --status error

# Step 4: Inspect LLM calls specifically
langsmith-cli --json runs list \
  --trace-id <trace-uuid> \
  --run-type llm

# Step 5: Open full trace in browser for detailed inspection
langsmith-cli runs open <trace-id>
```

### Find Slow Requests

**Scenario:** Find requests taking longer than 5 seconds to optimize performance.

```bash
# Step 1: Find slow runs
langsmith-cli --json runs list \
  --project production-app \
  --filter 'gt(latency, "5s")' \
  --limit 20 \
  --order-by -latency

# Step 2: Analyze latency distribution
langsmith-cli --json runs stats \
  --project production-app \
  --limit 1000

# Step 3: Find specific slow component (e.g., retriever)
langsmith-cli --json runs list \
  --project production-app \
  --run-type retriever \
  --filter 'gt(latency, "2s")' \
  --limit 10
```

### Analyze Token Usage

**Scenario:** Understand token consumption to optimize costs.

```bash
# Step 1: Get stats for recent runs
langsmith-cli --json runs stats \
  --project production-app \
  --limit 1000

# Step 2: Find high token usage runs
langsmith-cli --json runs list \
  --project production-app \
  --filter 'gt(total_tokens, 5000)' \
  --limit 20

# Step 3: Inspect specific high-cost run
langsmith-cli --json runs get <run-id> \
  --fields inputs,total_tokens,prompt_tokens,completion_tokens,total_cost
```

## Dataset Management

### Create a Dataset from Production Traces

**Scenario:** Build a test dataset from real production examples.

```bash
# Step 1: Find successful runs with specific criteria
langsmith-cli --json runs list \
  --project production-app \
  --status success \
  --filter 'has(tags, "annotated")' \
  --is-root true \
  --limit 50 > successful_runs.json

# Step 2: Create dataset
langsmith-cli --json datasets create "prod-golden-set" \
  --description "Curated examples from production" \
  --type kv

# Step 3: Extract inputs/outputs and create examples (requires scripting)
# Parse successful_runs.json and create examples:
for run in runs:
    langsmith-cli --json examples create \
      --dataset "prod-golden-set" \
      --inputs "$(jq '.inputs' <<< "$run")" \
      --outputs "$(jq '.outputs' <<< "$run")" \
      --metadata "{\"source_run_id\": \"$run_id\"}"
```

### Bulk Upload Dataset from File

**Scenario:** You have a CSV/JSON file with test cases to upload.

```bash
# Step 1: Convert CSV to JSONL format (requires jq or python)
# Format: {"inputs": {...}, "outputs": {...}}

# Step 2: Upload to LangSmith
langsmith-cli --json datasets push "my-test-dataset" examples.jsonl \
  --description "Test cases from CSV" \
  --type kv
```

**JSONL Format Example:**
```jsonl
{"inputs": {"query": "What is the weather?"}, "outputs": {"answer": "I need location"}}
{"inputs": {"query": "Capital of France"}, "outputs": {"answer": "Paris"}}
{"inputs": {"query": "Translate hello to Spanish"}, "outputs": {"answer": "Hola"}}
```

### Organize Dataset with Splits and Metadata

**Scenario:** Create train/test splits with metadata for ML workflows.

```bash
# Create dataset
langsmith-cli --json datasets create "qa-evaluation" \
  --description "Q&A pairs for evaluation" \
  --type kv

# Add training examples with metadata
langsmith-cli --json examples create \
  --dataset "qa-evaluation" \
  --inputs '{"question": "What is AI?"}' \
  --outputs '{"answer": "Artificial Intelligence"}' \
  --metadata '{"difficulty": "easy", "category": "definitions"}' \
  --split train

# Add test examples
langsmith-cli --json examples create \
  --dataset "qa-evaluation" \
  --inputs '{"question": "Explain neural networks"}' \
  --outputs '{"answer": "Neural networks are..."}' \
  --metadata '{"difficulty": "hard", "category": "technical"}' \
  --split test

# Query specific split
langsmith-cli --json examples list \
  --dataset "qa-evaluation" \
  --splits train \
  --limit 100

# Query by metadata
langsmith-cli --json examples list \
  --dataset "qa-evaluation" \
  --metadata '{"difficulty": "hard"}' \
  --limit 20
```

### Version Datasets

**Scenario:** Create versioned snapshots of a dataset.

```bash
# View current dataset
langsmith-cli --json datasets get "my-dataset"

# Add new examples
langsmith-cli --json examples create \
  --dataset "my-dataset" \
  --inputs '{"query": "new question"}' \
  --outputs '{"answer": "new answer"}'

# Query examples as of specific time
langsmith-cli --json examples list \
  --dataset "my-dataset" \
  --as-of "2024-01-01T00:00:00Z" \
  --limit 100
```

## Prompt Engineering

### Iterate on Prompts with Versioning

**Scenario:** Develop and test multiple prompt versions.

```bash
# Step 1: Create initial prompt
cat > prompt_v1.json <<EOF
{
  "_type": "prompt",
  "input_variables": ["user_query"],
  "messages": [
    {"role": "system", "content": "You are a helpful assistant."},
    {"role": "user", "content": "{user_query}"}
  ]
}
EOF

langsmith-cli --json prompts push "my-assistant" prompt_v1.json \
  --description "Initial version" \
  --tags "v1"

# Step 2: Test in application, collect feedback

# Step 3: Create improved version
cat > prompt_v2.json <<EOF
{
  "_type": "prompt",
  "input_variables": ["user_query", "context"],
  "messages": [
    {"role": "system", "content": "You are a helpful assistant. Use the context provided."},
    {"role": "user", "content": "Context: {context}\n\nQuestion: {user_query}"}
  ]
}
EOF

langsmith-cli --json prompts push "my-assistant" prompt_v2.json \
  --description "Added context parameter" \
  --tags "v2"

# Step 4: Compare performance across versions
# Run evaluation on different prompt versions and compare stats
```

### Share Prompts with Team

**Scenario:** Publish a prompt for team use.

```bash
# Push prompt as public
langsmith-cli --json prompts push "team-qa-prompt" prompt.json \
  --description "Standard Q&A prompt for customer support" \
  --tags "production,customer-support" \
  --is-public true

# Team members can retrieve it
langsmith-cli --json prompts get "team-qa-prompt"

# Get specific version
langsmith-cli --json prompts get "team-qa-prompt" --commit <hash>
```

## Production Monitoring

### Monitor Real-Time Performance

**Scenario:** Watch live traces during a deployment.

```bash
# Terminal 1: Watch live traces
langsmith-cli runs watch --project production-app --refresh 2

# Terminal 2: Run load test
# Your application generates traces...

# Check stats periodically
langsmith-cli --json runs stats --project production-app --limit 100
```

### Daily Error Report

**Scenario:** Generate a daily report of errors for monitoring.

```bash
#!/bin/bash
# daily_error_report.sh

TODAY=$(date -u +"%Y-%m-%dT00:00:00Z")
PROJECT="production-app"

echo "Error Report for $PROJECT - $(date)"
echo "================================"

# Get error count and details
langsmith-cli --json runs list \
  --project "$PROJECT" \
  --status error \
  --filter "gt(start_time, \"$TODAY\")" \
  --limit 100 \
  > errors_today.json

# Count errors
ERROR_COUNT=$(jq '.items | length' errors_today.json)
echo "Total errors today: $ERROR_COUNT"

# Group by error type
jq -r '.items[] | .error' errors_today.json | sort | uniq -c | sort -rn

# Top 5 most common errors
echo ""
echo "Top 5 error patterns:"
jq -r '.items[] | .error' errors_today.json | \
  grep -oP '^\w+Error' | sort | uniq -c | sort -rn | head -5
```

### Track Success Rate Over Time

**Scenario:** Monitor application health metrics.

```bash
# Get stats for different time windows
langsmith-cli --json runs stats --project production-app --limit 100
langsmith-cli --json runs stats --project production-app --limit 1000
langsmith-cli --json runs stats --project production-app --limit 10000

# Filter by time range and calculate success rate
langsmith-cli --json runs list \
  --project production-app \
  --filter 'gt(start_time, "2024-01-01T00:00:00Z")' \
  --limit 1000 | \
  jq '{
    total: .items | length,
    successful: [.items[] | select(.status == "success")] | length,
    failed: [.items[] | select(.status == "error")] | length,
    success_rate: ([.items[] | select(.status == "success")] | length) / (.items | length) * 100
  }'
```

### Cost Analysis

**Scenario:** Track and optimize API costs.

```bash
# Get cost stats
langsmith-cli --json runs stats --project production-app --limit 1000

# Find expensive runs
langsmith-cli --json runs list \
  --project production-app \
  --filter 'gt(total_cost, 0.1)' \
  --limit 20 \
  --order-by -total_cost

# Analyze cost by component type
langsmith-cli --json runs list \
  --project production-app \
  --run-type llm \
  --limit 100 | \
  jq '[.items[] | {name, total_tokens, total_cost}] |
      group_by(.name) |
      map({name: .[0].name,
           count: length,
           avg_cost: ([.[].total_cost] | add / length),
           total_cost: [.[].total_cost] | add})'
```

## Advanced Filtering

### Complex Filter Queries

**Scenario:** Find specific patterns in production data.

```bash
# Find slow errors (latency > 5s AND errored)
langsmith-cli --json runs list \
  --project production-app \
  --filter 'and(gt(latency, "5s"), neq(error, null))' \
  --limit 20

# Find successful runs with high token usage
langsmith-cli --json runs list \
  --project production-app \
  --filter 'and(eq(status, "success"), gt(total_tokens, 3000))' \
  --limit 20

# Find runs with specific tag AND positive feedback
langsmith-cli --json runs list \
  --project production-app \
  --filter 'and(has(tags, "production"), eq(feedback_score, 1))' \
  --limit 50

# Find recent runs excluding specific tag
langsmith-cli --json runs list \
  --project production-app \
  --filter 'and(gt(start_time, "2024-01-01T00:00:00Z"), not(has(tags, "test")))' \
  --limit 100
```

### Trace-Level Filtering

**Scenario:** Find child runs based on root trace properties.

```bash
# Find all retriever calls in traces that got positive feedback
langsmith-cli --json runs list \
  --project production-app \
  --run-type retriever \
  --trace-filter 'and(eq(feedback_key, "user_score"), eq(feedback_score, 1))' \
  --limit 50

# Find LLM calls in traces that errored at the root level
langsmith-cli --json runs list \
  --project production-app \
  --run-type llm \
  --trace-filter 'neq(error, null)' \
  --limit 20

# Find any run in traces that contain a specific component
langsmith-cli --json runs list \
  --project production-app \
  --tree-filter 'eq(name, "DocumentRetriever")' \
  --limit 100
```

### Metadata-Based Queries

**Scenario:** Query examples by complex metadata criteria.

```bash
# Find hard examples that failed evaluation
langsmith-cli --json examples list \
  --dataset "eval-set" \
  --metadata '{"difficulty": "hard", "eval_result": "fail"}' \
  --limit 50

# Find production examples from specific user
langsmith-cli --json examples list \
  --dataset "prod-traces" \
  --metadata '{"source": "production", "user_id": "user-123"}' \
  --limit 20
```

## Batch Operations

### Export Dataset to JSON

**Scenario:** Export entire dataset for backup or external processing.

```bash
# Get dataset metadata
langsmith-cli --json datasets get "my-dataset" > dataset_metadata.json

# Export all examples (paginated)
OFFSET=0
LIMIT=100
TOTAL=1000  # Get from dataset metadata

while [ $OFFSET -lt $TOTAL ]; do
    langsmith-cli --json examples list \
      --dataset "my-dataset" \
      --offset $OFFSET \
      --limit $LIMIT \
      >> dataset_examples.jsonl
    OFFSET=$((OFFSET + LIMIT))
done
```

### Batch Create Examples

**Scenario:** Create multiple examples from a script.

```bash
#!/bin/bash
# batch_create_examples.sh

DATASET="my-dataset"

# Read from CSV or generate programmatically
while IFS=',' read -r input output category; do
    langsmith-cli --json examples create \
      --dataset "$DATASET" \
      --inputs "{\"query\": \"$input\"}" \
      --outputs "{\"answer\": \"$output\"}" \
      --metadata "{\"category\": \"$category\"}" \
      --split train
done < examples.csv
```

### Migrate Dataset Between Workspaces

**Scenario:** Copy dataset from dev to prod workspace.

```bash
# Export from dev workspace
export LANGSMITH_API_KEY="dev-key"
langsmith-cli --json datasets get "my-dataset" > dataset.json
langsmith-cli --json examples list --dataset "my-dataset" --limit 1000 > examples.json

# Import to prod workspace
export LANGSMITH_API_KEY="prod-key"

# Create dataset
DATASET_NAME=$(jq -r '.name' dataset.json)
DATASET_DESC=$(jq -r '.description' dataset.json)
DATASET_TYPE=$(jq -r '.data_type' dataset.json)

langsmith-cli --json datasets create "$DATASET_NAME" \
  --description "$DATASET_DESC" \
  --type "$DATASET_TYPE"

# Import examples
jq -c '.items[]' examples.json | while read -r example; do
    INPUTS=$(echo "$example" | jq -c '.inputs')
    OUTPUTS=$(echo "$example" | jq -c '.outputs')
    METADATA=$(echo "$example" | jq -c '.metadata // {}')

    langsmith-cli --json examples create \
      --dataset "$DATASET_NAME" \
      --inputs "$INPUTS" \
      --outputs "$OUTPUTS" \
      --metadata "$METADATA"
done
```

## Integration Patterns

### CI/CD Pipeline Integration

**Scenario:** Run evaluations in CI and fail build on regressions.

```bash
#!/bin/bash
# ci_evaluation.sh

set -e

PROJECT="ci-test-$(date +%s)"

# Run tests and capture trace data in LangSmith
# Your test command here with LANGSMITH_PROJECT=$PROJECT

# Wait for traces to be available
sleep 5

# Get stats
STATS=$(langsmith-cli --json runs stats --project "$PROJECT" --limit 100)

# Extract success rate
SUCCESS_RATE=$(echo "$STATS" | jq -r '.success_rate')
THRESHOLD=95

echo "Success rate: $SUCCESS_RATE%"

if (( $(echo "$SUCCESS_RATE < $THRESHOLD" | bc -l) )); then
    echo "ERROR: Success rate below threshold ($THRESHOLD%)"

    # Print errors for debugging
    langsmith-cli --json runs list \
      --project "$PROJECT" \
      --status error \
      --limit 10 | jq -r '.items[] | "\(.name): \(.error)"'

    exit 1
fi

echo "Tests passed!"
```

### Alerting on Error Patterns

**Scenario:** Monitor for specific error patterns and alert.

```bash
#!/bin/bash
# alert_on_errors.sh

PROJECT="production-app"
LOOKBACK_MINUTES=5
ERROR_THRESHOLD=10

# Get recent errors
LOOKBACK=$(date -u -d "$LOOKBACK_MINUTES minutes ago" +"%Y-%m-%dT%H:%M:%SZ")

ERRORS=$(langsmith-cli --json runs list \
  --project "$PROJECT" \
  --status error \
  --filter "gt(start_time, \"$LOOKBACK\")" \
  --limit 100)

ERROR_COUNT=$(echo "$ERRORS" | jq '.items | length')

if [ "$ERROR_COUNT" -gt "$ERROR_THRESHOLD" ]; then
    echo "ALERT: $ERROR_COUNT errors in last $LOOKBACK_MINUTES minutes"

    # Send to alerting system (e.g., PagerDuty, Slack)
    # curl -X POST $WEBHOOK_URL -d "{\"text\": \"High error rate: $ERROR_COUNT errors\"}"

    # Log error patterns
    echo "$ERRORS" | jq -r '.items[] | .error' | sort | uniq -c | sort -rn
fi
```

### Jupyter Notebook Analysis

**Scenario:** Analyze LangSmith data in Python notebooks.

```python
import subprocess
import json
import pandas as pd

def get_runs(project, limit=100):
    """Fetch runs from LangSmith CLI."""
    result = subprocess.run(
        ["langsmith-cli", "--json", "runs", "list",
         "--project", project,
         "--limit", str(limit)],
        capture_output=True,
        text=True
    )
    data = json.loads(result.stdout)
    return pd.DataFrame(data['items'])

# Load data
df = get_runs("production-app", limit=1000)

# Analyze
print(f"Total runs: {len(df)}")
print(f"Success rate: {(df['status'] == 'success').mean() * 100:.2f}%")
print(f"\nLatency stats:")
print(df['latency'].describe())

# Find patterns
errors = df[df['status'] == 'error']
print(f"\nTop error patterns:")
print(errors['error'].value_counts().head(10))
```

### Stream Processing

**Scenario:** Process new runs in real-time.

```bash
#!/bin/bash
# stream_processor.sh

PROJECT="production-app"
POLL_INTERVAL=10
LAST_SEEN=""

while true; do
    # Get recent runs since last check
    if [ -z "$LAST_SEEN" ]; then
        FILTER=""
    else
        FILTER="--filter 'gt(start_time, \"$LAST_SEEN\")'"
    fi

    RUNS=$(langsmith-cli --json runs list \
      --project "$PROJECT" \
      --limit 100 \
      --order-by -start_time \
      $FILTER)

    # Process each new run
    echo "$RUNS" | jq -c '.items[]' | while read -r run; do
        RUN_ID=$(echo "$run" | jq -r '.id')
        STATUS=$(echo "$run" | jq -r '.status')

        # Custom processing logic
        echo "Processing run $RUN_ID with status $STATUS"

        # Update last seen timestamp
        LAST_SEEN=$(echo "$run" | jq -r '.start_time')
    done

    sleep $POLL_INTERVAL
done
```

## Tips and Best Practices

### Context Efficiency

1. **Always use `--fields` with `runs get`:**
   ```bash
   # Good: ~1KB response
   langsmith-cli --json runs get <id> --fields inputs,outputs,error

   # Bad: ~20KB response
   langsmith-cli --json runs get <id>
   ```

2. **Use filters before fetching:**
   ```bash
   # Good: Filter first, then get details
   langsmith-cli --json runs list --status error --limit 5
   langsmith-cli --json runs get <id> --fields error,inputs

   # Bad: Fetch all, then filter locally
   langsmith-cli --json runs list --limit 100  # Too much data
   ```

3. **Progressive disclosure:**
   ```bash
   # 1. Find the right data (small limit)
   langsmith-cli --json runs list --limit 5

   # 2. Get minimal details
   langsmith-cli --json runs get <id> --fields name,status

   # 3. Only if needed, get full details or open in browser
   langsmith-cli runs open <id>
   ```

### Scripting Best Practices

1. **Always check exit codes:**
   ```bash
   if ! langsmith-cli --json runs list --project myapp > runs.json; then
       echo "Failed to fetch runs"
       exit 1
   fi
   ```

2. **Use jq for JSON processing:**
   ```bash
   # Extract specific fields
   langsmith-cli --json runs list | jq -r '.items[] | "\(.id): \(.status)"'

   # Filter in jq
   langsmith-cli --json runs list | jq '.items[] | select(.status == "error")'
   ```

3. **Handle pagination:**
   ```bash
   fetch_all_examples() {
       local dataset=$1
       local offset=0
       local limit=100

       while true; do
           local batch=$(langsmith-cli --json examples list \
               --dataset "$dataset" \
               --offset $offset \
               --limit $limit)

           local count=$(echo "$batch" | jq '.items | length')

           if [ "$count" -eq 0 ]; then
               break
           fi

           echo "$batch" | jq -c '.items[]'
           offset=$((offset + limit))
       done
   }
   ```

## Additional Resources

- [SKILL.md](SKILL.md) - Quick reference and core commands
- [REFERENCE.md](REFERENCE.md) - Complete parameter documentation
- [MCP_PARITY.md](../../docs/dev/MCP_PARITY.md) - MCP compatibility mapping
- [LangSmith Documentation](https://docs.smith.langchain.com) - Official docs
