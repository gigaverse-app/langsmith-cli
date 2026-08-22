# ENG-6997 dataset replicas — test plan

## User journey

- [x] Pull an exact cloud dataset version into a local replica and read the same
  dataset and examples without constructing a LangSmith client.
- [x] Pull the same version again and observe an idempotent result.
- [x] Replicate an archive snapshot into the local store.
- [x] List replica versions and select one with `--as-of`.

## Correctness invariants

- [x] One exact `DatasetVersion.as_of` is resolved before examples are fetched.
- [x] Publication exposes a new head only after both Parquet objects, the manifest,
  and all attachment blobs are present.
- [x] Dataset and example SDK fields round-trip through Parquet without changing
  IDs, timestamps, schemas, transformations, metadata, splits, or lineage.
- [x] Attachment bytes are content-addressed and reconstructed with their name and
  MIME type.
- [x] A dataset name cannot silently replace a different dataset ID in one store.
- [x] Local and archive reads never instantiate or call the cloud client.

## CLI and regression coverage

- [x] `datasets pull`, `datasets versions`, source-aware `datasets list/get`, and
  source-aware `examples list/get` cover both human and `--json` output.
- [x] Existing cloud command arguments and calls remain unchanged by default.
- [x] Invalid source/target combinations and unsupported offline filters fail with
  actionable errors.
- [x] `skills/langsmith/SKILL.md` documents every new argument and workflow.
- [x] Focused tests, full pytest, Ruff, Pyright, and startup-latency checks pass.
