# ENG-6997 dataset replicas — test plan

## User journey

- [x] Pull an exact cloud dataset version into a local replica and read the same
  dataset and examples without constructing a LangSmith client.
- [x] Pull the same version again and observe an idempotent result.
- [x] Replicate an archive snapshot into the local store.
- [x] List replica versions and select one with `--as-of`.
- [x] Exercise every replica list filter, sort, projection, count, and pagination
  option against both archive and local stores.
- [x] Exercise selected-version, all-version, repeated/idempotent, and invalid
  archive-to-local transfers through the public CLI.

## Correctness invariants

- [x] One exact `DatasetVersion.as_of` is resolved before examples are fetched.
- [x] Publication exposes a new head only after Example Parquet, an authenticated
  immutable manifest, and all attachment blobs are present.
- [x] Concurrent independent versions both publish after CAS retries; concurrent
  divergent content for one timestamp produces one winner and one typed conflict.
- [x] Parquet objects are content-addressed, local writes replace atomically, and
  every Parquet/attachment digest is verified before deserialization.
- [x] `(Dataset ID, canonical UTC as_of)` is idempotent only for identical
  Example/attachment content; mutable Dataset catalog metadata refreshes separately.
- [x] Every Example belongs to the enclosing Dataset and duplicate IDs are rejected.
- [x] LangSmith SDK model-field drift fails before publication instead of silently
  omitting a new field.
- [x] Malformed head/manifest JSON, cross-dataset manifest references, and row-count
  mismatches fail as typed schema/integrity errors before SDK reconstruction.
- [x] Strict Pydantic Dataset and Example SDK fields round-trip without changing
  IDs, timestamps, schemas, transformations, metadata, splits, or lineage.
- [x] Equivalent timestamp offsets collapse to one version identity and naive
  timestamps fail before publication.
- [x] A version tag has at most one owner and selected-version transfers synchronize
  moved tag pointers.
- [x] Redirecting an authenticated manifest to another valid Parquet object fails.
- [x] Cloud/replica transfers stream Examples; attachment publication uses bounded
  chunks; DuckDB staging is disk-backed and memory-capped.
- [x] A global offline `examples list --as-of` skips unrelated histories that do
  not contain the requested version.
- [x] Attachment bytes are content-addressed and reconstructed with their name and
  MIME type.
- [x] A dataset name cannot silently replace a different dataset ID in one store.
- [x] Local and archive reads never instantiate or call the cloud client.
- [x] Filtering and sorting always happen before `--offset`/`--limit`, independent
  of whether the source performs some filters server-side.
- [x] `latest` version selection is based on timestamps, never backend return order.
- [x] Mutually exclusive transfer selectors and physically identical source/target
  repositories fail before any snapshot is copied.

## CLI and regression coverage

- [x] `datasets pull`, `datasets versions`, source-aware `datasets list/get`, and
  source-aware `examples list/get` cover both human and `--json` output.
- [x] Existing cloud command arguments and calls remain unchanged by default.
- [x] Invalid source/target combinations and unsupported offline filters fail with
  actionable errors.
- [x] Non-positive limits, negative offsets, and non-object JSON option payloads
  fail at the CLI boundary instead of producing source-dependent results.
- [x] `skills/langsmith/SKILL.md` documents every new argument and workflow.
- [x] Focused tests, full pytest, Ruff, Pyright, and startup-latency checks pass.

## Final reality evidence

- Public CLI matrix: 43/43 archive/local query, output, transfer, idempotence,
  round-trip, and rejection cases passed.
- Automated suite: 1,463 tests passed in 118.72 seconds; Ruff and Pyright passed.
- Normal import latency stayed flat: parent median 273.8 ms, current median
  273.9 ms over 20 warm samples. The operation-only Pydantic query/version modules
  were absent from `sys.modules` after normal CLI import.
- A selected tag reads exactly one authenticated manifest; filter-only cloud pages
  stop after `offset + limit` survivors, while global sort is the only list operation
  that requires full eligible-set materialization.
