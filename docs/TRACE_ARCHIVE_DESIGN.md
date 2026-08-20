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

An organization-owned daily job invokes:

```bash
LANGSMITH_ARCHIVE_URI=s3://gigaverse-langsmith-traces-prd/langsmith \
  langsmith-cli --json archive sync --project prd/my-agent --retention-days 14
```

Each invocation calculates both due windows. Completed phases are skipped, known
in-flight jobs are resumed, and failures are safe to retry.

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
  raw/project_id=<uuid>/date=YYYY-MM-DD/phase=primary/generation=<uuid>/runs.parquet
  raw/project_id=<uuid>/date=YYYY-MM-DD/phase=reconciliation/generation=<uuid>/runs.parquet
  canonical/project_id=<uuid>/date=YYYY-MM-DD/generation=<uuid>/runs.parquet
  manifests/project_id=<uuid>/date=YYYY-MM-DD.json
```

Raw generations are immutable. A canonical generation is also immutable; the
manifest is the publication pointer and is written last. Readers never glob all
canonical generations. They resolve manifests and read only their referenced keys.

Arbitrary SDK `bytes` values are preserved as tagged base64 objects with
`__langsmith_archive_encoding__ = "base64"`. Lazy SDK serialization iterators are
materialized at the JSON-to-Parquet boundary. This keeps non-UTF-8 media payloads
without weakening UTF-8 validation for the surrounding trace document.

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
A phase transitions through `pending`, `exporting`, `exported`, and `verified`.
The manifest records provider job/generation identifiers, object keys, counts, and
errors. A verified phase is never exported again unless an operator explicitly asks
for repair.

Cross-service exactly-once behavior is impossible if a process dies after creating a
remote export but before persisting its ID. The design therefore guarantees
at-least-once raw attempts and exactly-once canonical rows. Orphan raw attempts can be
adopted or removed by lifecycle policy.

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
- Canonicalization can operate directly on S3 through DuckDB's credential-chain
  secret; no full historical archive is downloaded.
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
window and writes Parquet itself. An optional LangSmith Bulk Export provider may use
an organization-created destination ID. Scheduling, manifests, verification,
canonicalization, and querying are provider-independent.
