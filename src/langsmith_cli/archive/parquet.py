"""Canonical Parquet row/query semantics shared by archive and local traces."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import TYPE_CHECKING, Any

from langsmith_cli.time_parsing import ensure_aware_datetime
from langsmith_cli.trace_query import RunQuery

if TYPE_CHECKING:
    from langsmith.schemas import Run


# Canonical Parquet keeps arbitrary payloads as JSON text and promotes only
# shape-stable, queryable dimensions to native lists/maps. This explicit taxonomy
# prevents provider keys from becoming inferred STRUCT children, and one contract
# keeps every fragment provider- and source-compatible.
ARCHIVE_JSON_OBJECT_COLUMNS = (
    "extra",
    "inputs",
    "outputs",
    "feedback_stats",
)
ARCHIVE_JSON_LIST_COLUMNS = ("events",)
ARCHIVE_JSON_COLUMNS = ARCHIVE_JSON_OBJECT_COLUMNS + ARCHIVE_JSON_LIST_COLUMNS
ARCHIVE_STRING_LIST_COLUMNS = ("tags", "parent_run_ids")
BULK_EXPORT_JSON_COLUMNS = ARCHIVE_JSON_COLUMNS + ARCHIVE_STRING_LIST_COLUMNS
ARCHIVE_INTEGER_MAP_COLUMNS = (
    "prompt_token_details",
    "completion_token_details",
)
ARCHIVE_DECIMAL_MAP_COLUMNS = (
    "prompt_cost_details",
    "completion_cost_details",
)
ARCHIVE_TYPED_MAP_COLUMNS = ARCHIVE_INTEGER_MAP_COLUMNS + ARCHIVE_DECIMAL_MAP_COLUMNS
ARCHIVE_METADATA_COLUMN = "metadata"
ARCHIVE_DIMENSION_COLUMNS = (
    *ARCHIVE_STRING_LIST_COLUMNS,
    *ARCHIVE_TYPED_MAP_COLUMNS,
    ARCHIVE_METADATA_COLUMN,
)

# The single SQL spelling of each promoted dimension's physical type. Every
# writer and reader path that projects onto the canonical contract builds its
# CAST/NULL expressions from this map — adding a dimension means adding it to
# the column tuples above and here, nowhere else.
ARCHIVE_DIMENSION_SQL_TYPES: dict[str, str] = {
    **dict.fromkeys(ARCHIVE_STRING_LIST_COLUMNS, "VARCHAR[]"),
    **dict.fromkeys(ARCHIVE_INTEGER_MAP_COLUMNS, "MAP(VARCHAR, BIGINT)"),
    **dict.fromkeys(ARCHIVE_DECIMAL_MAP_COLUMNS, "MAP(VARCHAR, DECIMAL(38,18))"),
    ARCHIVE_METADATA_COLUMN: "MAP(VARCHAR, VARCHAR)",
}


def json_dimension_projection(
    columns: set[str], *, metadata_column_is_canonical: bool
) -> tuple[list[str], list[str]]:
    """SQL that projects JSON-text/absent dimension columns onto canonical types.

    One projection serves every path that meets v1-era or provider JSON text:
    Bulk Export snapshots, legacy SQL canonicalization, and the query-time
    normalized view. Returns ``(excluded_columns, expressions)`` for a
    ``SELECT * EXCLUDE (...), <expressions> FROM ...``.

    ``metadata_column_is_canonical`` controls an existing ``metadata`` column:
    the SQL canonicalizer may re-read its own typed output (pass ``True`` to
    cast it through), while provider sources never carry a canonical
    ``metadata`` column (pass ``False`` to re-derive from ``extra``, keeping
    the accelerator consistent with its authoritative source).
    """
    excluded = [column for column in ARCHIVE_DIMENSION_COLUMNS if column in columns]
    expressions = [
        f"CAST(CAST({column} AS JSON) AS {ARCHIVE_DIMENSION_SQL_TYPES[column]}) "
        f"AS {column}"
        if column in columns
        else f"NULL::{ARCHIVE_DIMENSION_SQL_TYPES[column]} AS {column}"
        for column in (*ARCHIVE_STRING_LIST_COLUMNS, *ARCHIVE_TYPED_MAP_COLUMNS)
    ]
    metadata_type = ARCHIVE_DIMENSION_SQL_TYPES[ARCHIVE_METADATA_COLUMN]
    if metadata_column_is_canonical and ARCHIVE_METADATA_COLUMN in columns:
        expressions.append(
            f"CAST({ARCHIVE_METADATA_COLUMN} AS {metadata_type}) "
            f"AS {ARCHIVE_METADATA_COLUMN}"
        )
    elif "extra" in columns:
        expressions.append(
            "coalesce(CAST(json_extract(CAST(extra AS JSON), '$.metadata') "
            f"AS {metadata_type}), map()::{metadata_type}) "
            f"AS {ARCHIVE_METADATA_COLUMN}"
        )
    else:
        expressions.append(f"map()::{metadata_type} AS {ARCHIVE_METADATA_COLUMN}")
    return excluded, expressions


def normalize_event_time(value: object) -> object:
    """Return one event time as zoned UTC, preserving unparseable values."""
    event_time = value
    if isinstance(event_time, str):
        try:
            event_time = datetime.fromisoformat(event_time.replace("Z", "+00:00"))
        except ValueError:
            return value
    if not isinstance(event_time, datetime):
        return value
    if event_time.tzinfo is None:
        event_time = event_time.replace(tzinfo=timezone.utc)
    return event_time.astimezone(timezone.utc).isoformat()


def normalize_run_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize provider-specific Parquet values to the LangSmith Run contract."""
    # ``metadata`` is a physical query accelerator extracted from ``extra``; the
    # strict SDK Run contract continues to receive the authoritative full object.
    payload.pop(ARCHIVE_METADATA_COLUMN, None)
    # Managed Bulk Export omits attachments. UNION BY NAME materializes that
    # absence as NULL, while the SDK contract expects the field omitted/defaulted.
    if "attachments" in payload and payload["attachments"] is None:
        payload.pop("attachments")
    for field in (
        "id",
        "trace_id",
        "parent_run_id",
        "session_id",
        "reference_example_id",
    ):
        if field not in payload:
            continue
        value = payload[field]
        if isinstance(value, str) and value.startswith('"'):
            decoded = json.loads(value)
            if not isinstance(decoded, str):
                raise ValueError(f"Archived UUID field is not a string: {field}")
            payload[field] = decoded
    expected_json_types = {
        **dict.fromkeys(ARCHIVE_JSON_OBJECT_COLUMNS, dict),
        **dict.fromkeys(ARCHIVE_JSON_LIST_COLUMNS, list),
        **dict.fromkeys(ARCHIVE_STRING_LIST_COLUMNS, list),
    }
    for field, expected_type in expected_json_types.items():
        value = payload[field] if field in payload else None
        if not isinstance(value, str):
            continue
        decoded = json.loads(value)
        if decoded is not None and not isinstance(decoded, expected_type):
            raise ValueError(f"Archived JSON field has an invalid type: {field}")
        payload[field] = decoded
    inputs = payload.get("inputs")
    if isinstance(inputs, dict) and isinstance(inputs.get("input"), str):
        try:
            inputs["input"] = json.loads(inputs["input"])
        except json.JSONDecodeError:
            pass
    events = payload.get("events")
    if isinstance(events, list):
        for event in events:
            if not isinstance(event, dict) or "time" not in event:
                continue
            event["time"] = normalize_event_time(event["time"])
    for details_field in ARCHIVE_TYPED_MAP_COLUMNS:
        if details_field not in payload:
            continue
        details = payload[details_field]
        if isinstance(details, dict):
            payload[details_field] = {
                key: value for key, value in details.items() if value is not None
            }
    return payload


