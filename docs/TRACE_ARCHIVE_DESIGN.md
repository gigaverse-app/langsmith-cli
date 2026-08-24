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

Status has a separate fast path because publication scale makes a full metadata
audit expensive. During the live backfill, downloading every dev manifest was still
unfinished when interrupted at 168.42 seconds. Key-derived `status --summary`
counted 23,404 dev manifests in 8.37 seconds and all 56,453 then-published manifests
across three routes in 19.41 seconds, with zero invalid keys. That is at least 20x
lower dev completion-check latency in the observed run. The summary labels itself
`manifest_contents_verified: false`; use the bounded full audit only when manifest
body validation is required.

The six-shard run also exposed why DuckDB resource ownership is an invariant, not a
tuning detail. Two large workers peaked near 10 GiB each and used swap; one
failed after another process truncated the shared default
`.tmp/duckdb_temp_storage_*.tmp`. Every archive connection now has a 1 GiB memory
limit and creates a unique spill directory within its already-unique staging root.
The limit prevents several connections from each claiming most of host memory; the
directory prevents cleanup in one process from corrupting another. Publication
completed before the failure remains sealed and the failed disjoint shard is safe to
replay.

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

Canonical schema v2 separates arbitrary payloads from shape-stable query dimensions:

| Physical type | Run fields | Reason |
| --- | --- | --- |
| JSON text | `inputs`, `outputs`, `extra`, `events`, `feedback_stats` | Values are arbitrary provider/application documents; inferred children are unbounded. |
| `list<string>` | `tags`, `parent_run_ids` | Both are documented homogeneous lists and useful filter/topology dimensions. |
| `map<string, bigint>` | prompt/completion token details | Providers may add token categories without changing the Parquet schema. |
| `map<string, decimal(38,18)>` | prompt/completion cost details | Cost categories remain open-ended while values retain decimal precision. |
| `map<string, string>` | extracted `extra.metadata` | Metadata keys stay queryable without per-key columns; the authoritative heterogeneous object remains intact in `extra`. |

Metadata map values use their stable textual representation (plain strings, decimal
or boolean text, and compact JSON for nested values). Queries can cast a selected
value when numeric comparison is required. Runs API JSONL and Bulk Export v2 are
normalized to these types before canonical union.

Manifest schema v1 remains readable. Readers normalize each published generation to
v2 independently before cross-day `UNION ALL BY NAME`, so old JSON-text tags and
parent IDs cannot dictate the type of new list columns. The next publication for an
unsealed v1 day upgrades its manifest and canonical Parquet atomically to v2. The
upgrade re-canonicalizes v1 text-dimension raw on the same row-group-bounded
streaming path as v2 raw (dimension values are parsed per byte-bounded row group),
so migrating an unsealed whale day never re-enters the day-scaled SQL union.
Id-only empty snapshots likewise never demote a day off the streaming path.

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

