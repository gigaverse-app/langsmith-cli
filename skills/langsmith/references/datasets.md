## Datasets

### `datasets list`

List datasets with filtering.

```bash
langsmith-cli --json datasets list [OPTIONS]
```

**Options:**
- `--limit INTEGER` - Maximum results (default: 10)
- `--name TEXT` - Filter by exact dataset name
- `--name-contains TEXT` - Filter by name substring (case-insensitive)
- `--dataset-ids TEXT` - Comma-separated list of dataset UUIDs
- `--data-type TEXT` - Filter by type: `kv`, `llm`, or `chat`
- `--metadata JSON` - Filter by metadata (JSON object)

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
langsmith-cli --json datasets get <dataset-id-or-name>
```

**Arguments:**
- `dataset-id-or-name` (required) - Dataset UUID or exact name

**Output:** Complete dataset object with all metadata

**Example:**
```bash
langsmith-cli --json datasets get "my-test-dataset"
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
- `--type TEXT` - Dataset type: `kv` (default), `llm`, or `chat`
- `--metadata JSON` - Custom metadata (JSON object)

**Output:** Created dataset object

**Example:**
```bash
langsmith-cli --json datasets create "qa-pairs" \
  --description "Question answering test set" \
  --type kv \
  --metadata '{"source": "production", "version": "1.0"}'
```

### `datasets push`

Bulk upload examples from JSONL file.

```bash
langsmith-cli --json datasets push <dataset-name> <file.jsonl> [OPTIONS]
```

**Arguments:**
- `dataset-name` (required) - Target dataset name (creates if doesn't exist)
- `file.jsonl` (required) - Path to JSONL file

**JSONL Format:**
```jsonl
{"inputs": {"query": "What is AI?"}, "outputs": {"answer": "Artificial Intelligence..."}}
{"inputs": {"query": "Define ML"}, "outputs": {"answer": "Machine Learning..."}}
```

**Options:**
- `--description TEXT` - Description for new dataset
- `--type TEXT` - Type for new dataset: `kv`, `llm`, or `chat`

**Output:** Upload summary with count of examples added

**Example:**
```bash
langsmith-cli --json datasets push "my-dataset" examples.jsonl
```

