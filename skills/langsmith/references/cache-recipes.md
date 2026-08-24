# Local Trace Cache Recipes

The local trace cache is an intermediate Parquet working set managed by the
CLI. Use the normal `runs` facade with `--source local`; do not depend on the
cache's fragment paths or catalog layout.

## 1. Materialize Explicitly

Reads never populate the cache. Pull exactly when you want a local working set:

```bash
# From live LangSmith
langsmith-cli --json runs pull --source cloud --to local \
  --project dev/namedrop_service --last 7d

# From retained S3 archive
langsmith-cli --json runs pull --source archive --to local \
  --project dev/namedrop_service --last 90d
```

Pulls are additive and content-idempotent. Existing cached traces remain
reachable, identical content is not republished, and newer observations of a
run win when querying.

## 2. Query Through the Common Runs Facade

```bash
# List recent local runs
langsmith-cli --json runs list --source local \
  --project dev/namedrop_service --last 7d \
  --fields id,name,status,start_time

# Search full local inputs, outputs, and errors
langsmith-cli --json runs search "Ivana Stradner" --source local \
  --project dev/namedrop_service --fields id,name,outputs

# Read one locally cached run and its trace children
langsmith-cli --json runs get <run-id> --source local \
  --project dev/namedrop_service --follow-children

# Get the newest local failure
langsmith-cli --json runs get-latest --source local \
  --project dev/namedrop_service --failed --fields id,name,error
```

Cloud-only FQL is rejected for local queries rather than silently approximated.
Use the shared structured options (`--project`, time bounds, status, run type,
tags, roots, and text search) that the local DuckDB backend supports.

## 3. Inspect and Discover

```bash
# Which projects are locally reachable?
langsmith-cli --json runs cache list \
  --fields project_name,run_count,fragment_count,origins

# Discover nested input/output shape
langsmith-cli --json runs cache schema \
  --project dev/namedrop_service --include inputs --include outputs --sample 500

# Compatibility grep; new workflows may prefer `runs search --source local`
langsmith-cli --json runs cache grep "pattern" \
  --project dev/namedrop_service --grep-in outputs --fields id,name,outputs

# Cache root (for diagnostics, not direct querying)
langsmith-cli runs cache dir
```

The root contains an atomically published catalog and immutable Parquet
fragments. Fragment paths are an implementation detail; querying them directly
can miss reachable fragments or expose superseded observations.

## 4. Evict Local Reachability

```bash
# One project
langsmith-cli --json runs cache clear --project dev/namedrop_service --yes

# Everything local
langsmith-cli --json runs cache clear --yes
```

Eviction updates the logical catalog atomically. The cache is intentionally not
a durable store: reconstruct it from cloud or archive when needed.

## 5. Choose the Right Backend

| Need | Source |
|---|---|
| Current authoritative operational data | `--source cloud` |
| Durable retained history or recovery | `--source archive` |
| Fast/offline repeated intermediate analysis | `--source local` |

Do not treat local cache presence as a claim of completeness. A pull records
selected content; later cloud/archive changes are visible only after another
explicit pull.