def validated_parquet_run(payload: dict[str, Any]) -> Run:
    """Validate one canonical Parquet row as a strict LangSmith SDK Run."""
    from langsmith.schemas import Run

    run = Run.model_validate(normalize_run_payload(payload))
    normalized_events: list[dict[str, Any]] = []
    for event in run.events or []:
        normalized_event = dict(event)
        if "time" in normalized_event:
            normalized_event["time"] = normalize_event_time(normalized_event["time"])
        normalized_events.append(normalized_event)
    return run.model_copy(update={"events": normalized_events or run.events})


def parquet_where_clause(query: RunQuery) -> tuple[str, list[object]]:
    """Compile the typed predicate subset to bound DuckDB SQL values."""
    clauses: list[str] = []
    parameters: list[object] = []
    since = ensure_aware_datetime(query.since)
    before = ensure_aware_datetime(query.before)
    if since is not None:
        clauses.append("start_time >= ?")
        parameters.append(since)
    if before is not None:
        clauses.append("start_time < ?")
        parameters.append(before)
    if query.error is not None:
        clauses.append("error IS NOT NULL" if query.error else "error IS NULL")
    if query.run_id is not None:
        clauses.append("CAST(id AS VARCHAR) = ?")
        parameters.append(query.run_id)
    if query.trace_id is not None:
        clauses.append("CAST(trace_id AS VARCHAR) = ?")
        parameters.append(query.trace_id)
    if query.trace_ids:
        clauses.append("CAST(trace_id AS VARCHAR) = ANY(?)")
        parameters.append(list(query.trace_ids))
    if query.run_type is not None:
        clauses.append("run_type = ?")
        parameters.append(query.run_type)
    if query.is_root is not None:
        clauses.append(
            "parent_run_id IS NULL" if query.is_root else "parent_run_id IS NOT NULL"
        )
    for tag in query.tags:
        # Every reader path materializes ``tags`` as a native VARCHAR[] (typed v2
        # fragments directly; v1 archive generations through the normalized view),
        # so list predicates push down into Parquet instead of re-parsing JSON.
        clauses.append("list_contains(tags, ?)")
        parameters.append(tag)
    if query.text is not None:
        # Field identifiers come only from RunQuery's explicit allowlist. Every user
        # value remains bound, including regex patterns and wildcard characters.
        text_sql = " || ' ' || ".join(
            f"coalesce(CAST({field} AS VARCHAR), '')" for field in query.text_fields
        )
        if query.text_regex:
            clauses.append(f"regexp_matches({text_sql}, ?)")
            pattern = f"(?i){query.text}" if query.text_ignore_case else query.text
            parameters.append(pattern)
        elif query.text_ignore_case:
            clauses.append(f"lower({text_sql}) LIKE ?")
            parameters.append(f"%{query.text.lower()}%")
        else:
            clauses.append(f"{text_sql} LIKE ?")
            parameters.append(f"%{query.text}%")
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    return where, parameters
