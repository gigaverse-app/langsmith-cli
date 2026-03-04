## Datasets

### `datasets list`

List datasets with filtering.

```bash
langsmith-cli --json datasets list [OPTIONS]
```

**Options:**
- `--limit INTEGER` - Maximum results (default: 20)
- `--name TEXT` - Filter by exact dataset name
- `--name-contains TEXT` - Filter by name substring
- `--dataset-ids TEXT` - Comma-separated list of dataset UUIDs
- `--data-type TEXT` - Filter by type: `kv`, `llm`, or `chat`
- `--metadata TEXT` - Filter by metadata (JSON string)
- `--exclude TEXT` - Exclude items containing substring (repeatable)
- `--fields TEXT` - Comma-separated field names to include
- `--count` - Output only the count of results
- `--output TEXT` - Write output to file (JSONL format)

**Output Fields:**
- `id` (UUID) - Dataset identifier
- `name` (string) - Dataset name
- `description` (string|null) - Dataset description
- `data_type` (string) - Type: kv, llm, or chat
- `created_at` (datetime) - Creation timestamp
- `modified_at` (datetime) - Last modified timestamp
- `example_count` (integer) - Number of examples
- `metadata` (object|null) - Custom metadata

**Examples:**
```bash
# All datasets
langsmith-cli --json datasets list --limit 20

# Search by name
langsmith-cli --json datasets list --name-contains "test"

# Filter by type
langsmith-cli --json datasets list --data-type llm
```

### `datasets get`

Get dataset details.

```bash
langsmith-cli --json datasets get <dataset-id> [OPTIONS]
```

**Arguments:**
- `dataset-id` (required) - Dataset UUID

**Options:**
- `--fields TEXT` - Comma-separated field names to include
- `--output TEXT` - Write output to file (JSON format)

**Output:** Complete dataset object with all metadata

**Example:**
```bash
langsmith-cli --json datasets get "ae99b6fa-a6db-4f1c-8868-bc6764f4c29e"
```

### `datasets create`

Create a new dataset.

```bash
langsmith-cli --json datasets create <name> [OPTIONS]
```

**Arguments:**
- `name` (required) - Dataset name

**Options:**
- `--description TEXT` - Dataset description
- `--type [kv|llm|chat]` - Dataset type (default: kv)

**Output:** Created dataset object

**Example:**
```bash
langsmith-cli --json datasets create "qa-pairs" \
  --description "Question answering test set" \
  --type kv
```

### `datasets delete`

Delete a dataset by name or ID.

```bash
langsmith-cli --json datasets delete <name-or-id> --confirm
```

**Arguments:**
- `name-or-id` (required) - Dataset name or UUID (auto-detected)

**Options:**
- `--confirm` - Skip confirmation prompt (required for non-interactive use)

**Output:** `{"status": "success", "name": "<dataset-name>"}`

**Example:**
```bash
langsmith-cli --json datasets delete "old-test-data" --confirm
```

### `datasets push`

Bulk upload examples from JSONL file.

```bash
langsmith-cli --json datasets push <file.jsonl> [OPTIONS]
```

**Arguments:**
- `file.jsonl` (required) - Path to JSONL file

**Options:**
- `--dataset TEXT` - Target dataset name (creates if doesn't exist; defaults to filename)

**JSONL Format:**
```jsonl
{"inputs": {"query": "What is AI?"}, "outputs": {"answer": "Artificial Intelligence..."}}
{"inputs": {"query": "Define ML"}, "outputs": {"answer": "Machine Learning..."}}
```

**Output:** Upload summary with count of examples added

**Example:**
```bash
langsmith-cli --json datasets push examples.jsonl --dataset "my-dataset"
```
