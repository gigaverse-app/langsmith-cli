# LangSmith Trace Archive Design

## Purpose

`langsmith-cli` provides an organization-operated mechanism for retaining LangSmith
traces in private object storage and querying them after LangSmith retention expires.
The organization owns the scheduler, LangSmith credentials, AWS identity, buckets,
encryption, lifecycle rules, and retention policy. The CLI owns only the protocol:
export, reconcile, verify, canonicalize, publish manifests, and query Parquet.

## Lifecycle

For a UTC trace date `D` and 14-day LangSmith retention:

```text
D+2   primary snapshot of [D, D+1)
D+12  reconciliation snapshot of the same window
D+14  LangSmith retention expires the source traces
```

The second snapshot deliberately overlaps the first. Late children and updated runs
make a delta-only export unsafe without a reliable modification watermark.

When an organization first enables the archive, the D+12 day may have no historical
D+2 snapshot. In that bootstrap case, the reconciliation export is still a complete
snapshot of `[D, D+1)` and is published alone as a sealed canonical generation. This
captures the oldest still-retained data immediately; steady-state days contain both
snapshots.

An organization-owned daily job invokes:

```bash
LANGSMITH_ARCHIVE_URI=s3://gigaverse-langsmith-traces-prd/langsmith \
  langsmith-cli --json archive sync --project prd/my-agent --retention-days 14
```

Each invocation calculates both due windows. Completed phases are skipped, known
in-flight jobs are resumed, and failures are safe to retry.

For high-volume projects, the same command accepts an organization-created managed
destination through `--bulk-export-destination-id` or
`LANGSMITH_BULK_EXPORT_DESTINATION_ID`. LangSmith writes `v2_beta` Parquet to an S3
prefix inside the archive; the CLI adopts matching jobs, validates all partition
coverage and row identities, and publishes the normal canonical contract.

Historical migration uses one range job per project:

```bash
langsmith-cli --json archive backfill --route production \
  --start-date 2025-08-01 --end-date 2026-08-01 \
  --import-workers 8 \
  --bulk-export-destination-id <uuid>
```

All project jobs are submitted before the CLI waits, letting LangSmith control
workspace concurrency. Completed jobs are harvested without submission-order
blocking, and bounded workers convert independent projects into sealed daily
manifests. Re-running adopts the same range jobs and skips days already sealed.

### Historical backfill execution model

Export and publication are deliberately separate concurrency domains:

```text
selected projects
      |
      | submit or adopt every exact project/range request
      v
LangSmith managed queue (remote concurrency and hourly Parquet)
      |
      | harvest whichever exports complete; submission order is irrelevant
      v
bounded project worker pool (local DuckDB + S3 concurrency)
      |
      | one worker owns one project and publishes its UTC days serially
      v
raw generation -> verified canonical generation -> manifest written last
```

The CLI never splits one project's days across workers in one invocation. This is
the local one-writer-per-project invariant; export IDs are also required to map to
exactly one project before any worker is scheduled. Independent projects may publish
concurrently. For a one-time migration, operators may run multiple invocations with
disjoint repeated `--project` selections. Overlapping project selections are safe at
the manifest CAS boundary but waste export, DuckDB, and S3 work and therefore are not
a scaling strategy.

`--import-workers` bounds projects being compacted per invocation; it does not alter
LangSmith's managed export concurrency. Its default is 8 and its maximum is 32.
Total local concurrency across manual shards is `invocations * import-workers`, so
operators must measure CPU, memory, S3 request rate, and object sizes rather than
assuming the maximum is faster. The live 399-day Gigaverse migration measured:

| Layout | Aggregate publication | Relative to serial |
|---|---:|---:|
| Three serial environment processes | ~45 project-days/min | 1x |
| One 8-worker process per environment | ~350 project-days/min | ~8x |
| Six disjoint shards, 48 aggregate workers, 6-core host | 560-630 project-days/min | ~12-14x |

These measurements describe one workload and host, not a universal default. Daily
scheduled syncs are much smaller and do not need manual sharding.

## Project routing

Archive destinations are selected by ordered, named project routes:

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

One config may describe every environment. The safer deployment uses one CronJob per
route (`archive sync --route dev`) and gives that workload access only to its bucket.
An explicitly authorized central job may use `--all-routes`, resolving the project
catalog once and dispatching each project to its route.

A project must match exactly one route. Multiple matches are a configuration error;
unmatched projects are reported and never fall back to another bucket. Route names
are configuration identifiers represented internally by typed models; archive phase
and state decisions use Enums.

## Storage model

