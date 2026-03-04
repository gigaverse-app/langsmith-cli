## Projects

### `projects list`

List all LangSmith projects (sessions).

```bash
langsmith-cli --json projects list [OPTIONS]
```

**Options:**
- `--limit INTEGER` - Maximum number of projects to return (default: 100, use 0 for no limit)
- `--name TEXT` - Filter by exact project name
- `--name-pattern TEXT` - Filter by name with wildcards (e.g. `'*prod*'`)
- `--name-regex TEXT` - Filter by name with regex
- `--reference-dataset-name TEXT` - Filter projects by reference dataset name
- `--has-runs` - Show only projects with runs (run_count > 0)
- `--sort-by TEXT` - Sort by field (name, run_count). Prefix with `-` for descending
- `--exclude TEXT` - Exclude items containing substring (repeatable)
- `--fields TEXT` - Comma-separated field names to include
- `--count` - Output only the count of results
- `--output TEXT` - Write output to file (JSONL format)
- `--format [table|json|csv|yaml]` - Output format

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

**Output:** Created project object

**Example:**
```bash
langsmith-cli --json projects create "my-experiment" --description "Testing new prompt"
```

### `projects get`

Get details of a single project by name or ID.

```bash
langsmith-cli --json projects get <name-or-id> [OPTIONS]
```

**Arguments:**
- `name-or-id` (required) - Project name or UUID (UUIDs auto-detected)

**Options:**
- `--include-stats/--no-stats` - Include/exclude run statistics (default: include)
- `--fields TEXT` - Comma-separated fields to return
- `--output FILE` - Write output to file

**Output:** Full project object (same fields as `projects list`)

**Examples:**
```bash
# Get by name
langsmith-cli --json projects get "my-project"

# Get by UUID (auto-detected, saves an API call)
langsmith-cli --json projects get "f47ac10b-58cc-4372-a567-0e02b2c3d479"

# Get with field pruning
langsmith-cli --json projects get "my-project" --fields name,run_count,error_rate
```

### `projects update`

Update a project's name or description.

```bash
langsmith-cli --json projects update <name-or-id> [OPTIONS]
```

**Arguments:**
- `name-or-id` (required) - Project name or UUID

**Options:**
- `--name TEXT` - New project name
- `--description TEXT` - New project description

At least one of `--name` or `--description` is required.

**Output:** Updated project object

**Example:**
```bash
langsmith-cli --json projects update "old-name" --name "new-name" --description "Updated"
```

### `projects delete`

Delete a project.

```bash
langsmith-cli --json projects delete <name-or-id> [OPTIONS]
```

**Arguments:**
- `name-or-id` (required) - Project name or UUID

**Options:**
- `--confirm` - Skip confirmation prompt

**Output:** Success status

**Example:**
```bash
langsmith-cli --json projects delete "test-project" --confirm
```

