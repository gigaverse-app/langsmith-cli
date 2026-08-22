# Unified trace sources: one CLI over cloud, archive, and local data

**Status:** Proposed · **Date:** 2026-08-22 · **Goal:** Make live LangSmith,
durable archives, and disposable local trace caches queryable through one honest CLI
contract without hiding their different freshness and completeness guarantees.

## TL;DR

1. **Use one named source at a time.** `runs list`, `search`, `get`, and
   `get-latest` should accept `--source <name>` and construct the same typed query
   for cloud, archive, and local data. Cloud remains the default for backward
   compatibility.
2. **Choose by intent, not by storage technology.** Cloud is for the freshest data
   and live-only operations; an archive is for durable, shared historical truth;
   local is a disposable cache for repeated analysis and offline intermediate work.
3. **Local is an accumulating inventory, not a replaceable snapshot.** Pulling or
   importing traces adds or refreshes those trace bundles without removing unrelated
   cached traces. The cache has no automatic size limit; users remove individual
   traces or evict it when it stops being useful.
4. **Defer multi-source result merging.** It could bridge live retention and archive
   history, but overlap, freshness, identity, ordering, and coverage make an
   apparently simple union unsafe. A source-comparison command has earlier value and
   lower semantic risk.
5. **Adopt LangSmith datasets wholesale.** There is no parallel collection abstraction.
   Cloud, archive, and local expose datasets, examples, versions/tags, schemas,
   transformations, splits, metadata, attachments, and `source_run_id` with the same
   meanings. Full traces remain separate data that may be materialized explicitly.

| Decision | Recommendation |
|---|---|
| User-facing selector | Named `--source`; default is `cloud` |
| Query commands | One typed query and view path for every readable source |
| Local execution | DuckDB over Parquet cache fragments only |
| Local writes | Immutable Parquet deltas plus an atomically revised inventory |
| Dataset organization | Existing LangSmith Dataset and Example contracts; no parallel collection model |
| JSONL | Explicit import/export format; never a queryable cache fragment |
| Archive writes | Keep verified trace `sync/backfill`; add immutable dataset-version publication |
| Automatic source choice | Do not add; freshness and coverage are user-visible decisions |
| Multi-source query | Deferred; add explicit comparison before federation |

## Product principles

**A trace source is a user-visible choice about truth, not an implementation
optimization.** The selector answers “which copy of the data do I trust for this
task?” before DuckDB, S3, or an API enters the picture.

| Principle | User promise | Consequence |
|---|---|---|
| Explicit truth boundary | The CLI says which source answered | Never fall back or switch sources silently |
| Honest absence | Zero rows means “no matches within known coverage” | Partial or unknown coverage produces a visible warning |
| Stable facade | Common read intent has common flags and output | Source-specific adapters cannot reinterpret a predicate |
| No surprise movement | Reading one source does not copy to another | Every transfer is a separate, directional command |
| Read-only derived copies | Archive and local cannot mutate cloud state | Writes fail before scanning or loading unrelated credentials |
| Progressive commitment | A one-off query does not require a local cache | Cache only when repetition, isolation, or offline use earns it |
| Dataset/trace separation | An example may retain `source_run_id`, but the source trace is not dataset content | Dataset transfer does not imply trace transfer |

Users should be able to choose in seconds:

| If the deciding question is… | Choose |
|---|---|
| “What is true in LangSmith right now?” | Cloud |
| “What did the organization durably retain?” | Archive |
| “What is in the disposable cache on this machine?” | Local |

The CLI should reinforce that choice in diagnostics. Human output identifies a
non-default source in its heading; verbose output reports source revision and
coverage; JSON row output stays backward-compatible and sparse. `runs source
status <name>` is the authoritative way to inspect freshness and coverage before a
query.

## Product contract

**The same query should mean the same thing everywhere, while source selection makes
freshness and coverage explicit.** The CLI may execute through LangSmith or DuckDB,
but it must return the same validated LangSmith `Run` contract and use the existing
JSON/table/CSV/YAML views.

```bash
# Fresh data in LangSmith Cloud (default)
langsmith-cli --json runs search "timeout" \
  --source cloud --project prd/my-agent --last 24h

# Durable history in the organization archive
langsmith-cli --json runs search "timeout" \
  --source archive --project prd/my-agent --last 365d

# Whatever has been accumulated locally
langsmith-cli --json runs search "timeout" \
  --source local --project prd/my-agent --last 30d

# The same dataset/examples facade through any readable source
langsmith-cli --json examples list \
  --source local --dataset incident-2841 --as-of prod
```

Read commands share the facade. Commands whose behavior depends on LangSmith itself
remain cloud-only and fail clearly for other sources.

| Command family | Cloud | Archive | Local | Product rule |
|---|---:|---:|---:|---|
| `runs list/search/get/get-latest` | Yes | Yes | Yes | Same typed query and output contract |
| Row-derived analytics (`usage`, `pricing`, discovery) | Yes | Later | Later | Migrate after core-read predicate and normalization parity |
| Service aggregate `runs stats` | Yes | No | No | LangSmith service metrics are not reconstructed from retained rows |
| `runs watch` | Yes | No | No | Watching requires a mutable live service |
| `runs open` | Yes | No | No | The LangSmith UI owns cloud URLs |
| Run mutations or feedback writes | Yes | No | No | No implicit write-back from retained copies |
| `datasets list/get`, `examples list/get`, versions | Yes | Yes | Yes | Same strict SDK contracts and `as_of` semantics |
| Dataset/Example mutations | Yes | No | No | Cloud applies validation, transformations, attachments, and version creation |

Selecting an incapable source is an error, not an invitation to fall back to cloud.
Selecting a capable source with incomplete coverage is different: the query may
return matching rows, but it also emits structured coverage evidence and a warning.
Automation can opt into a strict coverage policy and fail unless the request is
fully covered.

### Scope and non-goals

The initial scope is trace-row reads, source inspection, explicit materialization,
and source-aware dataset/example reads and replication. The Dataset and Example
contracts are copied, not reinterpreted. Prompts, feedback, annotation queues,
experiments, public sharing, and resource permissions remain separate LangSmith
service resources; adopting the dataset model does not imply cloning every adjacent
cloud feature. Restoring retained trace rows into LangSmith, treating a workstation
cache as archive truth, automatic source selection, and multi-source result union
are not goals of this design.

### Resolved CLI surface

**Use `runs source` for source lifecycle and comparison; keep data reads on the
existing `runs` commands.** The source command family is singular because every
lifecycle command has one explicit target, except the deliberately two-sided
comparison. Existing `datasets` and `examples` gain the same source selector.

| Command | Contract |
|---|---|
| `runs source list` | List configured source names, kinds, and capabilities |
| `runs source status <name>` | Report availability, coverage, freshness, and cache/archive health |
| `runs source pull local --from <cloud\|archive>` | Add or refresh selected full trace bundles; preserve unrelated cached traces |
| `runs source import local <path> --format jsonl` | Validate and convert external JSONL into the Parquet cache |
| `runs source remove local <selector>` | Remove selected traces; never affect datasets or another source |
| `runs source evict local` | Delete the disposable local trace inventory; dataset replicas are separate |
| `runs source compare <left> <right>` | Report bounded identity/content differences without merging rows |
| `datasets list/get/status --source <name>` | Read the Dataset contract; status adds replica lineage/head/attachment health |
| `examples list/get --source <name> --dataset <dataset>` | Read examples, splits, attachments, and `--as-of` versions uniformly |
| `datasets pull <dataset> --from <source> --to <source> [--as-of <version>\|--all-versions]` | Replicate exact dataset version(s) and their examples/attachments |
| `datasets versions --source <name>` / `datasets tag --source cloud` | Inspect versions everywhere; let LangSmith mutate version tags |
| `datasets create/delete` and `examples create/update/delete/from-run` | Cloud-only mutations using the existing LangSmith model |
| `datasets evict <dataset> --source local` | Remove a disposable replica; this is cache lifecycle, not a Dataset mutation |

`runs cache download/list/clear` remain temporary aliases for local
pull/status/evict. Commands fail before loading a backend when the selected source
lacks the requested capability. A local trace pull must select traces by IDs or a
bounded project/time/filter query. It performs an idempotent set-union/upsert: new
trace identities are added, a newer observation of an existing trace replaces its
logical cached value, and unrelated inventory is preserved. No command silently
evicts traces to meet a size target.

