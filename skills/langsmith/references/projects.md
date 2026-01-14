## Projects

### `projects list`

List all LangSmith projects (sessions).

```bash
langsmith-cli --json projects list [OPTIONS]
```

**Options:**
- `--limit INTEGER` - Maximum number of projects to return (default: 10)
- `--name TEXT` - Filter by exact project name (regex supported)
- `--reference-dataset-id UUID` - Filter projects by reference dataset ID
- `--reference-dataset-name TEXT` - Filter projects by reference dataset name

**Output Fields:**
- `id` (UUID) - Project identifier
- `name` (string) - Project name
- `description` (string|null) - Project description
- `created_at` (datetime) - Creation timestamp
- `run_count` (integer) - Number of runs in project
- `latency_p50` (float|null) - Median latency in seconds
- `latency_p99` (float|null) - 99th percentile latency
- `first_start_time` (datetime|null) - First run start time
- `last_start_time` (datetime|null) - Most recent run start time
- `feedback_stats` (object|null) - Feedback statistics
- `total_tokens` (integer|null) - Total tokens used
- `prompt_tokens` (integer|null) - Prompt tokens
- `completion_tokens` (integer|null) - Completion tokens
- `total_cost` (float|null) - Total cost in USD

**Example:**
```bash
langsmith-cli --json projects list --limit 5
```

### `projects create`

Create a new project.

```bash
langsmith-cli --json projects create <name> [OPTIONS]
```

**Arguments:**
- `name` (required) - Project name

**Options:**
- `--description TEXT` - Project description
- `--reference-dataset-id UUID` - Associate with a dataset

**Output:** Created project object

**Example:**
```bash
langsmith-cli --json projects create "my-experiment" --description "Testing new prompt"
```

