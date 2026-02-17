# Troubleshooting & Configuration

## Environment Variables

Configure langsmith-cli behavior:

```bash
# Required
export LANGSMITH_API_KEY="lsv2_pt_..."

# Optional
export LANGSMITH_ENDPOINT="https://api.smith.langchain.com"  # Default
export LANGSMITH_ENDPOINT="https://eu.api.smith.langchain.com"  # EU region
export LANGSMITH_ENDPOINT="https://your-instance.com"  # Self-hosted

# Alternative: Use .env file in project root
echo 'LANGSMITH_API_KEY="lsv2_pt_..."' > .env
```

## Output Formats

### JSON Mode (Agent Use)

Always use `--json` as the first argument for structured output:

```bash
langsmith-cli --json <command> [options]
```

**Output Structure:**
```json
{
  "items": [...],
  "total": 10,
  "limit": 10,
  "offset": 0
}
```

For single-item commands (`get`):
```json
{
  "id": "...",
  "name": "...",
  ...
}
```

### Rich Table Mode (Human Use)

Omit `--json` for human-friendly colored tables:

```bash
langsmith-cli <command> [options]
```

## Performance Tips

1. **Always use `--json`** for agent/automation use
2. **Use `--fields` for `runs get`** to reduce data by ~90%
3. **Keep `--limit` small** (5-10) for initial exploration
4. **Use filters** (`--status`, `--project`, `--filter`) before fetching
5. **Progressive loading**: List → Get → Inspect
6. **Prefer `runs open`** for complex trace inspection

## Error Codes

| Exit Code | Meaning |
|-----------|---------|
| 0 | Success |
| 1 | General error |
| 2 | API authentication error |
| 3 | Resource not found |
| 4 | Invalid arguments |

## Common Error Messages

### "Authentication failed"
**Cause:** Missing or invalid API key
**Solution:** Run `langsmith-cli auth login` or set `LANGSMITH_API_KEY` env var

### "Project not found"
**Cause:** Project name doesn't exist or has a path prefix (e.g., `prd/my-project`)
**Solution:**
1. The error message will suggest similar project names if available
2. List projects: `langsmith-cli --json projects list --fields name`
3. Use substring matching: `langsmith-cli --json runs list --project-name my-project`
4. Use wildcards: `langsmith-cli --json runs list --project-name-pattern "*my-project*"`
5. If you have a UUID, pass it to `--project-id` or `--project` (UUIDs are auto-detected)
6. In JSON mode, the error includes structured `suggestions` and `failed_sources` fields

### "Dataset not found"
**Cause:** Dataset name doesn't match exactly
**Solution:** Run `langsmith-cli datasets list --name-contains "..."` to find correct name

### "Invalid filter expression"
**Cause:** Malformed FQL query
**Solution:** Check FQL syntax, ensure quotes around values

### "Rate limit exceeded"
**Cause:** Too many API requests
**Solution:** Add delays between commands or reduce `--limit`

## Version Information

Run `langsmith-cli --version` to see installed version.

For updates: `uv pip install --upgrade langsmith-cli`