### Machine-readable coverage

**Preserve sparse `--json` and add an explicit envelope mode.** Existing automation
continues to receive a run array. `--json-envelope` is a mutually exclusive output
mode for callers that need source and coverage evidence:

```json
{
  "runs": [],
  "source": {"name": "local", "kind": "cache", "revision": "..."},
  "coverage": {"state": "partial", "requested": {}, "available": {}},
  "diagnostics": {"scanned_fragments": 0, "warnings": []}
}
```

Human and sparse JSON modes warn on partial/unknown coverage through stderr. The
source-aware read commands also accept `--require-complete`; when coverage cannot
satisfy the request, they exit nonzero without presenting rows as a complete
answer. `runs source status <name> --json` remains the preflight interface for full
source metadata.

Dataset/example sparse JSON likewise remains the SDK model shape. Replica lineage,
source namespace, resolved `as_of`, content digest, attachment health, and optional
linked-trace status live in `datasets status <dataset> --json` or an explicit
dataset envelope, never as invented Dataset/Example fields.

## Terminology and current state

**A source, backend, catalog, fragment, and store are different layers.** Keeping
them separate lets local and S3 data share DuckDB without pretending that a file
path and a remote service have the same lifecycle.

| Term | Meaning | Examples |
|---|---|---|
| Trace source | Named query origin selected by the user | `cloud`, `archive`, `local` |
| Trace backend | Implements query/count/get for one source | LangSmith backend, DuckDB backend |
| Catalog | Discovers relevant data fragments and their coverage | Archive manifests, local cache metadata |
| Fragment | Queryable unit with project, time, and row metadata | One canonical Parquet partition or cache fragment |
| Store | Reads/writes catalog and data objects | S3, local filesystem |
| Executor | Runs a physical query | LangSmith SDK/API, DuckDB |

The repository already contains two local models:

- `LocalArchiveStore` gives the canonical manifest/Parquet archive a filesystem
  store equivalent to S3.
- The current local run cache stores per-project JSONL plus `CacheMetadata`, but
  only cache and usage commands treat it as a source. General run commands do not.

The design does not promote that JSONL layout or create a third archive. It replaces
the cache storage with Parquet and adds a backend-neutral query boundary plus enough
cache metadata to make freshness and coverage honest. Old JSONL may be handled by
an explicit one-way importer; it is not auto-discovered as a source.

## Architecture

**Commands construct one logical query; source resolution selects how it is
executed.** DuckDB is shared by archive and local sources, while their catalogs and
coverage models remain distinct.

```text
runs list / search / get / get-latest
                  |
                  v
           typed TraceQuery
                  |
                  v
        TraceSourceRegistry.resolve(name)
             /                    \
            v                      v
 LangSmithTraceBackend      DuckDBTraceBackend
            |                 /              \
            v                v                v
   LangSmith SDK/API   ArchiveCatalog   LocalCacheCatalog
                           |                    |
                           v                    v
                     S3/local               local Parquet
                     manifests                 cache
             \                    |                    /
              \                   v                   /
               +---- canonical Run normalization ----+
                                  |
                                  v
                   TracePage(runs, source, coverage)
                                  |
                                  v
                      existing output/view layer
```

Datasets use the same source registry but a separate typed backend so trace and
dataset semantics cannot leak into one another:

```text
datasets / examples commands
           |
           v
  typed DatasetQuery / ExampleQuery
           |
           v
 DatasetSourceRegistry.resolve(name)
      /                         \
     v                           v
LangSmithDatasetBackend   DuckDBDatasetReplicaBackend
     |                       /               \
     v                      v                 v
strict SDK models      archive manifest   local replica catalog
                              \               /
                               v             v
                      strict SDK-model round trip
```

### Backend contract

The command layer depends on a small strongly typed protocol:

```python
class TraceBackend(Protocol):
    capabilities: TraceCapabilities

    def query(self, query: TraceQuery) -> TracePage: ...
    def count(self, query: TraceQuery) -> TraceCount: ...
    def get(self, lookup: TraceLookup) -> TraceBundle: ...

class DatasetBackend(Protocol):
    capabilities: DatasetCapabilities

    def list_datasets(self, query: DatasetQuery) -> DatasetPage: ...
    def get_dataset(self, lookup: DatasetLookup) -> Dataset: ...
    def list_examples(self, query: ExampleQuery) -> ExamplePage: ...
    def get_example(self, lookup: ExampleLookup) -> Example: ...
    def list_versions(self, lookup: DatasetLookup) -> DatasetVersionPage: ...
```

`search` is a query with a text predicate. `get-latest` is a query ordered by
`start_time` descending with limit one. `get --follow-children` is a lookup with
trace expansion. They are not separate backend capabilities.

`Dataset`, `Example`, and `DatasetVersion` above are the pinned LangSmith SDK
models. Mutation is a separate cloud-only capability protocol in the initial
release; a read backend cannot accidentally expose create/update/delete methods.

### Typed query model

**CLI flags are parsed once into backend-neutral semantics.** Backends compile a
typed predicate tree rather than reimplementing flag behavior in command adapters.

| Query component | Representative typed values |
|---|---|
| Project selection | exact ID/name, glob, regex |
| Exact identity selection | one or more scoped run/trace IDs |
| Time | half-open UTC `[since, before)` |
| Predicates | status, root/child, run type, tag, metadata, latency, text |
| Text search | fields, literal/regex, case sensitivity |
| Result shape | order, limit, count, projection, trace expansion |

LangSmith compiles predicates to SDK arguments and FQL. DuckDB compiles the same
predicates to SQL with bound values and an allowlist for identifiers. Raw `--filter`
remains an explicitly cloud-only escape hatch until it can be parsed into the typed
predicate model; unsupported semantics fail instead of being approximated.

### Result and coverage model

**Rows alone are not enough to interpret a local or archived result.** Every backend
returns source and coverage evidence even when the default view renders only runs.

```text
TracePage
  runs                 validated LangSmith Run objects
  source               source name, kind, and revision/generation
  coverage             project/time or identity scope, completeness, freshness, filters
  diagnostics          scanned fragments, skipped/corrupt fragments, warnings
```

Coverage uses typed states rather than a boolean:

| State | Meaning |
|---|---|
| Authoritative | Backend is the live authority for its stated availability window |
| Sealed | Verified immutable project/time partitions cover the requested interval |
| IdentityComplete | Every explicitly requested trace identity has a complete bundle |
| Filtered | Materialization deliberately contains only a recorded predicate subset |
| Partial | Some requested projects or intervals are absent |
| Unknown | Imported/external data has insufficient evidence to claim coverage |

Machine-readable run output remains sparse and backward-compatible. Coverage
warnings go to stderr, while `runs source status <name> --json` returns the full
evidence. `--json-envelope` returns the complete `TracePage` evidence with the runs.

## Local trace cache

**Local is a disposable acceleration cache for intermediate work, not a retained
dataset or system of record.** It is the growing set of trace bundles the user has
chosen to keep on this machine. Pulling more data expands that set; it does not
declare a new global scope or delete unrelated work. Anything that must survive, be
shared as evidence, or be reproduced later belongs in the archive or an explicit
export.

| Property | Local contract |
|---|---|
| Authority | Never authoritative; derived from cloud, archive, or explicit import |
| Durability | None; loss is recovered by pulling/importing again |
| Contents | An accumulating, deduplicated inventory of trace bundles; source transfers are complete, explicit imports may be `Unknown` |
| Capacity | No configured maximum and no automatic LRU/TTL eviction initially |
| Mutability | Published fragments are immutable; additions publish new fragments and one catalog revision |
| Storage | Parquet only |
| Correctness | Metadata separates item presence, observation freshness, and coverage evidence |
| Eviction | Explicit per-trace removal or whole-cache eviction; compaction is physical only |

The logical unit of caching is a **trace bundle**: the root run and its descendants
for one stable `trace_id`. Source pulls require the complete
bundle; an explicit external import may be admitted only with `Unknown`
completeness. Individual run rows remain the physical Parquet records, but commands
do not cache a child run without resolving its root trace. This makes
deduplication, removal, and transfer well-defined.