| ID | Invariant | Enforcement point | Regression proof |
|---|---|---|---|
| A1 | One project matches exactly one route | `ArchiveConfig.route_project` requires exactly one match | `test_route_config_selects_exactly_one_destination`, `test_overlapping_routes_fail_fast` |
| A2 | One command processes each project identity at most once | `_routed_projects` rejects duplicate project IDs before export/worker submission | `test_bulk_backfill_rejects_duplicate_project_identity_before_export` |
| A3 | Project identity is immutable inside a route | `ensure_project_record` create-or-verify boundary | `test_project_catalog_rejects_silent_rename` |
| A4 | A manifest is exactly one UTC day | `ArchiveManifest` construction/deserialization validation | `test_corrupt_manifest_fails_at_the_storage_boundary` |
| A5 | Object keys are normalized and namespace-bound | Store key validation plus manifest model | `test_store_rejects_object_key_traversal`, `test_manifest_location_must_match_its_project_and_date` |
| B1 | Every managed job has canonical IDs, a non-empty UTC window, and the exact requested destination/project/format contract | `BulkExportJob.__post_init__`, `BulkExportSnapshot.__post_init__`, `_matches_window` | `test_bulk_export_job_model_enforces_identity_and_window_invariants`, `test_bulk_export_snapshot_model_enforces_utc_window_invariant`, `test_bulk_export_batch_rejects_polled_request_identity_drift` |
| B2 | Every exported file remains inside the normalized configured S3 destination | `_get_validated_destination`, `_exported_file_uri`, `_require_normalized_s3_path` | `test_bulk_export_rejects_destination_outside_archive`, `test_bulk_export_rejects_invalid_completed_partitions`, `test_bulk_export_rejects_unsafe_identity_and_storage_configuration` |
| B3 | Completed partitions exactly cover the requested UTC window | `_validate_partition_coverage` rejects missing, gapped, overlapping, reversed, or extra intervals | `test_bulk_export_rejects_missing_partition_coverage`, `test_bulk_export_partitions_exactly_cover_requested_window` |
| B4 | Completion order never controls harvest order | `complete_exports` removes and yields every ready job before waiting or reporting a terminal peer | `test_bulk_export_batch_harvests_completed_jobs_without_head_of_line_blocking`, `test_bulk_export_batch_harvests_ready_peer_before_reporting_failure` |
| A6 | Snapshot and canonical run IDs are unique | DuckDB `count(*) = count(DISTINCT id)` checks before and after canonicalization | `test_snapshot_duplicate_run_ids_fail_before_publication`, `test_reconciliation_deduplicates_and_is_idempotent` |
| A7 | Canonical count is between the largest input and their sum | Manifest publication/read validation | `test_canonical_count_is_bounded_by_snapshot_counts` |
| A8 | Reconciliation wins duplicate IDs without losing primary-only or late rows | Canonical `row_number` ordered by snapshot rank | `test_reconciliation_deduplicates_and_is_idempotent`, `test_bulk_reconciliation_unifies_runs_api_and_v2_json_column_types` |
| A9 | A sealed day cannot regress | Phase idempotency plus sealed-state validation | `test_sealed_day_is_idempotent_and_cannot_be_unsealed`, `test_range_backfill_publishes_daily_partitions_and_resumes` |
| A10 | Only one concurrent publisher wins | S3 ETag/local locked compare-and-swap | `test_manifest_publication_rejects_a_stale_writer`, `test_two_manifest_publishers_cannot_both_win` |
| A11 | Readers use only published canonical keys | Manifest-directed discovery | `test_queries_ignore_unpublished_raw_and_canonical_objects` |
| A12 | Empty project-days return zero without schema-union failures | Zero-count manifest pruning | `test_empty_archive_is_queryable_as_zero_rows` |
| A13 | Text-search SQL identifiers come only from a fixed allowlist | `ArchiveRunQuery.__post_init__` | `test_archive_text_fields_are_an_explicit_allowlist` |
| A14 | Full-trace semantic values and topology are provider-independent | `_normalize_run_payload`, `_validated_archive_run`, and derived `child_run_ids` | `test_bulk_json_normalization_matches_live_run_shape`, `test_bulk_reconciliation_unifies_runs_api_and_v2_json_column_types` plus the documented real three-trace comparison |
| A15 | Archive completion counts do not require one object GET per manifest | `archive status --summary` derives identities from normalized immutable keys and labels the result `manifest_contents_verified: false`; full audits share one bounded metadata reader | `test_status_summary_is_key_derived_and_never_downloads_manifests`, `test_status_reads_independent_manifests_concurrently` |
| A16 | Concurrent archive connections have bounded memory and never share spill files | every connection has a 1 GiB memory limit and a unique temporary directory inside its project/query staging boundary | `test_duckdb_resources_are_bounded_and_unique_to_each_project_staging_area`; live replay of the shard that exposed shared `.tmp` truncation and severe host-memory pressure |

The table deliberately names the enforcing code and executable regression test for
each contract. Tests without an enforcement point prove an accident; comments without
a falsifying test are only claims. CLI tests additionally cover scheduled D+2/D+12
routing, explicit-project failures, progress behavior, and status output.

Bulk Export and the Runs API encode a few equivalent values differently. Bulk may
pad inferred nested objects with null keys, add one JSON layer to LangChain's reserved
`inputs.input`, omit the derived `child_run_ids`, and return unzoned UTC event times.
It also emits tags/topology lists as JSON text and omits usage-detail maps. Canonical
schema v2 normalizes those physical differences before publication; the archive
reader safely normalizes the remaining SDK differences. It deliberately preserves
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
