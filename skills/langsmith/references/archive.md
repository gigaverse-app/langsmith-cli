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

For high-volume projects, configure a LangSmith Bulk Export destination whose S3
bucket and prefix are inside the selected archive URI. Supply its UUID through the
environment or the command line:

```bash
LANGSMITH_BULK_EXPORT_DESTINATION_ID=<uuid> \
  langsmith-cli --json archive sync --route dev --retention-days 14
```

The CLI creates or adopts an exact project/window `v2_beta` export, waits for all
LangSmith partitions, verifies row counts and distinct run IDs from the published
Parquet, then publishes the same canonical manifest contract as the Runs API
provider. Primary export IDs are excluded when selecting reconciliation jobs, so
D+12 always captures a fresh snapshot.

Prefer one CronJob per route so its AWS role can access only that environment's
bucket. Use `--all-routes` only for a central role intentionally authorized for all
destinations.

On first deployment, a due D+12 day may not have a D+2 object. The CLI publishes
that complete reconciliation snapshot alone and seals the day, preserving the oldest
data still available. Later days follow the normal two-snapshot flow.

Overlapping jobs are safe: immutable data uploads happen before an ETag-conditional
manifest update. Exactly one writer publishes; stale writers fail and are safe to
retry. Do not add an external distributed lock around the CronJob.

Check archive size without downloading every manifest:

```bash
langsmith-cli --json archive status --summary --route dev
langsmith-cli --json archive status --summary --all-routes
```

Omit `--summary` only when you need every validated manifest body. A full status
audit performs one object read per manifest; the summary derives project/date counts
from immutable manifest keys using S3 LIST and reports
`manifest_contents_verified: false` explicitly.

## Historical backfill

Use one managed range export per project rather than creating one API job per day.
`--start-date` is inclusive and `--end-date` is exclusive:

```bash
langsmith-cli --json archive backfill \
  --route dev \
  --start-date 2025-08-01 \
  --end-date 2026-08-01 \
  --bulk-export-timeout-hours 73 \
  --import-workers 8 \
  --bulk-export-destination-id <uuid>
```

The command submits/adopts every selected project export before waiting, allowing
LangSmith to apply its workspace concurrency. It harvests projects in completion
order and compacts independent projects concurrently. Completed output is split into
sealed UTC-day manifests for DuckDB. Re-running the same command adopts the existing
range export and skips already sealed days.

Progress messages go to stderr even with `--json`, leaving stdout as one final
machine-readable result. The important transitions are:

```text
Submitted or adopted N project export(s)  remote work is known and resumable
Export ready; importing <project>         local DuckDB/S3 publication started
Imported <project>: X new, Y sealed       that project's range finished locally
final JSON on stdout + exit 0              every selected project finished
```

The first line does **not** mean the backfill is complete. LangSmith can have accepted
every job while local canonical publication still has thousands of project-days to
write.

Historical exports can remain queued behind other workspace jobs. Backfill waits up
to 73 hours without any project completing by default, slightly beyond the managed
workflow's 72-hour terminal timeout. Each completion resets that idle deadline.
Override `--bulk-export-timeout-hours` for a shorter operator window and
`--import-workers` to bound local DuckDB/S3 concurrency; rerunning adopts the same
exact-window jobs.

Use repeated `--project` options to limit a repair or trial. Keep ranges small enough
to finish within LangSmith's managed export workflow timeout. Bulk Export cannot
recover traces that LangSmith has already deleted under its retention policy.

### Scaling a one-time backfill

`--import-workers` is the number of independent projects compacted by one invocation,
not the number of days or LangSmith export jobs. Start with the default 8 and measure
the host and S3 before increasing it. The maximum is 32.

If one process cannot use the available host capacity, split projects into disjoint
invocations using repeated `--project` options:

```bash
# Shard A
langsmith-cli --json archive backfill --route production \
  --project prd/agent-a --project prd/agent-c \
  --start-date 2025-08-01 --end-date 2026-08-01 \
  --import-workers 8 --bulk-export-destination-id <uuid>

# Shard B: no project may also appear in shard A.
langsmith-cli --json archive backfill --route production \
  --project prd/agent-b --project prd/agent-d \
  --start-date 2025-08-01 --end-date 2026-08-01 \
  --import-workers 8 --bulk-export-destination-id <uuid>
```

Keep the route, destination, and half-open date window identical across shards. The
CLI rejects one export ID being assigned to multiple projects, and each invocation
schedules at most one publisher per selected project. Manifest compare-and-swap is a
last line of defense, not permission to overlap shard membership deliberately.

