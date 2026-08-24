# Typed queryable archive dimensions: test plan

- [x] `test_canonical_schema_v2_uses_stable_queryable_types` — sync a real SDK
  `Run` through the Runs API path and prove the published Parquet uses string lists
  for tags/topology, typed maps for usage breakdowns, and an extracted metadata map
  while arbitrary payloads remain JSON text.
- [x] `test_mixed_v1_v2_archive_days_query_through_one_normalized_relation` — publish
  adjacent legacy/current days, then exercise list, tag filtering, and strict SDK
  validation across both canonical schemas without rewriting the legacy artifact.
- [x] `test_reconciliation_upgrades_an_unsealed_v1_manifest_to_v2` — resume a legacy
  project-day and prove the next canonical publication advertises the current schema.
- [x] Extend the Runs API/Bulk Export reconciliation integration test so provider
  boundaries preserve the same typed dimensions and metadata extraction.
- [x] Keep corrupt/unknown manifest-version coverage: versions 1 and 2 are readable;
  any other version fails at the typed storage boundary.

Post-rebase additions (after #170 landed and the taxonomy moved to
`archive/parquet.py`, shared with the local trace backend):

- [x] `test_empty_snapshot_does_not_route_the_day_through_the_sql_path` — an
  id-only empty phase must not veto the bounded streaming path for the day's
  real snapshot (SQL canonicalization is monkeypatched to fail the test).
- [x] `test_unsealed_v1_text_raw_migrates_to_v2_on_the_bounded_streaming_path` —
  feeds genuine v1-writer raw (text tags/topology, inferred detail columns,
  string-encoded Decimal costs, no metadata column) through reconciliation and
  proves a typed v2 canonical publication without the day-scaled SQL union.
  Both tests were verified to fail against the pre-fix dispatch guard.
- [x] Local fragments adopt the same typed schema through the shared
  `write_runs_parquet` path; `LOCAL_TRACE_SCHEMA_VERSION` bumped to 2 and a
  version-mismatched catalog reads as empty (local is a disposable replica),
  covered by the updated local trace model test.

External systems are represented by the existing strict `Run` models, fake Runs
client, fake Bulk Export provider, local archive store, real DuckDB, and real PyArrow
Parquet readers. No production function or schema model is mocked.
