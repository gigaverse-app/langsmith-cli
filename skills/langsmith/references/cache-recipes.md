# Cache Recipes: Schema Discovery, Python & DuckDB Queries

## 1. Discover Schema Before Querying

Always discover the schema first — don't guess field names.

```bash
# What's cached?
langsmith-cli runs cache list

# Discover the full schema (sample 20 runs by default)
langsmith-cli --json runs cache schema --project dev/namedrop_service

# Focus on inputs/outputs only
langsmith-cli --json runs cache schema --project dev/namedrop_service --include inputs,outputs

# Increase sample size if fields are sparsely populated (e.g., empty lists)
langsmith-cli --json runs cache schema --project dev/namedrop_service --include outputs --sample-size 500
```

**Key insight:** Some fields (like `extracted_entities`) may be empty lists in most runs. Use `--sample-size 500` to find the few runs that populate them and reveal the full element schema.

## 2. Get the Cache Directory

All recipes below need the cache directory path:

```bash
CACHE_DIR=$(langsmith-cli runs cache dir)
# Files are named: ${CACHE_DIR}/<sanitized_project_name>.jsonl
# Example: ${CACHE_DIR}/dev_namedrop_service.jsonl
```

**Naming convention:** slashes become underscores, so `dev/namedrop_service` → `dev_namedrop_service.jsonl`.

## 3. Python One-Liners for JSONL Queries

### Extract specific nested fields
```bash
python3 -c "
import json
for line in open('$(langsmith-cli runs cache dir)/dev_namedrop_service.jsonl'):
    d = json.loads(line.strip())
    out = d.get('outputs', {}).get('output', {})
    entities = out.get('extracted_entities', [])
    for e in entities:
        print(json.dumps({'run_id': d['id'], 'name': e.get('canonical_full_name'), 'type': e.get('entity_type')}))
"
```

### Sort runs by output size
```bash
python3 -c "
import json
runs = []
seen = set()
for line in open('$(langsmith-cli runs cache dir)/dev_namedrop_service.jsonl'):
    d = json.loads(line.strip())
    rid = d.get('id')
    if rid in seen: continue
    seen.add(rid)
    out = d.get('outputs', {})
    size = len(json.dumps(out, default=str))
    runs.append((size, rid, d.get('name','?'), d.get('run_type','?')))
runs.sort(reverse=True)
for size, rid, name, rtype in runs[:20]:
    print(f'{size:>8,} | {rtype:>8} | {name[:40]:40} | {rid}')
"
```

### Find runs containing a specific entity/value
```bash
python3 -c "
import json
target = 'Ivana Stradner'  # change this
for line in open('$(langsmith-cli runs cache dir)/dev_namedrop_service.jsonl'):
    d = json.loads(line.strip())
    if target.lower() in json.dumps(d.get('outputs', {})).lower():
        print(d['id'], d.get('name','?'), str(d.get('start_time',''))[:16])
"
```

### Count entities per run
```bash
python3 -c "
import json
from collections import Counter
entity_counts = Counter()
for line in open('$(langsmith-cli runs cache dir)/dev_namedrop_service.jsonl'):
    d = json.loads(line.strip())
    out = d.get('outputs', {}).get('output', {})
    for key in ['extracted_entities', 'extracted_terminology']:
        for item in out.get(key, []):
            name = item.get('canonical_full_name', '?')
            entity_counts[name] += 1
for name, count in entity_counts.most_common(30):
    print(f'{count:>5} | {name}')
"
```

## 4. DuckDB for SQL Queries Over JSONL

DuckDB can query JSONL files directly with SQL — no data loading step needed.

### Setup
```bash
pip install duckdb  # or: uv pip install duckdb
```

### Basic query with nested field extraction
```python
import duckdb

cache_dir = "$(langsmith-cli runs cache dir)"
result = duckdb.sql(f"""
SELECT
    id,
    name,
    run_type,
    outputs->>'$.output.extracted_entities' as entities,
    start_time
FROM read_ndjson_auto('{cache_dir}/dev_namedrop_service.jsonl')
WHERE json_array_length(outputs->'$.output.extracted_entities') > 0
ORDER BY start_time DESC
LIMIT 20
""")
print(result.df().to_string())
```

### Aggregate by model
```python
import duckdb

cache_dir = "$(langsmith-cli runs cache dir)"
result = duckdb.sql(f"""
SELECT
    json_extract_string(extra, '$.metadata.ls_model_name') as model,
    COUNT(*) as runs,
    AVG(total_tokens) as avg_tokens,
    SUM(total_tokens) as total_tokens
FROM read_ndjson_auto('{cache_dir}/dev_namedrop_service.jsonl')
GROUP BY model
ORDER BY runs DESC
""")
print(result.df().to_string())
```

### Unnest list fields (e.g., extracted entities)
```python
import duckdb

cache_dir = "$(langsmith-cli runs cache dir)"
result = duckdb.sql(f"""
WITH runs AS (
    SELECT
        id as run_id,
        start_time,
        json_extract(outputs, '$.output.extracted_entities') as entities_json
    FROM read_ndjson_auto('{cache_dir}/dev_namedrop_service.jsonl')
    WHERE json_array_length(json_extract(outputs, '$.output.extracted_entities')) > 0
)
SELECT
    run_id,
    start_time,
    json_extract_string(entity.value, '$.canonical_full_name') as entity_name,
    json_extract_string(entity.value, '$.entity_type') as entity_type,
    json_extract_string(entity.value, '$.llm_recognition') as llm_recognized
FROM runs, json_each(runs.entities_json) as entity
ORDER BY start_time DESC
LIMIT 50
""")
print(result.df().to_string())
```

### Find runs by output size
```python
import duckdb

cache_dir = "$(langsmith-cli runs cache dir)"
result = duckdb.sql(f"""
SELECT
    id,
    name,
    run_type,
    length(outputs::VARCHAR) as output_size,
    start_time
FROM read_ndjson_auto('{cache_dir}/dev_namedrop_service.jsonl')
ORDER BY output_size DESC
LIMIT 20
""")
print(result.df().to_string())
```

## 5. Workflow: Schema → Query

1. **Discover schema:** `langsmith-cli --json runs cache schema --project <name> --include outputs --sample-size 500`
2. **Identify the field paths** from the schema tree (e.g., `outputs.output.extracted_entities[].canonical_full_name`)
3. **Query with Python or DuckDB** using those exact paths
4. **Deduplicate** — cache files may contain duplicate run IDs; always dedupe by `id`
