## Examples

### `examples list`

List examples in a dataset with advanced filtering.

```bash
langsmith-cli --json examples list [OPTIONS]
```

**Options:**
- `--dataset TEXT` (required) - Dataset name or UUID
- `--limit INTEGER` - Maximum results (default: 10, max: 100)
- `--offset INTEGER` - Skip N examples (default: 0)
- `--example-ids TEXT` - Comma-separated list of example UUIDs
- `--filter TEXT` - Advanced FQL query
- `--metadata JSON` - Filter by metadata (JSON object)
- `--splits TEXT` - Comma-separated list of splits (e.g., "train,test")
- `--as-of TEXT` - Version tag or ISO timestamp
- `--inline-s3-urls BOOLEAN` - Inline S3 URLs: `true` or `false`
- `--include-attachments BOOLEAN` - Include attachments: `true` or `false`

**Output Fields:**
- `id` (UUID) - Example identifier
- `dataset_id` (UUID) - Parent dataset ID
- `inputs` (object) - Input data
- `outputs` (object|null) - Expected output data
- `metadata` (object|null) - Custom metadata
- `created_at` (datetime) - Creation timestamp
- `modified_at` (datetime) - Last modified timestamp
- `runs` (array) - Associated run IDs
- `source_run_id` (UUID|null) - Source run if from trace

**Examples:**
```bash
# All examples in dataset
langsmith-cli --json examples list --dataset "my-dataset" --limit 50

# Training split only
langsmith-cli --json examples list --dataset "my-dataset" --splits train

# Filter by metadata
langsmith-cli --json examples list \
  --dataset "my-dataset" \
  --metadata '{"difficulty": "hard"}'

# Paginated access
langsmith-cli --json examples list --dataset "my-dataset" --offset 100 --limit 50
```

### `examples get`

Get specific example details.

```bash
langsmith-cli --json examples get <example-id> [OPTIONS]
```

**Arguments:**
- `example-id` (required) - Example UUID

**Options:**
- `--as-of TEXT` - Version tag or ISO timestamp

**Output:** Complete example object

**Example:**
```bash
langsmith-cli --json examples get <uuid>
```

### `examples create`

Create a new example in a dataset.

```bash
langsmith-cli --json examples create [OPTIONS]
```

**Options:**
- `--dataset TEXT` (required) - Dataset name or UUID
- `--inputs JSON` (required) - Input data as JSON object
- `--outputs JSON` - Expected output data as JSON object
- `--metadata JSON` - Custom metadata as JSON object
- `--split TEXT` - Split name (e.g., "train", "test", "validation")
- `--source-run-id UUID` - Source run UUID if example from trace

**Output:** Created example object

**Examples:**
```bash
# Basic example
langsmith-cli --json examples create \
  --dataset "qa-dataset" \
  --inputs '{"question": "What is 2+2?"}' \
  --outputs '{"answer": "4"}'

# With metadata and split
langsmith-cli --json examples create \
  --dataset "qa-dataset" \
  --inputs '{"question": "Capital of France?"}' \
  --outputs '{"answer": "Paris"}' \
  --metadata '{"difficulty": "easy", "category": "geography"}' \
  --split train
```

