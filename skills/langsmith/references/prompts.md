## Prompts

### `prompts list`

List prompt templates.

```bash
langsmith-cli --json prompts list [OPTIONS]
```

**Options:**
- `--limit INTEGER` - Maximum results (default: 20)
- `--is-public BOOLEAN` - Filter by visibility: `true` or `false`
- `--exclude TEXT` - Exclude items containing substring (repeatable)
- `--fields TEXT` - Comma-separated field names to include
- `--count` - Output only the count of results
- `--output TEXT` - Write output to file (JSONL format)

**Output Fields:**
- `repo_handle` (string) - Prompt identifier
- `description` (string|null) - Prompt description
- `num_commits` (integer) - Number of versions
- `num_likes` (integer) - Like count
- `num_downloads` (integer) - Download count
- `num_views` (integer) - View count
- `liked_by_auth_user` (boolean) - Whether liked by current user
- `last_committed_at` (datetime) - Last update timestamp
- `is_public` (boolean) - Public visibility
- `is_archived` (boolean) - Archive status
- `tags` (array) - Prompt tags
- `original_repo_id` (UUID|null) - Source repo if forked

**Example:**
```bash
# List your prompts
langsmith-cli --json prompts list --is-public false --limit 20

# Browse public prompts
langsmith-cli --json prompts list --is-public true
```

### `prompts get`

Get a specific prompt template.

```bash
langsmith-cli --json prompts get <name> [OPTIONS]
```

**Arguments:**
- `name` (required) - Prompt name/handle

**Options:**
- `--commit TEXT` - Specific commit hash (default: latest)
- `--fields TEXT` - Comma-separated field names to include
- `--output TEXT` - Write output to file (JSON format)

**Output Fields:**
- `repo_handle` (string) - Prompt identifier
- `manifest` (object) - Prompt manifest with template
- `commit_hash` (string) - Commit hash
- `parent_commit_hash` (string|null) - Parent commit
- `examples` (array) - Example inputs/outputs

**Manifest Structure:**
```json
{
  "_type": "prompt",
  "input_variables": ["user_input", "context"],
  "messages": [
    {"role": "system", "content": "You are a helpful assistant."},
    {"role": "user", "content": "{user_input}"}
  ]
}
```

**Example:**
```bash
langsmith-cli --json prompts get "my-qa-prompt"
langsmith-cli --json prompts get "my-qa-prompt" --commit abc123def
```

### `prompts push`

Push a local prompt file to LangSmith.

```bash
langsmith-cli --json prompts push <name> <file-path> [OPTIONS]
```

**Arguments:**
- `name` (required) - Prompt name/handle
- `file-path` (required) - Path to prompt file (JSON or YAML)

**Options:**
- `--description TEXT` - Prompt description
- `--tags TEXT` - Comma-separated tags
- `--is-public BOOLEAN` - Make public: `true` or `false`

**File Format (JSON):**
```json
{
  "_type": "prompt",
  "input_variables": ["query"],
  "messages": [
    {"role": "system", "content": "You are a helpful assistant."},
    {"role": "user", "content": "{query}"}
  ]
}
```

**File Format (YAML):**
```yaml
_type: prompt
input_variables:
  - query
messages:
  - role: system
    content: You are a helpful assistant.
  - role: user
    content: "{query}"
```

**Output:** Push result with commit hash

**Examples:**
```bash
# Push new version
langsmith-cli --json prompts push "my-prompt" prompt.json

# Push with metadata
langsmith-cli --json prompts push "my-prompt" prompt.json \
  --description "Updated system message" \
  --tags "v2,production"
```

### `prompts pull`

Pull a prompt's full content (manifest, examples, model config).

```bash
langsmith-cli --json prompts pull <name> [OPTIONS]
```

**Arguments:**
- `name` (required) - Prompt name/handle

**Options:**
- `--commit TEXT` - Specific commit hash to pull (default: latest)
- `--include-model` - Include model configuration in output
- `--fields TEXT` - Comma-separated fields to return
- `--output FILE` - Write output to file

**Output Fields:**
- `repo` (string) - Repository handle
- `owner` (string) - Owner handle
- `commit_hash` (string) - Commit hash
- `manifest` (object) - Prompt manifest with template
- `examples` (array) - Example inputs/outputs

**Examples:**
```bash
# Pull latest version
langsmith-cli --json prompts pull "my-qa-prompt"

# Pull specific commit with model config
langsmith-cli --json prompts pull "my-qa-prompt" --commit abc123 --include-model

# Pull with field pruning
langsmith-cli --json prompts pull "my-qa-prompt" --fields manifest,examples
```

### `prompts create`

Create a new prompt.

```bash
langsmith-cli --json prompts create <name> [OPTIONS]
```

**Arguments:**
- `name` (required) - Prompt name

**Options:**
- `--description TEXT` - Prompt description
- `--is-public BOOLEAN` - Make prompt public: `true` or `false` (default: private)
- `--tags TEXT` - Comma-separated tags

**Output:** Created prompt object

**Examples:**
```bash
langsmith-cli --json prompts create "my-new-prompt"
langsmith-cli --json prompts create "my-prompt" --description "QA prompt" --tags "v1,qa"
```

### `prompts delete`

Delete a prompt.

```bash
langsmith-cli --json prompts delete <name> [OPTIONS]
```

**Arguments:**
- `name` (required) - Prompt name to delete

**Options:**
- `--confirm` - Skip confirmation prompt

**Output:** Success/error status

**Example:**
```bash
langsmith-cli --json prompts delete "old-prompt" --confirm
```

### `prompts commits`

List commit history (versions) of a prompt.

```bash
langsmith-cli --json prompts commits <name> [OPTIONS]
```

**Arguments:**
- `name` (required) - Prompt name

**Options:**
- `--limit INTEGER` - Maximum commits to return (default: 20)
- `--offset INTEGER` - Number of commits to skip
- `--include-model` - Include model configuration
- `--fields TEXT` - Comma-separated fields to return
- `--count` - Return only the count of commits
- `--output TEXT` - Write output to file

**Output Fields:**
- `commit_hash` (string) - Commit hash
- `parent_commit_hash` (string|null) - Parent commit
- `created_at` (datetime) - Commit timestamp

**Examples:**
```bash
# List recent versions
langsmith-cli --json prompts commits "my-prompt" --limit 5

# Count total versions
langsmith-cli --json prompts commits "my-prompt" --count
```

