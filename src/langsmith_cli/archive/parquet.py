"""Canonical Parquet row/query semantics shared by archive and local traces."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import TYPE_CHECKING, Any

from langsmith_cli.time_parsing import ensure_aware_datetime
from langsmith_cli.trace_query import RunQuery

if TYPE_CHECKING:
    from langsmith.schemas import Run


# Canonical Parquet stores nested provider fields as JSON text. Runs API JSONL is
# inferred as STRUCT/LIST while Bulk Export v2 emits VARCHAR for the same fields;
# this one contract keeps every fragment provider- and source-compatible.
ARCHIVE_JSON_OBJECT_COLUMNS = (
    "extra",
    "inputs",
    "outputs",
    "feedback_stats",
)
ARCHIVE_JSON_LIST_COLUMNS = (
    "events",
    "tags",
    "parent_run_ids",
)
ARCHIVE_JSON_COLUMNS = ARCHIVE_JSON_OBJECT_COLUMNS + ARCHIVE_JSON_LIST_COLUMNS


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
    for details_field in (
        "prompt_token_details",
        "completion_token_details",
        "prompt_cost_details",
        "completion_cost_details",
    ):
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
    if query.run_type is not None:
        clauses.append("run_type = ?")
        parameters.append(query.run_type)
    if query.is_root is not None:
        clauses.append(
            "parent_run_id IS NULL" if query.is_root else "parent_run_id IS NOT NULL"
        )
    for tag in query.tags:
        clauses.append("json_contains(CAST(tags AS JSON), to_json(?))")
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
