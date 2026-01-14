---
name: langsmith
description: Inspect and manage LangSmith traces, runs, datasets, and prompts using the 'langsmith-cli'.
---

# LangSmith Tool

Use this tool to debug AI chains, inspect past runs, or manage datasets and prompts in LangSmith.

## ⚡ Efficient Usage Guidelines (READ THIS)
1. **Machine Output:** ALWAYS add `--json` as the FIRST argument to `langsmith-cli` (e.g. `langsmith-cli --json runs list ...`) to get parseable output.
2. **Context Saving:** When inspecting a single run, use `--fields` to limit the data returned.
   - Example: `langsmith-cli --json runs get <id> --fields inputs,outputs,error`
3. **Filter Fast:** Use `--status error` to find failing runs quickly.
4. **Project Scope:** Always specify `--project` (default is "default") if you know it.

## API Reference

### Projects
- `langsmith-cli --json projects list`: List all projects.
- `langsmith-cli --json projects create <name>`: Create a new project.

### Runs (Traces)
- `langsmith-cli --json runs list [OPTIONS]`: List recent runs.
  - `--project <name>`: Filter by project.
  - `--limit <n>`: Max results (default 10, keep it small).
  - `--status <success|error>`: Filter by status.
  - `--filter <string>`: Advanced LangSmith query string.
- `langsmith-cli --json runs get <id> [OPTIONS]`: Get details of a single run.
  - `--fields <comma-separated-fields>`: Only return specific fields.
- `langsmith-cli --json runs stats --project <name>`: Get aggregate stats.
- `langsmith-cli --json runs open <id>`: Instruct the human to open this run in their browser.

### Datasets & Examples
- `langsmith-cli --json datasets list`: List datasets.
- `langsmith-cli --json datasets get <id>`: Get dataset details.
- `langsmith-cli --json datasets create <name>`: Create a dataset.
- `langsmith-cli --json examples list --dataset <name>`: List examples in a dataset.
- `langsmith-cli --json examples get <id>`: Get example details.
- `langsmith-cli --json examples create --dataset <name> --inputs <json> --outputs <json>`: Add an example.

### Prompts
- `langsmith-cli --json prompts list`: List prompt repositories.
- `langsmith-cli --json prompts get <name> [--commit <hash>]`: Fetch a prompt template.
- `langsmith-cli --json prompts push <name> <file_path>`: Push a local file as a prompt.

## Additional Resources

For complete documentation, see:

- **[Quick Reference](docs/reference.md)** - Fast command lookup
- **[Real-World Examples](docs/examples.md)** - Complete workflows and use cases

**Detailed API References:**
- [Projects](references/projects.md) - Project management
- [Runs](references/runs.md) - Trace inspection and debugging
- [Datasets](references/datasets.md) - Dataset operations
- [Examples](references/examples.md) - Example management
- [Prompts](references/prompts.md) - Prompt templates
- [FQL](references/fql.md) - Filter Query Language
- [Troubleshooting](references/troubleshooting.md) - Error handling & configuration
