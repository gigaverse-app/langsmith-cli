# Unify LangSmith datasets and traces across cloud, archive, and local sources

## TL;DR

**Expose cloud, archive, and local through one typed CLI facade while adopting the LangSmith Dataset and Example models without a parallel collection abstraction.** Deliver this in at most two independently useful PRs: exact dataset-version replicas first, then the accumulating DuckDB trace inventory and optional linked-trace materialization.

## Plan

| # | Independently usable PR | Capability after merge | Hard gate |
|---:|---|---|---|
| 1 | Exact dataset replicas | `datasets`/`examples` can read cloud, archive, or local; `datasets pull` publishes strict, read-only Dataset/Example versions with schemas, transformations, splits, tags, deletions, and attachment bytes | SDK-model round trips, frozen-version pagination, atomic publication, and cloud/archive/local CLI journeys pass |
| 2 | Accumulating trace inventory | `runs` can query an additive DuckDB-over-Parquet local cache; explicit pulls preserve unrelated traces; dataset pulls can optionally materialize linked `source_run_id` traces | Coverage-ledger, idempotency, interruption, offline-network-denial, and cross-backend predicate tests pass |

The critical path is PR 1 → PR 2. Do not split storage, CLI, and tests into separate PRs; each PR must expose a complete user workflow.

## Why now — one model prevents permanent drift

LangSmith datasets already define the semantics needed for organizing evaluation data. Recreating a trace-specific collection would duplicate identities, examples, versions, splits, transformations, metadata, and attachment behavior.

| Concern | Decision | User-visible result |
|---|---|---|
| Dataset identity | Source namespace plus Dataset ID; names are labels | Same-name datasets never overwrite each other |
| Dataset semantics | Strict pinned LangSmith `Dataset`, `Example`, and `DatasetVersion` contracts | Cloud/archive/local return the same shapes |
| Mutation | Cloud is authoritative initially | LangSmith alone applies validation, transformations, splits, attachments, and version creation |
| Archive/local | Exact, read-only version replicas | `--as-of` remains reproducible and later versions fast-forward safely |
| Trace relationship | `source_run_id` remains optional lineage | Dataset transfer succeeds independently of trace retention |
| Local traces | Accumulating inventory with explicit removal | Pulling new work never replaces unrelated cached traces |

## Decisions and non-goals

| Decision | Rationale |
|---|---|
| Use the existing `datasets` and `examples` commands with `--source` | Avoid a second facade and preserve current cloud scripts |
| Store typed rows in Parquet and attachments as content-addressed blobs | DuckDB can query strict data while arbitrary media retains native bytes |
| Freeze one exact `as_of` before pagination | A replica must never mix two cloud versions |
| Publish archive/local heads atomically | Readers see either the previous complete version or the new complete version |
| Keep dataset replication separate from linked trace transfer | An example without a live source trace is still a valid example |
| Defer writable local dataset forks | Reimplementing cloud mutation semantics would recreate the drift risk |
| Defer multi-source query union | Precedence, overlap, ordering, and coverage require a separate correctness design |

## Definition of done

- [ ] `datasets list/get/status`, `examples list/get`, and `datasets versions` accept one named source and preserve sparse JSON output.
- [ ] `datasets pull` supports exact `latest`, `--as-of`, and `--all-versions` replication from cloud to archive/local and archive to local.
- [ ] Dataset replicas preserve strict SDK fields, schemas, transformations, metadata, splits, version tags, deletions, and attachment bytes.
- [ ] Archive/local replicas are read-only, same-lineage pulls are idempotent/fast-forward only, and divergent identities fail before publication.
- [ ] The local trace cache is additive, deduplicated, Parquet-only, and has no automatic TTL/LRU/size eviction.
- [ ] Optional linked-trace materialization writes only to the trace inventory and reports status separately from dataset replication.
- [ ] All new CLI arguments are documented in `skills/langsmith/SKILL.md`; human and sparse `--json` journeys are covered.
- [ ] Unit, integration, full-suite, startup-latency, formatting, and type checks pass with measured evidence in each PR.

## Background

The existing CLI already supports LangSmith datasets/examples in cloud and durable trace archives through local/S3 object stores. The design extends those primitives instead of replacing them: archive/local dataset versions use the same strict SDK contracts, while trace caching remains a separate additive source. The canonical design is `docs/TRACE_SOURCES_DESIGN.md` in the repository.