Adding an already-cached trace is idempotent. If its canonical content digest is
unchanged, only new provenance is recorded. If the same stable
identity is observed again with different content, the newest successful
observation becomes the logical value; the older physical row version remains
invisible and can be discarded by compaction. The cache never merges two versions
field by field.

### Minimal cache metadata

The cache needs a typed inventory catalog and coverage ledger, not an archive
manifest. If the active catalog is absent, incompatible, or corrupt, the CLI treats
the cache as unavailable and asks the user to rebuild it; it never scans whatever
files happen to be present.

| Field | Why it is required |
|---|---|
| Catalog revision | Gives each reader one atomic view of fragments and inventory |
| Scoped trace identity | Provider/workspace, project ID, and root `trace_id`; names are labels only |
| Fragment sequence and schema version | Selects the newest logical trace version and the only paths DuckDB may scan |
| Trace digest, run count, and completeness | Detects conflicting observations and incomplete bundles |
| Origin source and observation time | Explains provenance and staleness per trace/batch |
| Pull-batch predicate and absolute UTC bounds | Records why items were selected and supports honest coverage |
| Coverage evidence | Distinguishes “this item exists” from “this entire interval was materialized” |

Coverage is a ledger over successful pull batches, not a bounding box inferred from
the oldest and newest cached row. For example, traces from Monday and Friday do not
prove Tuesday–Thursday coverage. A project/time query is complete only when the
union of compatible, unfiltered successful batch records covers the requested
interval. An exact-ID query can be `IdentityComplete` even when no continuous time
interval is covered.

### Cache filesystem layout

**Use one versioned application-cache root and one active pointer.** The default is
derived from `platformdirs.user_cache_dir("langsmith-cli", appauthor=False)` so it
follows each operating system's cache conventions:

```text
<langsmith-cli user cache>/traces/v1/local/
  active.json
  fragments/
    runs-<sequence>-<uuid>.parquet
    inventory-<sequence>-<uuid>.parquet
    coverage-<sequence>-<uuid>.parquet
  staging/
```

`active.json` is a small typed control file containing the catalog revision and the
approved relative fragment paths. Trace inventory and pull-batch evidence are
stored as typed Parquet tables referenced by that catalog so the metadata remains
queryable as it grows. An addition writes and validates uniquely
named fragments under `staging/`, renames them into `fragments/`, and atomically
replaces `active.json`. Readers therefore see either the old inventory or the old
inventory plus the complete addition. Orphaned staged/unreferenced files are safe to
delete during recovery.

Compaction writes replacement run and inventory fragments, validates that their
logical trace set is identical, and atomically publishes a new catalog revision. It
may remove superseded trace observations, but it must not change which traces the
user sees. Old referenced
fragments are deleted only after existing readers release their catalog revision.

### JSONL versus Parquet for the cache

**Choose Parquet as the single local-cache format.** JSONL remains useful at an
explicit import/export boundary, but it is never cataloged or queried as local cache
storage.

| Criterion | JSONL | Parquet | Cache priority | Winner |
|---|---|---|---:|---|
| Selective queries | Parses candidate objects and fields | Projection and filter pushdown skip columns and row groups | High | Parquet |
| Repeated aggregation | Repeats text parsing and nested conversion | Reuses typed columnar encoding | High | Parquet |
| Schema | Sampled inference can drift unless every read supplies a schema | Typed schema is embedded in every file | High | Parquet |
| Disk and I/O | Repeats keys and text values per row | Per-column encoding and compression | High | Parquet |
| Parallel scans | Limited by parsing work and file layout | Parallelizes across files and row groups | High | Parquet |
| Atomic additions | Partial appended lines can expose corruption | Staged immutable fragments are validated before catalog publication | High | Parquet |
| Streaming append | Natural byte append | Adds immutable batch fragments; compacts later | Medium; the inventory grows | Tie on capability; Parquet wins overall |
| Manual inspection | Readable with text tools | Requires DuckDB or another reader | Low; the CLI is the inspection facade | JSONL |
| Generic interchange | Nearly universal | Broad analytical support but binary | Low; import/export handles this | JSONL |

