"""DuckDB-backed queries over published archive manifests."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from fnmatch import fnmatchcase
import re
from typing import TYPE_CHECKING

from langsmith_cli.archive.config import load_archive_config
from langsmith_cli.archive.duckdb import (
    archive_duckdb_connection,
    configure_duckdb_s3,
)
from langsmith_cli.archive.parquet import (
    normalize_run_payload,
    parquet_where_clause as _where_clause,
    validated_parquet_run as _validated_archive_run,
)
from langsmith_cli.archive.repository import (
    list_project_records,
    manifest_identity_from_key as _manifest_identity_from_key,
    read_manifests,
)
from langsmith_cli.archive.storage import create_store
from langsmith_cli.time_parsing import ensure_aware_datetime
from langsmith_cli.trace_query import RunQuery

if TYPE_CHECKING:
    from langsmith.schemas import Run


# Compatibility name for the public archive API. Archive and local now consume the
# exact same Pydantic query contract instead of maintaining near-identical models.
ArchiveRunQuery = RunQuery
# Private test/import compatibility while canonical implementation lives in
# archive.parquet for reuse by the local backend.
_normalize_run_payload = normalize_run_payload


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


def query_archive_runs(
    query: ArchiveRunQuery, *, config_path: str | None = None
) -> list[Run]:
    """Return LangSmith Run contracts populated from canonical Parquet."""
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
        f"{where} ORDER BY start_time DESC, CAST(id AS VARCHAR) ASC{limit}"
    )

    with archive_duckdb_connection() as connection:
        configure_duckdb_s3(connection, uris)
        cursor = connection.execute(sql, parameters)
        columns = [description[0] for description in cursor.description]
        runs: list[Run] = []
        for row in cursor.fetchall():
            payload = dict(zip(columns, row, strict=True))
            runs.append(_validated_archive_run(payload))
        return runs


def count_archive_runs(
    query: ArchiveRunQuery, *, config_path: str | None = None
) -> int:
    """Count matching runs using Parquet metadata/predicate pushdown only."""
    uris = _canonical_uris(query, config_path)
    if not uris:
        return 0
    where, parameters = _where_clause(query)
    with archive_duckdb_connection() as connection:
        configure_duckdb_s3(connection, uris)
        row = connection.execute(
            f"SELECT count(*) FROM read_parquet(?, union_by_name=true){where}",
            [uris, *parameters],
        ).fetchone()
        if row is None:
            raise ValueError("DuckDB did not return an archive count")
        return int(row[0])


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
    with archive_duckdb_connection() as connection:
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