```text
<archive-uri>/
  projects/project_id=<uuid>.json
  raw/project_id=<uuid>/date=YYYY-MM-DD/phase=primary/generation=<uuid>/runs.parquet
  raw/project_id=<uuid>/date=YYYY-MM-DD/phase=reconciliation/generation=<uuid>/runs.parquet
  canonical/project_id=<uuid>/date=YYYY-MM-DD/generation=<uuid>/runs.parquet
  manifests/project_id=<uuid>/date=YYYY-MM-DD.json
```

Raw generations are immutable. A canonical generation is also immutable; the
manifest is the publication pointer and is written last. Readers never glob all
canonical generations. They resolve manifests and read only their referenced keys.

Project catalog entries are immutable `(project_id, project_name)` identities within
one route. They let readers resolve a project once and list only
`manifests/project_id=<uuid>/...`; exact-project queries do not GET every manifest in
the bucket. A project rename or move across routes is an explicit migration rather
than a silent archive split.

Catalog backfill is incremental and backward-compatible. Readers combine cataloged
project prefixes with any manifest project IDs not yet present in the catalog, so a
deployment cannot hide older data while an idempotent sync populates catalog entries.

Arbitrary SDK `bytes` values are preserved as tagged base64 objects with
`__langsmith_archive_encoding__ = "base64"`. Lazy SDK serialization iterators are
materialized at the JSON-to-Parquet boundary. This keeps non-UTF-8 media payloads
without weakening UTF-8 validation for the surrounding trace document.

Canonical nested fields (`inputs`, `outputs`, `extra`, `events`, `tags`,
`feedback_stats`, and `parent_run_ids`) are JSON text. Runs API JSONL inference
produces DuckDB `STRUCT`/`LIST` values while Bulk Export v2 supplies JSON `VARCHAR`;
canonicalization converts both forms before union. This prevents provider changes or
different object keys on adjacent days from producing incompatible Parquet schemas.

The bucket owner may expire `raw/` after a repair/audit window. Canonical objects and
manifests are retained according to the organization's policy.

## Deduplication

Canonicalization unions the primary and reconciliation snapshots using DuckDB and
selects one row per stable `run.id`. Reconciliation has higher precedence. A run
missing from reconciliation is retained from primary; a new late run is added; an
updated run is replaced by its reconciliation representation.

```sql
SELECT * EXCLUDE (snapshot_rank, archive_row_number)
FROM (
  SELECT *, row_number() OVER (
    PARTITION BY id ORDER BY snapshot_rank DESC
  ) AS archive_row_number
  FROM (
    SELECT *, 1 AS snapshot_rank FROM read_parquet(primary)
    UNION ALL BY NAME
    SELECT *, 2 AS snapshot_rank FROM read_parquet(reconciliation)
  ) snapshots
)
WHERE archive_row_number = 1
```

Each snapshot must independently satisfy `count(*) = count(DISTINCT id)`. A
duplicate within one provider snapshot is a verification failure, not something to
hide during compaction.

## Idempotency and crash recovery

The logical operation key is `(archive, project_id, UTC date, phase, schema_version)`.
A phase is published only after its raw object and new canonical object are verified.
The manifest records generation identifiers, object keys, counts, and timestamps. A
verified phase is never exported again unless a future explicit repair workflow is
used.

Cross-service exactly-once behavior is impossible if a process dies after creating a
remote export but before persisting its ID. The design therefore guarantees
at-least-once raw attempts and exactly-once canonical rows. Orphan raw attempts can be
adopted or removed by lifecycle policy.

Managed jobs are adopted by their complete immutable request identity: destination,
project, exact half-open window, `v2_beta`, zstandard compression, full field set,
and no schedule/filter. The newest non-failed match is used. Reconciliation excludes
the primary export ID, forcing a new snapshot of the same date. Concurrent creators
may leave a redundant managed job, but conditional manifest publication still
allows only one canonical winner.

The mutable manifest pointer is updated with compare-and-swap: `If-None-Match: *` for
its first S3 publication and `If-Match: <observed-etag>` thereafter. Local archives
use the same expected-content-version rule under a cross-process file lock. A stale
worker fails with a typed concurrency error and may leave only immutable orphan
objects; it cannot overwrite the winning manifest.

```text
worker A: read v1 ─ export raw A ─ canonical A ─ CAS(v1) ──► v2 published
worker B: read v1 ─ export raw B ─ canonical B ─ CAS(v1) ──► CONFLICT
                                                        (v2 remains visible)
```

## Enforced invariants

