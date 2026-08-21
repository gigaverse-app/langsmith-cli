"""DuckDB-backed queries over published archive manifests."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from fnmatch import fnmatchcase
import json
import re
from typing import TYPE_CHECKING, Any

from langsmith_cli.archive.config import load_archive_config
from langsmith_cli.archive.duckdb import configure_duckdb_s3
from langsmith_cli.archive.repository import (
    list_project_records,
    manifest_identity_from_key as _manifest_identity_from_key,
    read_manifests,
)
from langsmith_cli.archive.storage import create_store
from langsmith_cli.archive.sync import (
    ARCHIVE_JSON_LIST_COLUMNS,
    ARCHIVE_JSON_OBJECT_COLUMNS,
)
from langsmith_cli.time_parsing import ensure_aware_datetime

if TYPE_CHECKING:
    from langsmith.schemas import Run


# These values are interpolated as SQL identifiers, not bound values. Keeping the
# allowlist adjacent to the typed query contract makes that safety invariant visible.
ARCHIVE_TEXT_FIELDS = frozenset({"inputs", "outputs", "error", "extra"})


@dataclass(frozen=True)
class ArchiveRunQuery:
    project: str | None = None
    project_id: str | None = None
    project_name: str | None = None
    project_name_exact: str | None = None
    project_name_pattern: str | None = None
    project_name_regex: str | None = None
    since: datetime | None = None
    before: datetime | None = None
    limit: int | None = 20
    error: bool | None = None
    trace_id: str | None = None
    run_type: str | None = None
    is_root: bool | None = None
    tags: tuple[str, ...] = ()
    text: str | None = None
    text_fields: tuple[str, ...] = ("inputs", "outputs", "error")
    text_regex: bool = False
    text_ignore_case: bool = False

    def __post_init__(self) -> None:
        if self.text is not None and not self.text_fields:
            raise ValueError("Archive text search requires at least one field")
        invalid_fields = set(self.text_fields) - ARCHIVE_TEXT_FIELDS
        if invalid_fields:
            invalid = ", ".join(sorted(invalid_fields))
            raise ValueError(f"Unsupported archive text field(s): {invalid}")
        if self.limit is not None and self.limit < 0:
            raise ValueError("Archive query limit must be non-negative")


def _project_matches(
    query: ArchiveRunQuery, project_id: str, project_name: str
) -> bool:
    if query.project_id is not None and project_id != query.project_id:
        return False
    exact = query.project or query.project_name or query.project_name_exact
    if exact is not None and project_name != exact:
        return False
    if query.project_name_pattern is not None and not fnmatchcase(
        project_name, query.project_name_pattern
    ):
        return False
    if (
        query.project_name_regex is not None
        and re.search(query.project_name_regex, project_name) is None
    ):
        return False
    return True


def _canonical_uris(query: ArchiveRunQuery, config_path: str | None) -> list[str]:
    config = load_archive_config(config_path)
    since = ensure_aware_datetime(query.since)
    before = ensure_aware_datetime(query.before)
    uris: list[str] = []
    exact = query.project or query.project_name or query.project_name_exact
    routes = (config.route_project(exact),) if exact is not None else config.routes
    for route in routes:
        store = create_store(route.archive_uri)
        records = list_project_records(store)
        matching_project_ids: set[str] | None = None
        catalog_project_ids: set[str] = set()
        if records:
            matching_project_ids = set()
            for project in records:
                catalog_project_ids.add(project.project_id)
                configured_route = config.route_project(project.project_name)
                if configured_route.name != route.name:
                    raise ValueError(
                        "Archive project catalog entry is stored under the wrong "
                        f"route: {project.project_name}"
                    )
                if _project_matches(query, project.project_id, project.project_name):
                    matching_project_ids.add(project.project_id)
        elif query.project_id is not None:
            # Legacy archives may predate the project catalog. A project UUID still
            # maps directly to its manifest namespace without scanning all projects.
            matching_project_ids = {query.project_id}

        if matching_project_ids is None:
            manifest_keys = store.list_keys("manifests")
        else:
            manifest_keys = [
                key
                for project_id in sorted(matching_project_ids)
                for key in store.list_keys(f"manifests/project_id={project_id}")
            ]
            if catalog_project_ids:
                # A catalog is backfilled incrementally. Preserve visibility of
                # legacy/unindexed project namespaces during that rollout, while
                # avoiding GETs for unrelated projects already in the catalog.
                legacy_keys = [
                    key
                    for key in store.list_keys("manifests")
                    if (
                        (identity := _manifest_identity_from_key(key)) is None
                        or identity[0] not in catalog_project_ids
                    )
                ]
                manifest_keys.extend(legacy_keys)
        manifest_keys = [
            key
            for key in manifest_keys
            if (
                (identity := _manifest_identity_from_key(key)) is None
                or _date_partition_overlaps(identity[1], since, before)
            )
        ]
        for manifest in read_manifests(store, manifest_keys):
            if manifest.canonical_key is None:
                continue
            configured_route = config.route_project(manifest.project_name)
            if configured_route.name != route.name:
                raise ValueError(
                    f"Manifest project is stored under the wrong route: "
                    f"{manifest.project_name}"
                )
            if not _project_matches(query, manifest.project_id, manifest.project_name):
                continue
            if since is not None and manifest.window_end <= since:
                continue
            if before is not None and manifest.window_start >= before:
                continue
            # Empty canonical generations contain no rows and need not participate
            # in schema union. Skipping them also makes all-empty archives return an
            # immediate, deterministic zero instead of referencing absent columns.
            if manifest.canonical_run_count == 0:
                continue
            uris.append(store.object_uri(manifest.canonical_key))
    return sorted(set(uris))


def _date_partition_overlaps(
    trace_date: date, since: datetime | None, before: datetime | None
) -> bool:
    start = datetime.combine(trace_date, time.min, tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    return not (
        (since is not None and end <= since) or (before is not None and start >= before)
    )


def _normalize_event_time(value: object) -> object:
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


def _normalize_run_payload(payload: dict[str, Any]) -> dict[str, Any]:
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
    # Bulk v2 preserves LangChain's reserved ``inputs.input`` value with one
    # extra JSON layer. Decode that provider representation without stripping
    # nulls: explicit nested nulls are part of the live CLI output contract.
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
            event["time"] = _normalize_event_time(event["time"])
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


def _validated_archive_run(payload: dict[str, Any]) -> Run:
    """Validate an archive row and restore UTC on untyped SDK event datetimes."""
    from langsmith.schemas import Run

    run = Run.model_validate(_normalize_run_payload(payload))
    normalized_events: list[dict[str, Any]] = []
    for event in run.events or []:
        normalized_event = dict(event)
        if "time" in normalized_event:
            normalized_event["time"] = _normalize_event_time(normalized_event["time"])
        normalized_events.append(normalized_event)
    return run.model_copy(update={"events": normalized_events or run.events})


def _where_clause(query: ArchiveRunQuery) -> tuple[str, list[object]]:
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
        # Runs API snapshots expose a native VARCHAR[] while managed Bulk Export
        # writes the same field as JSON text. Casting either representation to
        # JSON keeps mixed-provider archives queryable.
        clauses.append("json_contains(CAST(tags AS JSON), to_json(?))")
        parameters.append(tag)
    if query.text is not None:
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


def query_archive_runs(
    query: ArchiveRunQuery, *, config_path: str | None = None
) -> list[Run]:
    """Return LangSmith Run contracts populated from canonical Parquet."""
    import duckdb

    uris = _canonical_uris(query, config_path)
    if not uris:
        return []

    where, where_parameters = _where_clause(query)
    parameters: list[object] = [uris, *where_parameters]
    limit = "" if query.limit is None or query.limit == 0 else " LIMIT ?"
    if limit:
        parameters.append(query.limit)
    sql = (
        "SELECT * FROM read_parquet(?, union_by_name=true)"
        f"{where} ORDER BY start_time DESC{limit}"
    )

    connection = duckdb.connect()
    try:
        configure_duckdb_s3(connection, uris)
        cursor = connection.execute(sql, parameters)
        columns = [description[0] for description in cursor.description]
        runs: list[Run] = []
        for row in cursor.fetchall():
            payload = dict(zip(columns, row, strict=True))
            runs.append(_validated_archive_run(payload))
        return runs
    finally:
        connection.close()


def count_archive_runs(
    query: ArchiveRunQuery, *, config_path: str | None = None
) -> int:
    """Count matching runs using Parquet metadata/predicate pushdown only."""
    import duckdb

    uris = _canonical_uris(query, config_path)
    if not uris:
        return 0
    where, parameters = _where_clause(query)
    connection = duckdb.connect()
    try:
        configure_duckdb_s3(connection, uris)
        row = connection.execute(
            f"SELECT count(*) FROM read_parquet(?, union_by_name=true){where}",
            [uris, *parameters],
        ).fetchone()
        if row is None:
            raise ValueError("DuckDB did not return an archive count")
        return int(row[0])
    finally:
        connection.close()


def read_archived_run(
    run_id: str,
    *,
    follow_children: bool,
    project: str | None = None,
    project_id: str | None = None,
    since: datetime | None = None,
    before: datetime | None = None,
    config_path: str | None = None,
) -> tuple[Run, list[Run]]:
    import duckdb

    uris = _canonical_uris(
        ArchiveRunQuery(
            project=project,
            project_id=project_id,
            since=since,
            before=before,
            limit=0,
        ),
        config_path,
    )
    if not uris:
        raise LookupError(f"Archived run not found: {run_id}")
    connection = duckdb.connect()
    try:
        configure_duckdb_s3(connection, uris)
        cursor = connection.execute(
            "SELECT * FROM read_parquet(?, union_by_name=true) "
            "WHERE CAST(id AS VARCHAR) = ? LIMIT 1",
            [uris, run_id],
        )
        row = cursor.fetchone()
        if row is None:
            raise LookupError(f"Archived run not found: {run_id}")
        columns = [description[0] for description in cursor.description]
        run = _validated_archive_run(dict(zip(columns, row, strict=True)))
        children: list[Run] = []
        if follow_children:
            child_cursor = connection.execute(
                "SELECT * FROM read_parquet(?, union_by_name=true) "
                "WHERE CAST(trace_id AS VARCHAR) = ? AND CAST(id AS VARCHAR) != ? "
                "ORDER BY start_time",
                [uris, str(run.trace_id or run.id), run_id],
            )
            child_columns = [description[0] for description in child_cursor.description]
            children = [
                _validated_archive_run(dict(zip(child_columns, child, strict=True)))
                for child in child_cursor.fetchall()
            ]
            # Bulk Export does not include the API's derived child_run_ids field.
            # The trace query above is authoritative, ordered identically to the
            # live CLI path, and restores full-trace output parity.
            run = run.model_copy(
                update={"child_run_ids": [child.id for child in children]}
            )
        return run, children
    finally:
        connection.close()
