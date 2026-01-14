# LangSmith CLI Quick Reference

Quick reference guide for all langsmith-cli commands. For detailed documentation, see the [references/](../references/) folder.

## Global Options

```bash
langsmith-cli --json <command> [options]
```

- `--json` - Output structured JSON (REQUIRED for agent use)
- `--help` - Show help for any command

## Quick Command Reference

### Authentication
```bash
langsmith-cli auth login                    # Set up API key
```

### Projects
```bash
langsmith-cli --json projects list          # List all projects
langsmith-cli --json projects create <name> # Create new project
```
📖 [Full Projects Reference](../references/projects.md)

### Runs (Traces)
```bash
# List runs
langsmith-cli --json runs list \
  --project <name> \
  --status error \
  --limit 10

# Get specific run (use --fields for efficiency!)
langsmith-cli --json runs get <id> \
  --fields inputs,outputs,error

# Statistics
langsmith-cli --json runs stats --project <name>

# Open in browser
langsmith-cli runs open <id>

# Live monitoring
langsmith-cli runs watch --project <name>
```
📖 [Full Runs Reference](../references/runs.md)

### Datasets
```bash
# List datasets
langsmith-cli --json datasets list

# Get dataset
langsmith-cli --json datasets get <id-or-name>

# Create dataset
langsmith-cli --json datasets create <name> \
  --description "..." \
  --type kv

# Bulk upload
langsmith-cli --json datasets push <name> file.jsonl
```
📖 [Full Datasets Reference](../references/datasets.md)

### Examples
```bash
# List examples
langsmith-cli --json examples list \
  --dataset <name> \
  --limit 10

# Get example
langsmith-cli --json examples get <id>

# Create example
langsmith-cli --json examples create \
  --dataset <name> \
  --inputs '{"query": "..."}' \
  --outputs '{"answer": "..."}'
```
📖 [Full Examples Reference](../references/examples.md)

### Prompts
```bash
# List prompts
langsmith-cli --json prompts list

# Get prompt
langsmith-cli --json prompts get <name>

# Push prompt
langsmith-cli --json prompts push <name> file.json \
  --description "..." \
  --tags tag1,tag2
```
📖 [Full Prompts Reference](../references/prompts.md)

## Essential Patterns

### 1. Debug Failed Traces
```bash
# Find errors
langsmith-cli --json runs list --status error --limit 5

# Inspect (context-efficient)
langsmith-cli --json runs get <id> --fields inputs,outputs,error
```

### 2. Context Efficiency
```bash
# ✅ Efficient (~1KB)
--fields inputs,outputs,error

# ❌ Inefficient (~20KB)
# No --fields flag
```

### 3. Filter for Speed
```bash
# Use filters to reduce results
--status error
--project myapp
--filter 'gt(latency, "5s")'
--run-type llm
```

## Advanced Features

### Filter Query Language (FQL)
```bash
# Slow runs
--filter 'gt(latency, "5s")'

# Errored runs
--filter 'neq(error, null)'

# Tagged runs
--filter 'has(tags, "production")'

# Complex queries
--filter 'and(neq(error, null), gt(latency, "5s"))'
```
📖 [Full FQL Reference](../references/fql.md)

### Environment Variables
```bash
export LANGSMITH_API_KEY="lsv2_pt_..."
export LANGSMITH_ENDPOINT="https://api.smith.langchain.com"
```

## Common Issues

**"Authentication failed"**
→ Run `langsmith-cli auth login`

**"Project not found"**
→ Run `langsmith-cli --json projects list`

**"Result set too large"**
→ Use filters: `--status error --limit 5`

📖 [Full Troubleshooting Guide](../references/troubleshooting.md)

## Complete Documentation

- **[Projects](../references/projects.md)** - Project management
- **[Runs](../references/runs.md)** - Trace inspection and debugging
- **[Datasets](../references/datasets.md)** - Dataset operations
- **[Examples](../references/examples.md)** - Example management
- **[Prompts](../references/prompts.md)** - Prompt templates
- **[FQL](../references/fql.md)** - Filter Query Language
- **[Troubleshooting](../references/troubleshooting.md)** - Error handling & tips

## Real-World Workflows

See **[docs/examples.md](examples.md)** for complete workflows including:
- Debugging failed traces
- Creating datasets from production
- Monitoring performance
- CI/CD integration
- Cost analysis
