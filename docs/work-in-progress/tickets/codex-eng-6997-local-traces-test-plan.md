# ENG-6997 local traces: test plan

This PR completes the second independently useful slice of the unified trace-source
design. Local cache population remains an explicit write operation; ordinary reads
never populate, refresh, or evict the cache.

## User journeys

- [x] `test_runs_pull_cloud_then_list_local_offline` — invoke the real CLI pull and
  list commands, fake only the LangSmith client, then deny network access during the
  local read.
- [x] `test_runs_pull_archive_then_get_local` — publish archive Parquet through the
  existing archive harness, explicitly pull it locally, and read the same strict Run
  through `runs get --source local`.
- [x] `test_runs_get_latest_uses_the_selected_source` — prove cloud, archive, and
  local selection route to the intended backend without fallback.
- [x] `test_legacy_cache_download_and_local_list_share_one_inventory` — populate via
  `runs cache download`, query via `runs list --source local`, and prove there is no
  second JSONL cache.
- [x] `test_usage_and_pricing_read_the_shared_local_inventory` — exercise the
  existing `--from-cache` workflows against the same Parquet inventory.

## Invariants

- [x] `test_local_addition_preserves_unrelated_traces` — a second pull is set-union
  addition, never replacement.
- [x] `test_repeated_pull_is_idempotent` — publishing identical observations does not
  create a second active fragment or duplicate logical runs.
- [x] `test_newer_observation_wins_without_duplicate_run_ids` — immutable fragments
  may overlap physically but the logical inventory returns one scoped run identity.
- [x] `test_interrupted_fragment_write_does_not_change_catalog` — failure before the
  compare-and-swap publication boundary leaves the previous inventory readable.
- [x] `test_concurrent_catalog_additions_merge` — two stale writers cannot erase each
  other's independently published fragments.
- [x] `test_catalog_rejects_paths_outside_the_cache_root` — DuckDB only scans
  catalog-approved canonical paths below the configured root.
- [x] `test_local_reads_do_not_create_or_mutate_cache_files` — no read-through cache,
  automatic refresh, TTL, LRU, or implicit eviction.
- [x] `test_source_and_archive_alias_conflict_fails_fast` — source selection is
  explicit and unambiguous.
- [x] `test_local_backend_rejects_unsupported_fql` — unsupported semantics fail
  instead of silently producing approximate results.

## Regression and quality gates

- [x] Cross-backend golden query fixtures return equivalent IDs and ordering for the
  supported predicate subset.
- [x] Existing archive, cache, runs list/get, usage, and pricing tests pass after
  consolidation.
- [x] No duplicate test function names exist in touched test modules.
- [x] CLI startup has no material regression: median import was 239.6 ms versus
  235.4 ms on `origin/main`, and DuckDB remains absent from startup imports.
- [x] Ruff, Pyright, targeted coverage (over 90% across the new local-trace slice),
  and the full suite pass (`1,360 passed` on a native POSIX temp root after
  rebasing onto current `main`).
- [x] A real temporary-directory CLI scenario records Parquet files, row counts,
  additive/idempotent behavior, offline reads, and wall-clock timings for the PR body.
