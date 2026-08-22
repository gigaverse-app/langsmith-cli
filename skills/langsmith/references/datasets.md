## Datasets

### `datasets list`

List datasets with filtering.

```bash
langsmith-cli --json datasets list [OPTIONS]
```

**Options:**
- `--source [cloud|archive|local]` - Read from one backend (default: `cloud`)
- `--archive-uri TEXT` - Archive root; defaults to `LANGSMITH_ARCHIVE_URI`
- `--local-dir DIRECTORY` - Override the platform-local dataset cache
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
langsmith-cli --json datasets get <dataset-id-or-name> [OPTIONS]
```

**Arguments:**
- `dataset-id-or-name` (required) - Dataset UUID, or a unique replica name

**Options:**
- `--source [cloud|archive|local]` - Read from one backend (default: `cloud`)
- `--as-of TEXT` - Replica version tag or ISO timestamp (default: `latest`)
- `--archive-uri TEXT` - Archive root; defaults to `LANGSMITH_ARCHIVE_URI`
- `--local-dir DIRECTORY` - Override the platform-local dataset cache
- `--fields TEXT` - Comma-separated field names to include
- `--output TEXT` - Write output to file (JSON format)

**Output:** Complete dataset object with all metadata

**Example:**
```bash
langsmith-cli --json datasets get "ae99b6fa-a6db-4f1c-8868-bc6764f4c29e"
```

### `datasets pull`

Copy exact, read-only dataset versions between backends. Cloud is the mutation
authority; archive and local replicas preserve LangSmith `Dataset`, `Example`, and
`DatasetVersion` fields in Parquet, with attachment bytes stored separately.

```bash
langsmith-cli --json datasets pull <dataset-id-or-name> --to local [OPTIONS]
```

**Options:**
- `--source [cloud|archive|local]` - Source backend (default: `cloud`)
- `--to [archive|local]` - Destination backend (required)
- `--as-of TEXT` - Exact version tag or ISO timestamp (default: `latest`)
- `--all-versions` - Replicate every version, oldest first
- `--archive-uri TEXT` - Archive root; defaults to `LANGSMITH_ARCHIVE_URI`
- `--local-dir DIRECTORY` - Override the platform-local dataset cache

```bash
# Freeze the latest cloud version for offline intermediate work
langsmith-cli --json datasets pull my-evals --to local

# Publish all cloud versions into the durable archive
langsmith-cli --json datasets pull my-evals --to archive --all-versions

# Hydrate local work from an archive without contacting LangSmith
langsmith-cli --json datasets pull my-evals --source archive --to local
```

Repeated pulls of the same exact version are idempotent. Use `--archive-uri` when
the command needs the archive; local data defaults to the OS cache directory.

### `datasets versions`

```bash
langsmith-cli --json datasets versions <dataset-id-or-name> \
  --source [cloud|archive|local]
```

Lists exact timestamps and tags available in the selected backend. Archive/local
versions are immutable replica snapshots.

### `datasets status`

```bash
langsmith-cli --json datasets status --source [archive|local]
```

Lists replicated dataset identities, latest timestamps, version counts, and the
resolved storage URI.

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
langsmith-cli --json datasets delete <name-or-id> --yes
```

**Arguments:**
- `name-or-id` (required) - Dataset name or UUID (auto-detected)

**Options:**
- `--yes`, `--confirm` - Skip confirmation prompt (required for non-interactive use)

**Output:** `{"status": "success", "name": "<dataset-name>"}`

**Example:**
```bash
langsmith-cli --json datasets delete "old-test-data" --yes
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
Rows must be JSON objects with an `inputs` object. `outputs` is optional, but if present it must be an object or `null`. Malformed rows fail fast with the JSONL line number.

**Output:** Upload summary with count of examples added

**Example:**
```bash
langsmith-cli --json datasets push examples.jsonl --dataset "my-dataset"
```