| Invariant | Enforcement point | Failure behavior |
|---|---|---|
| One project matches exactly one route | Config routing | Unmatched/ambiguous explicit projects fail; catalog scans report unmatched projects |
| Project identity is immutable inside a route | `projects/project_id=<uuid>.json` create/verify | Rename or route move requires explicit migration |
| A manifest is exactly one UTC day | Manifest construction and deserialization | Non-UTC or non-24-hour windows fail before query/publication |
| Object keys are normalized and namespace-bound | Store key validation plus manifest model | Absolute, traversal, wrong project/date/phase, and malformed generation keys fail |
| Snapshot run IDs are unique | DuckDB validation before canonicalization | No manifest is published; uploaded raw attempt remains an expirable orphan |
| Canonical run IDs are unique | Validation of the written canonical Parquet | No manifest is published |
| Canonical count is between the largest input and their sum | Manifest publication/read boundary | Truncated or inflated manifests fail closed |
| Reconciliation wins duplicate IDs | Canonical `row_number` rank | Updated D+12 rows replace D+2; primary-only and late rows are retained |
| A sealed day cannot regress | Phase idempotency plus sealed-state validation | Repeated phase calls return the published manifest without exporting |
| Only one concurrent publisher wins | S3 ETag/local locked CAS | Stale writer receives a concurrency error; winning pointer is preserved |
| Readers use only published canonical keys | Manifest-directed discovery | Raw/orphan/unreferenced canonical generations are invisible |
| Empty project-days return zero | Zero-count manifest pruning | DuckDB is not asked to union an empty partial schema |
| Text-search SQL identifiers are fixed | `ArchiveRunQuery` field allowlist | Unsupported `--grep-in` fields fail before SQL construction |
| Full-trace semantic values are provider-independent | Archive read normalization restores Bulk nested values, UTC event offsets, and derived child IDs | Real API/archive traces match after treating missing and null object members as equivalent |

These invariants are executable contracts, not documentation only. Unit tests cover
corrupt manifests, duplicate IDs, stale and simultaneous writers, sealed retries,
empty partitions, unsafe text fields, route pruning, and Windows paths. CLI tests
cover scheduled D+2/D+12 routing, explicit-project failures, and status output.

Bulk Export and the Runs API encode a few equivalent values differently. Bulk may
pad inferred nested objects with null keys, add one JSON layer to LangChain's reserved
`inputs.input`, omit the derived `child_run_ids`, and return unzoned UTC event times.
The archive reader safely normalizes the latter three. It deliberately preserves
nested nulls because the live CLI promises not to coerce or discard them. Parquet
schema union cannot always distinguish an absent object member from an explicit null,
so raw JSON is not universally byte-identical; semantic comparison must treat missing
and null object members as equivalent.

## Query architecture

Read-only run commands create a typed query and select one backend:

```text
command options -> RunQuery -> LangSmith backend | DuckDB archive backend
                              -> shared result/view layer
```

`--archive` is a boolean source selector. A single `LANGSMITH_ARCHIVE_URI` or a route
configuration supplies the archive location. For a configured project path, the
reader resolves exactly one destination; cross-route project patterns query each
matching archive and merge results. Archive readers need AWS read permissions but no
LangSmith key.
Unsupported live-only filters fail explicitly rather than silently changing meaning.

The initial parity surface is `runs list`, `runs search`, `runs get`, and
`runs get-latest`; run-derived discovery and analysis commands follow the same
backend contract. `runs watch`, `runs open`, and mutations remain live-only.

## Efficiency

- Work is bounded to a project-day partition and performed twice during retention.
- DuckDB reads and writes compressed Parquet and applies project/date pruning.
- Project catalog entries reduce exact-project discovery from every manifest GET to
  one small catalog scan plus that project's manifest prefix.
- Canonicalization can operate directly on S3 through DuckDB's credential-chain
  secret; no full historical archive is downloaded.
- Canonical output is counted and uniqueness-checked from the newly written local
  Parquet file; the remote reconciliation union/window query runs only once.
- Large partitions may be sharded by `hash(run.id) % N` without changing manifests.
- Queries project requested columns and use Parquet predicate pushdown.
- Raw duplicates are temporary and can be expired after reconciliation is sealed.

## Security

- Buckets use Block Public Access, Bucket Owner Enforced ownership, encryption,
  versioning, and a deny-non-TLS bucket policy.
- An environment-specific archiver role receives prefix-scoped list/read/write and
  multipart permissions. Reader roles receive only prefix-scoped list/read.
- Dev, staging, and production use separate buckets and roles without cross-grants.
- Secrets come from the runtime environment/default AWS credential chain and are
  never written to manifests, Parquet, configuration, or logs.

## Provider boundary

The portable provider pages through `Client.list_runs` for an exact half-open UTC
window and writes Parquet itself. The LangSmith Bulk Export provider uses an
organization-created destination ID, submits/adopts managed jobs, verifies every
partition covers the requested window without gaps, verifies Parquet count and run
ID uniqueness, and compacts the provider output into the same raw/canonical layout.
Scheduling, manifests, verification, canonicalization, and querying remain
provider-independent.