Total local concurrency is `number of invocations * import workers`. Watch CPU,
memory, S3 throttling, and error logs while increasing it. More workers cannot make a
queued LangSmith export complete sooner; they only accelerate publication after an
export is ready. On an 11 GiB host, 48 aggregate workers briefly drove two large
DuckDB processes near 10 GiB each and into swap; scale down aggregate workers for the
long tail instead of assuming the broad-middle throughput will hold for the largest
projects. Each archive DuckDB connection is limited to 1 GiB and owns a unique spill
directory. That bounds connection memory and prevents concurrent processes from
truncating each other's spill files; aggregate process overhead still makes worker
count an operator-controlled capacity decision.

### Proving completion

Use the process result and the archive matrix together. For a half-open range, the
expected day count is `end_date - start_date`. A complete rectangular backfill has
`selected_projects * expected_days` sealed manifests:

```bash
langsmith-cli --json archive status --summary \
  --config archive.yaml --route production \
  | jq '.routes[0] | {
      manifests: .manifest_count,
      projects: .project_count,
      first_date,
      last_date,
      invalid_manifest_keys
    }'
```

Completion requires all of the following:

1. Every shard exits 0 and emits its final JSON result.
2. Every shard result reports `imported_days + skipped_days == expected_days` for
   each selected project, and summary `manifests == selected_projects *
   expected_days` with no invalid keys. Use the original selected-project count;
   deriving it only from manifests could hide a project with zero published days.
3. No shard reports a terminal export, validation, DuckDB, S3, or CAS error.
4. Representative DuckDB archive queries return the expected runs before temporary
   Bulk Export credentials or raw source objects are retired.

### Restart and cleanup

Restart with the exact same destination, route, projects, and half-open window. The
CLI adopts the newest non-failed job whose complete immutable request identity
matches and skips each already sealed day. Changing the destination, project, range,
format, or field contract is a different request and may create a new managed job.

If a process stops after uploading immutable data but before publishing the manifest,
the object is an invisible orphan; readers only follow manifest pointers. A later
retry safely republishes, and the bucket's explicit raw-object lifecycle may remove
orphans after the audit window.

Keep the LangSmith destination credentials valid until both remote export and local
publication are complete. Remove temporary credentials only after the completion
checks above. Do not silently expire or delete Bulk Export source objects until the
organization has chosen and documented its repair/audit retention window.

## Archive queries

```bash
langsmith-cli --json runs list --source archive --project dev/my-agent --last 90d
langsmith-cli --json runs search "timeout" --source archive --project dev/my-agent
langsmith-cli --json runs get <id> --source archive --project dev/my-agent --last 1d --follow-children
langsmith-cli --json runs get-latest --source archive --project dev/my-agent --failed
```

Archive readers do not need `LANGSMITH_API_KEY`. They need read/list access to the
configured archive. `--archive` is a compatibility alias for `--source archive`.
Archive queries support project selectors, time windows,
status, trace ID, root selection, run type, tags, model text, full-content text/regex
search, sorting, field projection, counts, and output formats. Raw FQL, trace/tree
FQL, metadata predicates, and latency flags fail explicitly until translated to the
typed DuckDB query model.

Name/regex/exclude filters currently run after the DuckDB query. Use `--fetch N` to
control how many archived rows they evaluate; `--count` evaluates the full matching
archive before applying those local filters.

For `runs get`, project and time options are partition-pruning hints. A bare run ID
has no project/date information, so a year-scale archive otherwise requires manifest
discovery across every project and day. Pass the narrowest known `--project` or
`--project-id` plus `--since`/`--before` or `--last` window.

`runs get --follow-children --json` restores Bulk's nested `inputs.input`, UTC event
timestamps, and API-derived `child_run_ids`. Bulk Parquet may pad an inferred object
schema with null members, while the Runs API may omit those members. Because the CLI
preserves explicit nested nulls, raw JSON is not universally byte-identical; compare
missing and null object members as equivalent when validating provider parity.

Canonical schema v2 stores `tags` and `parent_run_ids` as native string lists;
prompt/completion token and cost breakdowns as typed maps; and a queryable
`map<string,string>` projection of `extra.metadata`. Full `extra`, inputs, outputs,
events, and feedback remain JSON text because their shapes are arbitrary. Existing
v1 project-days remain queryable: readers normalize each generation before union,
and the next write to an unsealed v1 day upgrades it to v2.

See `docs/TRACE_ARCHIVE_DESIGN.md` for storage layout, reconciliation,
deduplication, crash recovery, IAM boundaries, and efficiency decisions.

Project names are immutable archive identities. If a LangSmith project is renamed or
moved from one route to another, migrate its catalog/manifests explicitly; the sync
fails rather than silently splitting history across destinations.