JSONL wins capabilities that are explicitly not cache requirements: hand editing,
tail append, and zero-tool inspection. Parquet wins the analytical path for which
the cache exists. DuckDB's Parquet reader supports projection and filter pushdown,
including row-group skipping from column statistics; its JSON reader otherwise
needs explicit columns to avoid sampled-schema behavior. See DuckDB's official
[Parquet reader](https://duckdb.org/docs/current/data/parquet/overview),
[Parquet writing guidance](https://duckdb.org/docs/current/data/parquet/tips), and
[JSON reader](https://duckdb.org/docs/current/data/json/loading_json).

| Situation | Required behavior |
|---|---|
| Pull from cloud | Resolve complete trace bundles, normalize in DuckDB, and add Parquet fragments |
| Pull from archive | Read/copy selected complete trace bundles and extend the inventory |
| Import JSONL | Explicit importer validates and converts to Parquet before the cache becomes visible |
| Export for a person or generic tool | DuckDB writes JSONL outside the cache root |
| Discover an old JSONL cache | Ignore it; offer an explicit import or pull command |

There is no direct JSONL query mode and no transparent format conversion during a
read. This keeps the backend, schema, tests, and failure semantics single-format.

JSONL import handles one project per invocation. Every row is validated as a
canonical `Run`; all non-null `session_id` values must agree. That ID is used as the
project ID, or `--project-id` is required when the field is absent; an explicit ID
must agree with any value in the rows. `--project-name` is an optional display label,
the filename is never identity, and arbitrary imports receive `Unknown` coverage
unless accompanied by a CLI-produced evidence sidecar that validates successfully.
Rows are assembled by root `trace_id` and must be internally closed under their
declared parent links. Without evidence that the export contained every descendant,
bundle completeness remains `Unknown`: it is locally queryable but cannot satisfy
`IdentityComplete` or be published as verified trace evidence until revalidated
against cloud or archive.

### DuckDB's role in the cache lifecycle

**DuckDB owns cache reads, additions, deduplication, and compaction; the catalog owns
atomic visibility and correctness evidence.** Catalog revisions are concurrency
mechanics, not durable cache history or a user-visible rollback feature.

| Responsibility | Owner |
|---|---|
| Query, normalize, validate, deduplicate, sort, and write Parquet | DuckDB |
| Inventory identity, origin, batch coverage, and freshness | Typed Parquet catalog tables |
| Atomic catalog publication, removal, and eviction | Cache manager |
| JSONL import/export conversion | DuckDB import/export adapter |

An addition follows this sequence:

| # | Step | Failure behavior |
|---:|---|---|
| 1 | Resolve trace IDs directly or from bounded project/time/filter selectors | Fail before changing the active inventory |
| 2 | Prefer source Parquet; otherwise stream API rows to a process-private staging spool for explicit-schema DuckDB ingestion | Staging is never a cache fragment and is deleted on interruption |
| 3 | Resolve each selected run to its root, fetch the complete trace bundle, and validate schema, identities, and row counts | Any malformed/incomplete trace fails by default |
| 4 | `COPY` sorted data to uniquely named Zstd Parquet files in a staging directory | Existing cache remains readable |
| 5 | Reopen and validate the written Parquet; write inventory, provenance, and coverage deltas | Invalid output is deleted |
| 6 | Atomically publish a catalog that references the old fragments plus the complete addition | Readers see either inventory before or after the addition |
| 7 | Later compact superseded versions/small deltas without changing the logical sets | No user-visible history or rollback promise |

Pull always preserves traces outside the request. It never appends bytes to a
published Parquet file; “append” means publish another immutable fragment and merge
its trace identities into the logical inventory. Explicit `remove` publishes
tombstones or a replacement compacted catalog, while `evict` clears the whole
inventory. DuckDB warns that excessive small partitions are expensive in its
[partitioned-write guidance](https://duckdb.org/docs/current/data/partitioning/partitioned_writes).

### Initial Parquet physical defaults

**Start with one sorted file for small caches and approximately 512 MiB files for
larger caches.** These are implementation defaults, not part of the source API.

| Setting | Initial decision | Rationale |
|---|---|---|
| Sort order | `session_id, start_time, id` | Tightens project/time zonemaps and makes tie ordering deterministic |
| Compression | Zstd, level 3 | Existing archive choice; balanced cache size and decompression cost |
| Row group | DuckDB default `122,880` rows | Falls inside DuckDB's recommended 100K–1M range |
| File target | `FILE_SIZE_BYTES='512MB'` | Inside DuckDB's recommended 100 MB–10 GB range without giant local files |
| Small cache | One file; do not pad or partition | Avoids tiny-file overhead |
| Hive partitioning | None initially | Cache metadata and Parquet statistics already prune; project/day folders would fragment small caches |
| Write result | `RETURN_STATS` | Populate file paths, counts, sizes, and column bounds without rescanning |

The cache manager compacts when fragment count or small-file overhead crosses an
implementation threshold; that threshold affects performance only, never retention.
The implementation records actual file and row-group statistics and adds a
benchmark before changing these defaults. DuckDB documents `FILE_SIZE_BYTES`,
`ROW_GROUP_SIZE`, and `RETURN_STATS` in its
[`COPY` reference](https://duckdb.org/docs/current/sql/statements/copy) and publishes
the file/row-group ranges in its
[file-format performance guide](https://duckdb.org/docs/current/guides/performance/file_formats).

A persistent `.duckdb` file is outside this JSONL-versus-Parquet decision. Because
the cache is disposable, DuckDB storage-version coupling is not a durability
blocker; however, native storage has different multi-process locking and incremental
write tradeoffs. The initial Parquet choice maximizes reuse of the archive schema
and query compiler and keeps concurrent readers on immutable fragments. Native
DuckDB storage should be evaluated only as a separate performance proposal, not
introduced accidentally as a third format.

## Datasets across sources

**Dataset means the LangSmith Dataset model everywhere.** The CLI will not introduce
another trace-collection type, reinterpret examples as membership rows, or discard
dataset features that are inconvenient outside cloud. `datasets` and `examples` become source-aware
facades over replicas of the SDK contracts.

### Replicated contract

The pinned LangSmith SDK models are the schema authority. The replica preserves all
fields rather than maintaining a hand-written “common subset”:

| Contract | Replicated semantics |
|---|---|
| Dataset | ID, name, description, data type, created/modified times, counts, input/output schemas, transformations, metadata |
| Example | ID, dataset ID, inputs, outputs/reference outputs, metadata, created/modified times, optional `source_run_id`, attachments |
| Splits | The same example split assignments and filtering behavior exposed by LangSmith |
| Versions | Every example add, update, or delete creates a timestamped dataset version |
| Version tags | Human-readable tags resolve to an exact immutable `as_of` version; the tag pointer itself may later move |
| Attachments | Names, media types, bytes, and attachment operations; never expiring URLs as durable content |

“Wholesale” does not mean silently accepting future SDK drift. Every storage schema
is stamped with the LangSmith model/schema version it implements. A newly required
or changed SDK field fails fast until an explicit migration and round-trip fixture
are added. Dataset and Example are strict typed objects at the backend boundary;
replica catalog metadata is a separate typed transfer envelope, never injected into
their metadata fields.

The complete Dataset envelope is mutable catalog state, exactly as it is in
LangSmith. A successful pull refreshes its name, description, schemas, metadata,
counts, and timestamps without rewriting an immutable DatasetVersion. Those fields
remain the last source observation while the replica is offline; service-derived
session/experiment summaries do not imply that their runs or feedback were copied.

This contract follows LangSmith's official
[dataset management model](https://docs.langchain.com/langsmith/manage-datasets-in-application),
[Example schema](https://docs.langchain.com/langsmith/example-data-format),
[version/tag semantics](https://docs.langchain.com/langsmith/manage-datasets), and
[attachment behavior](https://docs.langchain.com/langsmith/evaluate-with-attachments).

### Dataset content versus source traces

**An example's `source_run_id` is lineage, not ownership of the source trace.** A
dataset remains complete and useful when an example has no source run or when the
source trace has expired. Pulling a dataset therefore copies Dataset, Example,
version, split, schema/transformation, metadata, and attachment state; it does not
copy traces by default.

Callers that need the original traces request a separate companion materialization:

```bash
langsmith-cli --json datasets pull incident-2841 \
  --from cloud --to local --as-of prod \
  --include-source-traces
```

The dataset version is replicated first and remains valid even if trace
materialization is incomplete. For each non-null `source_run_id`, the optional trace
phase resolves the run to its root `trace_id`, fetches a complete trace bundle, and
adds it to the ordinary destination trace inventory. Null links are normal and are
reported as `examples_without_source_run`; expired/missing linked runs fail the trace
phase by default or are recorded explicitly with `--allow-partial-traces`. The
dataset object and examples are never changed to encode trace-transfer status.

### Backend behavior

| Source | Dataset role | Mutability in the initial design | Version behavior |
|---|---|---|---|
| Cloud | LangSmith authority | Full existing Dataset/Example APIs | LangSmith creates versions and applies schema validation/transformations |
| Archive | Durable replica | Publish exact source versions only | Retains every published version, tags, examples, and attachments immutably |
| Local | Disposable working replica | Read-only replica initially | Retains downloaded versions until explicit dataset deletion or cache eviction |

Cloud is initially the only mutation authority. This is deliberate: reproducing
LangSmith schema validation, transformations, split mutation, attachment operations,
and version creation in local/archive code would recreate the drift risk this
decision is meant to eliminate. “Append to an archive dataset” means mutate the
cloud dataset through its normal API, then publish its newer exact version to the
archive. Local authoring can be added only if the same SDK-backed mutation engine
can produce byte-for-byte and version-for-version parity.

Archive and local still satisfy the uniform read facade: `datasets list/get`,
`examples list/get`, split/metadata filters, attachment inclusion, and `--as-of`
resolve with the same typed semantics. Unsupported cloud-only adjacency—running an
experiment, sharing publicly, or inspecting associated experiment runs—fails as a
capability error rather than being approximated.

`datasets pull` defaults to the source's resolved `latest` version. `--as-of`
selects one timestamp or tag; `--all-versions` replicates every available version
and tag mapping. `datasets versions --source archive|local` reports versions actually
present in that replica, while `datasets status` distinguishes a one-version checkout
from a complete copied history.

### Identity, lineage, and conflict policy

**A replica is identified by source namespace plus Dataset ID, never by name.** The
destination catalog stores the current Dataset envelope plus fetched versions,
content digests, and manifest digests. Dataset and Example IDs are preserved in
local/archive replicas. LangSmith's DatasetVersion freezes Example
membership/content and attachments; the complete Dataset envelope remains mutable
head state and is deliberately excluded from immutable version identity. An
archive/local `datasets get --as-of` validates that the requested version exists but
returns the current Dataset envelope, because LangSmith exposes no historical
Dataset-metadata API.

| Situation | Decision |
|---|---|
| Pull same dataset/version again | Idempotent version no-op after digest verification; refresh mutable Dataset catalog state |
| Pull a later version from the same lineage | Fast-forward; retain the older version and publish the new immutable head |
| Pull an older version from the same lineage | Add the historical snapshot; do not move the current head backward |
| Same name, different source Dataset ID | Keep as distinct datasets; ambiguous name lookup fails |
| Destination head is not an ancestor of incoming version | Fail divergence; no example-by-example merge |
| Same lineage and `as_of`, different canonical Example/attachment digest | Fail corruption/divergence; never choose one silently |
| Example deleted in a newer version | Absent from that version, present when reading an older `as_of` version |
| Source dataset later deleted | Existing local/archive replicas remain; deletion never propagates implicitly |

No union or mirror policy is invented. Dataset updates and deletions already have
meaning through LangSmith versions; replaying exact versions preserves that meaning.
A future writable local fork must receive a new lineage and cannot masquerade as a
fast-forward replica.

### Dataset replication transaction

`datasets pull` freezes one source version and publishes it atomically:

| Step | Required behavior |
|---:|---|
| 1 | Resolve the source dataset by ID/name and freeze an exact timestamp/tag target |
| 2 | Read the current Dataset catalog object, version/tag records, and all examples at that `as_of` |
| 3 | Stream attachment bytes into bounded local staging, validate media/name/digest metadata, and reject expiring URLs as stored content |
| 4 | Validate every strict SDK object and cross-reference Dataset/Example IDs |
| 5 | Stream Examples through a disk-backed DuckDB staging table into content-addressed Parquet; stage content-addressed attachment blobs and one unique immutable manifest |
| 6 | Authenticate the manifest digest from the head, verify object/canonical digests, then compare-and-swap the destination head; on contention, reread and merge independent versions and unique tag pointers |
| 7 | Optionally materialize linked source traces as a separate, auditable phase |

The dataset transaction is all-or-nothing. The optional trace phase has its own
status so an expired source trace cannot corrupt or roll back a valid dataset
replica. In strict mode a trace-phase failure exits nonzero but reports that the
dataset version committed successfully; rerunning is idempotent and retries only
missing traces.

### Physical storage

Dataset replicas are separate from the trace cache while sharing DuckDB as the
reader:

```text
<replica-root>/
  datasets/
    heads/<dataset-id>.json             # schema v2: current Dataset + versions
    <dataset-id>/versions/<sha256(as-of)>/
      objects/
        examples-<sha256>.parquet
      manifests/<publication-uuid>.json
    blobs/<attachment-sha256>
  .locks/<head-key-sha256>.lock        # local backend only
```

The head is the only reachability boundary. Writers may leave unreachable staged
objects after losing a race, but they cannot overwrite the bytes selected by the
winning head: Parquet/blob keys are content-addressed and manifests are unique.
Local writes stream to an adjacent temporary file, `fsync`, and atomically replace
the target. Each head version stores the SHA-256 of its exact manifest bytes; readers
verify that trust edge, the Parquet/blob digests, row counts, cross-references, and
the canonical Example/attachment digest before constructing any SDK Example.

Parquet remains the typed query format for immutable Example-version rows. The
current strict Pydantic Dataset envelope and movable tag pointers live in the CAS
head because they are mutable LangSmith catalog state. Attachments
are content-addressed binary blobs because wrapping arbitrary media in Parquet would
not reproduce LangSmith attachment semantics. Heads expose only authenticated
manifests and validated versions. Dataset names, version tags, attachment names, and
raw timestamps are metadata only and never become path components; all identity
timestamps are canonical aware UTC. Publication uses 1,000-row Pydantic batches, a
disk-backed DuckDB staging database, and a dataset-specific memory cap. Archive uses
the equivalent object layout with conditionally published heads and durable
retention; local uses the same schema without a durability promise. Local
performs no automatic version, TTL, size, or attachment eviction; `datasets evict`
removes the replica explicitly, and garbage collection deletes attachment blobs
only when no visible version references their digest.

## Source selection

**Select the narrowest source that truthfully satisfies the task.** Do not optimize
for fewer keystrokes at the cost of freshness or completeness.

| Need | Preferred source | Why | Main caveat |
|---|---|---|---|
| Debug a run that just happened | Cloud | Freshest data and full live semantics | Retention, API/network dependency |
| Watch or mutate LangSmith state | Cloud | Only authoritative writable backend | Cannot work offline |
| Investigate history beyond retention | Archive | Durable sealed organization record | Read-only; publication lag |
| Shared audit/reproducible historical analysis | Archive | Verified immutable partitions | S3/IAM and scan cost |
| Repeated exploration of accumulated traces | Local | Low latency and no repeated API/S3 scan | Freshness and coverage must be checked |
| Curate or mutate a dataset | Cloud | LangSmith owns validation, transformations, attachments, and version creation | Requires service access |
| Reproduce a known dataset version | Archive | Exact durable Dataset/Example snapshot | Replica is read-only |
| Use a dataset offline | Local | Same examples/version without network access | Replica is disposable and read-only |
| Work offline or isolate sensitive analysis | Local | Files remain on the workstation | Local disk/security responsibility |
| One quick query already covered locally | Local | Avoid unnecessary transfer | Never assume the cache is current |

Default behavior remains cloud. There is no `--source auto`: the CLI cannot infer
whether the user values freshness, durability, privacy, or zero network use.

## Core workflows

**Query in place by default; cache only when the desired operating property
changes.** Copying to local is justified by offline/private intermediate work or
lower cost for repeated scans. Durable retention and reproducibility are separate
archive/export workflows.

| Workflow | Start with | Transfer trigger | End state |
|---|---|---|---|
| Debug a current incident | Cloud | Repeated deep analysis or impending offline work | Selected traces accumulate locally |
| Investigate old behavior | Archive | Repeated scans, offline work, or workstation tooling | Selected traces added locally with archive provenance |
| Retain organizational history | Cloud | Retention policy and scheduled archive window | Verified, sealed archive partitions |
| Curate evaluation examples | Cloud | A run/example is worth preserving for evaluation | New LangSmith dataset version |
| Reproduce an evaluation input set | Cloud dataset | Version is ready for durable/offline use | Exact archive and/or local dataset replica |
| Audit archive publication | Cloud plus archive comparison | Suspected lag, omission, or divergence | Parity report; no merged rows |

Do not transfer for a one-off query that the selected source can answer cheaply, to
make stale data appear fresh, or to enable a mutation. A local trace pull extends a
disposable working inventory; an archive trace sync or dataset-version publication
creates durable, verified state.

The commands below illustrate the target UX; they are design proposals, not a claim
that every form is implemented today.

### Current incident: stay live, then optionally cache

```bash
# Ask the live authority first.
langsmith-cli --json runs search "timeout" \
  --source cloud --project prd/my-agent --last 2h

# If the investigation will be repeated or taken offline, add that slice locally.
langsmith-cli --json runs source pull local --from cloud \
  --project prd/my-agent --since 2026-08-22T08:00:00Z \
  --before 2026-08-22T10:00:00Z

langsmith-cli --json runs search "timeout" \
  --source local --project prd/my-agent \
  --since 2026-08-22T08:00:00Z --before 2026-08-22T10:00:00Z
```

The explicit interval matters for a reproducible pull: `--last 2h` means something
different tomorrow. The pull ledger records absolute UTC bounds, origin, filters,
and observation time. It adds this slice to whatever is already local; it does not
make the cache globally equivalent to that interval.

### Historical investigation: query the archive, check out only if useful

```bash
# Query sealed history directly without downloading it first.
langsmith-cli --json runs search "timeout" \
  --source archive --project prd/my-agent \
  --since 2025-01-01 --before 2025-02-01

# Check out the same evidence only when repeated/offline analysis justifies it.
langsmith-cli --json runs source pull local --from archive \
  --project prd/my-agent --since 2025-01-01 --before 2025-02-01
```

The local metadata records the archive manifest generation and selected bounds so
the CLI can explain provenance and coverage. This does not make the cache durable;
eviction still requires another archive read.

### Dataset workflow: curate in cloud, preserve in archive, use locally

```bash
# LangSmith creates the Example and the next Dataset version.
langsmith-cli --json examples from-run 7b1c... \
  --dataset incident-2841

# Preserve the exact current Dataset/Example version durably.
langsmith-cli --json datasets pull incident-2841 \
  --from cloud --to archive --as-of latest

# Download that same version for offline/local use.
langsmith-cli --json datasets pull incident-2841 \
  --from archive --to local --as-of latest

langsmith-cli --json examples list \
  --source local --dataset incident-2841 --as-of latest
```

Append through the cloud model, then fast-forward both replicas:

```bash
langsmith-cli --json examples from-run 92ac... --dataset incident-2841
langsmith-cli --json datasets pull incident-2841 --from cloud --to archive
langsmith-cli --json datasets pull incident-2841 --from archive --to local
```

The first command lets LangSmith apply the dataset schema, transformations, and
version semantics. Each pull publishes the exact newer version while retaining the
older version for `--as-of` reads. It does not merge examples independently.

### Scheduled retention: publish, verify, then seal

Cloud-to-archive remains an operator workflow: discover the eligible window,
export, validate counts and identities, reconcile late arrivals, and publish a
manifest conditionally. It is neither a `runs list` mode nor a local-cache pull.
Consumers query only published generations; partially written data is invisible.

### Offline/private analysis: prove locality before relying on it

```bash
langsmith-cli --json runs source status local
langsmith-cli --json runs list --source local \
  --project prd/my-agent --since 2026-08-01 --before 2026-08-08
```

The status check exposes inventory counts, pull-batch coverage, freshness, and
fragment health.
Local querying then operates with cloud and archive credentials absent and network
access denied. “Private” here means no query-time transfer; users must still decide
whether downloading the traces was permitted in the first place.

### Publication audit: compare evidence instead of unioning it

```bash
langsmith-cli --json runs source compare cloud archive \
  --project prd/my-agent --since 2026-08-01 --before 2026-08-02
```

The report separates missing IDs, differing canonical row digests, source coverage,
and publication lag. It does not choose a winner, combine rows, or mutate either
source.

## Transfer model

**Transfers are directional materializations with different trust guarantees, not a
generic byte copy.** Query syntax is uniform; publication and pull syntax may
remain purpose-specific.

| From | To | Product purpose | Required semantics | Decision |
|---|---|---|---|---|
| Cloud traces | Archive traces | Long-term retention and shared historical truth | Full-window export, verification, reconciliation, sealing | Supported by `archive sync/backfill` |
| Cloud traces | Local trace cache | Fast/offline intermediate work | Explicit IDs/window/filter; add complete bundles and provenance | Supported trace pull |
| Archive traces | Local trace cache | Repeated/offline historical analysis | Add selected bundles and manifest provenance | Supported trace pull |
| Cloud dataset | Archive dataset | Durable reproducible evaluation data | Freeze and publish exact Dataset/Example version plus attachments | Supported dataset pull |
| Cloud/archive dataset | Local dataset | Offline evaluation inputs and inspection | Replicate exact version read-only | Supported dataset pull |
| Dataset source | Destination trace store | Optional original-trace analysis | Explicit `--include-source-traces`; separate trace-phase evidence | Supported companion materialization |
| Local traces | Canonical archive windows | Promote a partial cache to retention truth | Cache has no authoritative completeness guarantee | Unsupported; retention sync sources from cloud |
| Archive/local traces | Cloud | Restore traces into LangSmith | LangSmith is not a general trace-import target | Non-goal |
| Archive/local dataset | Cloud dataset | Create a new cloud lineage from a replica | IDs, validation, transformations, and conflict policy need a separate import design | Deferred |
| Archive | Archive | Storage migration or replication | Preserve trace manifests and dataset versions/identities | Separate operator workflow |

Cloud-to-archive remains deliberately specialized because D+2/D+12 reconciliation,
Bulk Export adoption, uniqueness checks, and conditional publication are stronger
than a local pull. The resolved pull command makes direction explicit:

```bash
# Add a bounded live slice to the cache
langsmith-cli --json runs source pull local --from cloud \
  --project prd/my-agent --last 7d

# Check out sealed history without contacting LangSmith
langsmith-cli --json runs source pull local --from archive \
  --project prd/my-agent --since 2025-01-01 --before 2025-02-01
```

Existing `runs cache download` remains a compatibility alias during migration.

## Multiple sources

**General federation is valuable but not required for the uniform single-source
facade.** Users may configure several sources and compare two bounded sources, but
the first release selects exactly one source to produce run or dataset/example
results for a query.
Transfer commands necessarily read one source and write another, but that is an
explicit materialization boundary—not a query that merges answers from two sources.

| Potential value | Example |
|---|---|
| Bridge retention boundary | Query recent cloud data plus older sealed archive data |
| Audit publication parity | Compare cloud rows with the corresponding archive day |
| Local overlay | Combine a disposable private cache with organization history |
| Availability fallback | Continue reading an archive when LangSmith is unavailable |

The same cases create non-obvious correctness requirements:

- Overlapping sources contain the same run with different freshness.
- Limit, ordering, and count must be applied after union and deduplication.
- Project names can move while IDs remain stable.
- A local subset cannot fill an archive coverage gap merely because it has rows.
- Automatic fallback can return stale data while appearing successful.

The near-term feature should be comparison, not union:

```bash
langsmith-cli --json runs source compare cloud archive \
  --project prd/my-agent --since 2026-08-01 --before 2026-08-02
```

A later composite source requires explicit membership, precedence, deduplication by
stable project/run identity, post-union ordering/limit/count, and combined coverage
evidence. It must never be activated as an automatic fallback.

## Compatibility and migration

**Existing scripts keep working while the source model replaces boolean modes.**

| Existing form | Transition |
|---|---|
| No source option | Resolves to `--source cloud` |
| `--archive` | Deprecated alias for the configured archive source |
| `runs cache ...` | Compatibility command family over the default local source |
| `runs usage --from-cache` | Deprecated alias for `--source local` |
| Existing `datasets`/`examples` without `--source` | Continue to resolve to cloud |
| Existing JSONL cache files | Not auto-discovered; explicit import converts them to Parquet |
| `LANGSMITH_ARCHIVE_URI/CONFIG` | Used to synthesize the default archive source |

Conflicting selectors fail. The CLI never changes an existing command from cloud to
local merely because a cache happens to exist.

## Red-team review

**The dangerous outcome is not a crash; it is a plausible but wrong answer.** The
default policy is therefore fail-fast for corrupt identity, schema, or query
semantics, and warn-or-fail by explicit policy for insufficient coverage.

| Failure scenario | Incorrect conclusion it could create | Required guardrail |
|---|---|---|
| Stale local data returns zero rows | “No failures occurred” | Return requested-versus-known coverage and freshness; strict mode fails |
| Local data was exported with `status=error` | “All runs in the interval were errors” | Record the materialization predicate; never claim unfiltered coverage |
| Two project names sanitize to one filename | Runs silently mix or overwrite | Stable project IDs in cache metadata; ambiguous import fails |
| A project is renamed | Old history appears to belong to a different project | Resolve names to stored project IDs and retain aliases as labels only |
| A run changes after cache/archive materialization | Copies look identical because IDs match | Report origin observation time; comparison uses canonical row digests |
| An imported JSONL row has a malformed shape | Import silently drops or coerces fields | Explicit import schema and canonical `Run` validation; import fails |
| A cache fragment is missing or corrupt | Partial results look complete | Reject the catalog revision and require repair/re-pull; never best-effort scan |
| A trace pull is interrupted | Half of the new traces appear | Publish immutable fragments first and atomically advance the catalog only after validation |
| Monday and Friday traces are cached | The cache appears to cover the whole week | Compute interval coverage from successful pull batches, never min/max cached timestamps |
| Dataset changes while examples are paginated | Replica mixes two versions | Resolve/freeze one `as_of` before the first page and use it for every page |
| One examples page is skipped or repeated | A partial/duplicated version looks valid | Verify unique Example IDs, complete pagination, expected counts, and a canonical version digest |
| A dataset example has no `source_run_id` | Example is treated as incomplete | Null is valid lineage; dataset replication succeeds without trace materialization |
| A linked cloud trace has expired | Valid dataset is rejected or fake trace content is invented | Dataset phase succeeds; optional trace phase reports the missing linked run separately |
| A signed attachment URL is archived | Replica appears valid until the URL expires | Fetch bytes, validate digest/media metadata, and store a content-addressed blob |
| Attachment name contains traversal syntax | Pull writes outside the replica root | Treat name as metadata; address blobs only by validated digest |
| Local code approximates a cloud transformation | Replica examples silently differ | Local/archive replicas are read-only; cloud remains mutation authority |
| A newer version deletes an example | Destination union resurrects deleted data | Publish the exact new version; preserve deletion in latest and older `as_of` snapshot separately |
| Same dataset name exists in two workspaces | Pull advances the wrong replica | Identity is source namespace plus Dataset ID; ambiguous names fail |
| A version tag moves later | Previously copied evidence changes meaning | Resolve tag to exact `as_of` at pull time and record both tag and resolution |
| One transferred version moves a tag already present at the destination | The tag becomes ambiguous | Synchronize the source tag map and enforce at most one owner per tag inside the head CAS |
| Equivalent instants use different timezone offsets | One version is published twice and makes the head unreadable | Canonicalize every identity timestamp to aware UTC before comparison or keying |
| Dataset metadata changes without a new DatasetVersion | A routine refresh is rejected as divergent | Keep the Dataset envelope as mutable head state; version digests cover stable Dataset ID plus Examples/attachments |
| A manifest redirects one version to another valid Parquet object | Historical reads return plausible data from the wrong version | Authenticate exact manifest bytes from the CAS head and recompute canonical content on read |
| A large Dataset is pulled | Multiple full Python copies exhaust the workstation before DuckDB batching | Stream SDK pages into bounded Pydantic batches and disk-backed DuckDB staging; stream attachments in fixed chunks |
| SDK adds or changes a required field | Replica silently drops cloud semantics | Fail schema compatibility until explicit migration and round-trip fixture exist |
| Backend compilers order ties differently | `--limit` returns different runs | Canonical ordering includes a stable run-ID tie-breaker |
| Example filtering is pushed down differently | Cloud and DuckDB disagree on examples/splits | Shared typed semantics and cross-backend golden fixtures |
| A local path references outside the cache | Metadata points DuckDB at unintended workstation files | Canonicalize paths and enforce a fixed root after symlink resolution |
| DuckDB installs or auto-loads an unapproved extension | A “local” query reaches the network | Preload only bundled allowlisted format support; disable external access and unapproved extension loading |
| User text reaches generated SQL | SQL injection or unexpected file reads | Bind values; compile identifiers and operators from typed allowlists |
| A huge local scan spills without bounds | Workstation exhaustion | Configured memory, thread, scan-size, and unique spill-directory limits |
| Cloud is unavailable | CLI silently serves stale retained data | Never fall back automatically; suggest an explicit source instead |
| Cloud and archive are unioned before limiting | Recent or duplicate rows displace valid results | No federation in this proposal; comparison operates on full bounded sets |
| A local subset is published as a retention window | Durable history acquires invisible holes | Forbid local-to-canonical-archive trace sync |

### Correctness decisions produced by the red team

- Canonical trace identity is scoped by provider/workspace, project ID, and root
  `trace_id`; run ID identifies a row within that bundle. A display name or
  filesystem path is never identity.
- Published Parquet fragments are read-only. Pull and compaction atomically publish
  a new catalog revision; concurrent readers finish on their prior revision before
  unreferenced files are deleted.
- Coverage is computed from pull-batch evidence, not from rows encountered by the
  query. Filtered batches prove only their predicate subset; disjoint intervals are
  not collapsed into one bounding interval. Exact-ID requests use
  `IdentityComplete` only when every requested bundle validates.
- Relative time expressions are resolved to absolute UTC bounds before dispatch.
  Those bounds, rather than the original `--last` expression, are recorded during
  materialization.
- Invalid identity, manifest, digest, or schema fails the query. Missing coverage
  can return partial rows only with visible evidence; automation may require full
  coverage as a policy.
- Comparison uses stable identities and canonical digests. A same-ID/different-row
  result is reported as divergence, not deduplicated or assigned an implicit winner.
- Dataset replication freezes one exact LangSmith Example/attachment version and
  round-trips strict Pydantic Dataset/Example contracts. The Dataset envelope and
  unique version tags remain mutable catalog state, matching LangSmith rather than
  inventing historical metadata semantics.
- Cloud is the dataset mutation authority initially. Archive/local fast-forward
  exact versions from the same lineage and reject divergence.
- `source_run_id` is optional lineage. Dataset completeness and optional linked-trace
  materialization have separate result/status contracts.

## Security and resource boundaries

**Source selection must not cause implicit data movement or credential use.**

- Local queries never upload trace content or contact LangSmith/S3.
- Archive queries require read/list access only; archive publication remains a
  separate write-capable operation.
- Cloud credentials and AWS libraries remain lazily loaded by the selected backend.
- Local cache roots reject traversal and symlink escapes outside the configured root.
- Local DuckDB connections preload only required bundled format support, disable
  external access and unapproved extension loading, and may scan only
  cache-metadata-approved canonical paths.
- Local cache metadata and Parquet default to user-only permissions; encryption at
  rest is the workstation owner's responsibility and must be documented.
- Dataset attachment blobs are addressed only by verified digests, never executed,
  and never written using user-controlled attachment names as paths.
- DuckDB connections retain bounded memory and unique spill directories.

On POSIX systems the cache manager creates the root with mode `0700` and metadata,
Parquet, attachment blobs, and spill files with mode `0600`; on Windows it inherits the current
user's profile ACL and never broadens it. There is no built-in encryption or secure
erase in the initial design. Environments that require either must use an encrypted
volume/profile or disable the local cache; `evict` is logical deletion, not a
forensic-erasure guarantee.

## Delivery plan

**Land the abstraction before adding behavior, then add local reads without
federation.**

| Phase | Outcome | Hard gate |
|---|---|---|
| 1. Query boundary | Typed `TraceQuery`, backend protocol, source registry | Cloud/archive parity tests pass unchanged |
| 2. Source UX | `--source`, lifecycle commands, evidence modes, compatibility aliases | Sparse JSON stays stable; envelope and strict-coverage contracts are tested |
| 3. Local reader | Parquet-only accumulating inventory through DuckDB plus explicit JSONL import | Identity and coverage are explicit; corrupt catalogs are rejected |
| 4. Predicate parity | Metadata and latency compile to FQL and SQL | Same fixtures produce equivalent result IDs/order |
| 5. Local materialization | Atomic pull/add/remove/compaction over immutable Parquet fragments | Additions preserve unrelated traces; provenance and batch coverage survive restart |
| 6. Dataset replicas | Source-aware Dataset/Example reads and exact-version pull to archive/local | SDK-model, version, split, schema, transformation, and attachment round trips pass |
| 7. Comparison | Source-to-source parity report | No merged result semantics |
| Deferred: federation | Explicit composite source | Precedence and coverage model approved separately |

## Definition of done

- [ ] `runs list/search/get/get-latest` accept one named source and return the same
      sparse output shape for equivalent fixtures.
- [ ] Cloud, archive, and local backends validate rows into the same LangSmith `Run`
      contract before the view layer.
- [ ] A local empty result distinguishes no matching rows from missing/unknown
      coverage.
- [ ] Sparse `--json` remains unchanged; `--json-envelope` exposes source,
      coverage, and diagnostics; `--require-complete` rejects insufficient coverage.
- [ ] Local queries scan only Parquet cache fragments; JSONL is accepted only by an
      explicit import that validates before conversion.
- [ ] Local pull adds/upserts complete traces without removing unrelated inventory;
      repeated pulls are idempotent.
- [ ] Pull, remove, and compaction atomically publish catalog revisions over
      immutable canonical Parquet; interrupted writes remain invisible.
- [ ] There is no automatic size/TTL eviction; explicit trace removal affects no
      dataset object or example.
- [ ] `datasets` and `examples` accept `--source` and return strict LangSmith SDK
      contracts with equivalent version, split, filter, and attachment semantics.
- [ ] Dataset replicas preserve all Dataset/Example fields, schemas,
      transformations, metadata, splits, version tags, and attachment bytes.
- [ ] Archive/local dataset replicas are read-only; later cloud versions
      fast-forward atomically while older `as_of` versions remain addressable.
- [ ] Same-name/different-ID datasets remain distinct and divergent lineages fail
      without unioning or overwriting examples.
- [ ] `--include-source-traces` writes linked full traces only to the destination
      trace store and reports its status separately from dataset replication.
- [ ] Local cache files use the versioned platform layout and user-only permissions;
      `evict` leaves no active catalog and makes no secure-erasure claim.
- [ ] Unsupported predicates identify the selected source and fail explicitly.
- [ ] No local query contacts LangSmith or S3 in an end-to-end network-denial test.
- [ ] `--archive`, cache commands, and `--from-cache` remain compatible through the
      documented deprecation window.
- [ ] Multi-source union remains unavailable until its separate correctness gates
      are met.

## Alternatives rejected

| Alternative | Why it is rejected |
|---|---|
| Add `--local` beside `--archive` | Produces another command branch and no extensible source identity |
| Treat every local file as an archive | Conflates partial working sets with sealed verified history |
| Make DuckDB the user-visible source | DuckDB is an executor; it says nothing about freshness or coverage |
| Auto-select local when available | A stale or filtered cache would silently replace live truth |
| Query every source by default | Deduplication and precedence become invisible product policy |
| Use JSON schema inference as the contract | Adjacent files can infer incompatible nested types |
| Keep JSONL as the canonical query format | Preserves inspectability but repeatedly pays parsing and loses Parquet pruning |
| Use a persistent `.duckdb` file as the initial cache | Creates a separate mutable storage/concurrency path from archive Parquet before benchmarks justify it |
| Rewrite or byte-append published cache files in place | Exposes mixed revisions to concurrent readers; use new immutable fragments plus atomic catalog publication |
| Generic copy command for every direction | Hides the stronger verification required by archive publication |
| Custom trace-collection model beside Dataset | Duplicates identity, membership, version, metadata, and transfer semantics that LangSmith already owns |
| Writable local/archive datasets initially | Requires reimplementing cloud schema validation, transformations, splits, attachment operations, and version creation |
| Replicate only latest examples | Breaks `as_of`, version tags, deletions, and reproducibility |
| Omit attachments or retain signed URLs | Produces a replica that is neither semantically complete nor durable |

# Appendix A: core invariants

| ID | Invariant | Enforcement boundary |
|---|---|---|
| S1 | One query selects exactly one source | CLI/source registry |
| S2 | One source name resolves to one typed backend configuration | Configuration validation |
| S3 | Every returned row satisfies the canonical `Run` contract | Backend normalization boundary |
| S4 | Project identity never depends on a sanitized filename | Local cache metadata/import validation |
| S5 | Coverage claims are evidence-backed and never inferred from row presence | Catalog/result model |
| S6 | Filter, time, ordering, and limit semantics are backend-independent | Typed query plus compiler parity tests |
| S7 | Local reads cause no network traffic | Local backend dependency boundary |
| S8 | Canonical archive retention publication never consumes the disposable local cache | Transfer policy |
| S9 | Multi-source overlap is never silently resolved | Single-source query gate |
| S10 | Dataset/Example contracts come from the pinned LangSmith SDK, not a custom reduced model | Model/storage boundary |
| S11 | A dataset replica contains one exact source lineage/version and never unions divergent heads | Dataset catalog/publication |
| S12 | `source_run_id` is optional lineage and never substitutes for copied trace content | Dataset/trace transfer boundary |
| S13 | Pull/add preserves unrelated local traces; compaction preserves the logical trace inventory | Cache manager parity checks |
| S14 | Attachment bytes and all historical dataset versions remain addressable for every published archive version | Dataset manifest validation |
| S15 | `(Dataset ID, canonical UTC as_of)` identifies exactly one canonical Example/attachment snapshot; Dataset metadata remains mutable catalog state | Content digest comparison before idempotent return plus mutable Pydantic Dataset head payload |
| S16 | A published head references only complete immutable objects from one authenticated manifest, including under concurrent writers | Content-addressed objects, unique manifests, manifest SHA-256, and head CAS |
| S17 | Concurrent writers of independent versions merge by rereading the winning head; they never discard an already-published version | Bounded head-CAS retry loop |
| S18 | Every replicated Example has the same `dataset_id` as its Dataset and example IDs are unique within a snapshot | Pre-publication cross-reference validation |
| S19 | SDK contract additions/removals fail before read or publication rather than silently dropping fields | Runtime model-field-set assertion plus contract test |
| S20 | The complete Dataset envelope can change without changing stable Dataset identity or rewriting immutable version content | Mutable strict Pydantic head payload plus ID-based storage keys |
| S21 | Catalog JSON, authenticated manifest cross-references, row counts, Dataset/Example IDs, canonical content, and object digests are validated before SDK reconstruction | Replica schema-v2 and integrity boundary |

# Appendix B: resolved follow-up decisions

**No blocking design questions remain for phases 1–7.** The follow-up passes
resolved the former open items as follows.

| Former question | Resolution |
|---|---|
| Source-management names | `runs source list/status/pull/import/remove/evict/compare`; existing cache commands are aliases |
| Local root and metadata | Platform user-cache `traces/v1/local`, immutable fragments, typed Parquet catalogs, and atomic `active.json` revision |
| JSON coverage envelope | Sparse `--json` stays stable; `--json-envelope` returns `runs/source/coverage/diagnostics` |
| Coverage enforcement | `--require-complete` succeeds only when authoritative/sealed evidence, compatible pull batches, or `IdentityComplete` evidence covers the exact trace request |
| Trace pull scope | IDs or a bounded query add/upsert selected complete trace bundles and preserve unrelated inventory |
| Capacity and eviction | No maximum, TTL, or LRU initially; users explicitly remove traces or evict the whole disposable cache |
| Coverage | Trace presence is separate from interval coverage; only compatible successful pull batches prove time-window completeness |
| Dataset terminology | Use LangSmith Dataset/Example everywhere; no parallel CLI or storage-level collection domain |
| Dataset schema authority | Pinned strict LangSmith SDK Dataset, Example, and DatasetVersion models plus explicit migrations |
| Dataset mutation | Cloud only initially; archive/local are read-only exact-version replicas |
| Dataset append | Mutate in cloud, then fast-forward the newer exact version into archive/local; never union example rows |
| Dataset identity | Source namespace plus Dataset ID; preserve Dataset/Example IDs in replicas and treat names as labels |
| Dataset completeness | Preserve schemas, transformations, metadata, splits, version tags, deletions, and attachment bytes |
| Dataset history scope | Default pull freezes `latest`; `--as-of` copies one version and `--all-versions` copies complete available history |
| Dataset/trace relationship | `source_run_id` remains optional lineage; linked full traces transfer only through a separate explicit phase |
| JSONL import identity | One project per import; unanimous `session_id` or required matching `--project-id`; filename ignored |
| Local encryption | No built-in crypto or secure erase; user-only permissions plus OS/encrypted-volume controls |
| Parquet layout | Sort by `session_id,start_time,id`; Zstd level 3; 122,880-row groups; ~512 MiB files; no Hive partitioning |
| Local physical retention | Current logical inventory persists until explicit removal/eviction; superseded physical observations disappear during compaction |

# Appendix C: explicitly deferred proposals

| Proposal | Why it is not open in this design | Re-entry gate |
|---|---|---|
| Multi-source union and precedence | The approved product contract returns rows from exactly one source; comparison covers near-term audit value | Separate federation design covering membership, precedence, deduplication, ordering, count, and combined coverage |
| Native `.duckdb` cache storage | Parquet is the selected cache format and reuses the archive schema/compiler | Representative benchmark shows a material end-to-end win and a safe multi-process locking/rebuild model |
| Multiple named local trace caches | Datasets organize evaluation examples, not trace-cache capacity; initial `local` remains one accumulating inventory | A workflow needs different security/capacity/eviction policies and cannot use explicit trace removal |
| Writable local dataset forks | Initial replicas intentionally delegate all mutation semantics to LangSmith | One SDK-backed mutation engine demonstrates schema/transformation/split/attachment/version parity |
| Dataset replica upload to cloud | Creating a new cloud lineage involves ID remapping and cloud validation | Separate import design defines lineage, conflicts, transformations, and attachment behavior |

# Appendix D: review record

| Pass | Lens | Change made |
|---|---|---|
| 1 | Core architecture | Separated source, backend, catalog, fragment, store, and executor; introduced one typed query boundary |
| 2 | Product | Defined three user-visible truth questions, explicit source choice, and capability-versus-coverage behavior |
| 3 | Usage/workflows | Tested current incidents, old history, retention, offline work, reproduction, and publication audit end to end |
| 4 | Red team | Added failure cases and guardrails for identity, staleness, filtered data, atomicity, schema drift, determinism, and network isolation |
| 5 | Final decision | Narrowed initial parity to core trace reads, clarified non-goals, and retained comparison while deferring merged results |
| 6 | Local cache follow-up | Selected Parquet as the only cache format and removed durable local revision semantics |
| 7 | Open-question pass 1 | Fixed lifecycle commands, the original bounded replacement semantics, JSON evidence, coverage policy, and import identity |
| 8 | Open-question pass 2 | Fixed filesystem, permissions, encryption stance, Parquet sizing, and explicit re-entry gates for deferred proposals |
| 9 | Accumulating-cache correction | Replaced global snapshot semantics with set-union/upsert, immutable deltas, explicit removal, and a batch coverage ledger |
| 10 | Collection/product pass | Explored a custom trace-membership abstraction and cross-source download/append workflows |
| 11 | Collection red-team pass | Identified revision, identity, partial-transfer, and overwrite hazards in that custom abstraction |
| 12 | Dataset-model correction | Removed the custom abstraction; adopted strict Dataset/Example/version semantics for cloud, archive, and local |
| 13 | Dataset parity pass | Made non-cloud replicas read-only, separated linked traces, preserved attachments/splits/transformations, and required fast-forward publication |
