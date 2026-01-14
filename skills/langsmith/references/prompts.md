## Prompts

### `prompts list`

List prompt templates.

```bash
langsmith-cli --json prompts list [OPTIONS]
```

**Options:**
- `--limit INTEGER` - Maximum results (default: 10)
- `--is-public BOOLEAN` - Filter by visibility: `true` or `false`

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

