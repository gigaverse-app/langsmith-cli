# S3 Trace Archive

The archive is organization-operated. `langsmith-cli` does not own a LangSmith key,
AWS credentials, scheduler, or bucket. Supply credentials through the runtime
environment and AWS default credential chain.

## Route configuration

```yaml
routes:
  - name: dev
    project_pattern: "dev/**"
    archive_uri: s3://gigaverse-langsmith-traces-dev/langsmith
  - name: staging
    project_pattern: "stg/**"
    archive_uri: s3://gigaverse-langsmith-traces-stg/langsmith
  - name: production
    project_pattern: "prd/**"
    archive_uri: s3://gigaverse-langsmith-traces-prd/langsmith
```

Set `LANGSMITH_ARCHIVE_CONFIG=/path/archive.yaml`, or pass `--config`. Projects must
match exactly one route. Overlapping and unmatched routes fail fast.

For a single archive, set `LANGSMITH_ARCHIVE_URI=s3://bucket/prefix` without a config.

## Scheduled sync

With 14-day LangSmith retention, each daily invocation exports the UTC day at D+2
and reconciles it at D+12:

```bash
langsmith-cli --json archive sync --route dev --retention-days 14
langsmith-cli --json archive sync --all-routes --retention-days 14

# One exact repair/experiment window
langsmith-cli --json archive sync --route dev \
  --date 2026-08-19 --phase primary
```

Prefer one CronJob per route so its AWS role can access only that environment's
bucket. Use `--all-routes` only for a central role intentionally authorized for all
destinations.

List manifests:

```bash
langsmith-cli --json archive status --route dev
langsmith-cli --json archive status --all-routes
```

## Archive queries

```bash
langsmith-cli --json runs list --archive --project dev/my-agent --last 90d
langsmith-cli --json runs search "timeout" --archive --project dev/my-agent
langsmith-cli --json runs get <id> --archive --follow-children
langsmith-cli --json runs get-latest --archive --project dev/my-agent --failed
```

Archive readers do not need `LANGSMITH_API_KEY`. They need read/list access to the
configured archive. `--archive` currently supports project selectors, time windows,
status, trace ID, root selection, run type, tags, model text, full-content text/regex
search, sorting, field projection, counts, and output formats. Raw FQL, trace/tree
FQL, metadata predicates, and latency flags fail explicitly until translated to the
typed DuckDB query model.

See `docs/TRACE_ARCHIVE_DESIGN.md` for storage layout, reconciliation,
deduplication, crash recovery, IAM boundaries, and efficiency decisions.
