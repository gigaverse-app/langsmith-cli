"""Atomic immutable-fragment repository for explicitly cached local traces."""

from __future__ import annotations

from collections.abc import Iterable
from fnmatch import fnmatchcase
import hashlib
import json
from pathlib import Path
import re
import tempfile
from typing import TYPE_CHECKING

from langsmith_cli.archive.duckdb import archive_duckdb_connection
from langsmith_cli.archive.parquet import parquet_where_clause, validated_parquet_run
from langsmith_cli.archive.storage import (
    ConcurrentArchiveWriteError,
    LocalArchiveStore,
)
from langsmith_cli.archive.sync import write_runs_parquet
from langsmith_cli.local_traces.models import (
    TraceCacheWriteResult,
    TraceCatalog,
    TraceFragment,
    TraceEvictResult,
    TraceProjectSummary,
    TracePullRecord,
    TracePullRequest,
)
from langsmith_cli.trace_query import RunQuery

if TYPE_CHECKING:
    from langsmith.schemas import Run


CATALOG_KEY = "traces/catalog.json"
FRAGMENT_PREFIX = "traces/fragments"
MAX_CATALOG_PUBLICATION_ATTEMPTS = 16


class LocalTraceRepository:
    """Expose one additive logical inventory over immutable Parquet observations."""

    def __init__(self, root: Path) -> None:
        self._root = root
        self._store = LocalArchiveStore(root=root, base_uri=str(root.resolve()))

    def read_catalog(self) -> TraceCatalog:
        if not self._store.exists(CATALOG_KEY):
            return TraceCatalog()
        return TraceCatalog.model_validate_json(self._store.get_text(CATALOG_KEY))

    def add_runs(
        self, request: TracePullRequest, runs: Iterable[Run]
    ) -> TraceCacheWriteResult:
        """Stage one immutable delta and atomically add it to the logical inventory.

        INVARIANT: a fragment is queryable only after its entry is merged through
        the catalog compare-and-swap. Failed or stale writers can leave immutable
        unreferenced objects, but can never expose a partial cache update or erase
        another writer's additions.
        """
        normalized_runs = self._normalize_project_identity(request, runs)
        if not normalized_runs:
            self._publish_pull(request, content_digest=None, runs=())
            total = self.count(RunQuery(limit=None))
            return TraceCacheWriteResult(
                added_run_count=0,
                selected_run_count=0,
                total_run_count=total,
                fragment_count=len(self.read_catalog().fragments),
                content_digest=None,
            )

        content_digest = _content_digest(request, normalized_runs)
        existing = self.read_catalog()
        if any(
            fragment.content_digest == content_digest for fragment in existing.fragments
        ):
            return TraceCacheWriteResult(
                added_run_count=0,
                selected_run_count=len(normalized_runs),
                total_run_count=self._count_catalog(existing, RunQuery(limit=None)),
                fragment_count=len(existing.fragments),
                content_digest=content_digest,
            )

        with tempfile.TemporaryDirectory(prefix="langsmith-local-traces-") as raw:
            staged_path = Path(raw) / "runs.parquet"
            written = write_runs_parquet(iter(normalized_runs), staged_path)
            if written != len(normalized_runs):
                raise ValueError("Local trace Parquet row count changed while staging")
            parquet_sha256 = _file_sha256(staged_path)
            fragment_key = f"{FRAGMENT_PREFIX}/{content_digest}.parquet"
            if self._store.exists(fragment_key):
                if (
                    hashlib.sha256(self._store.get_bytes(fragment_key)).hexdigest()
                    != parquet_sha256
                ):
                    raise ValueError("Local trace fragment content-address collision")
            else:
                self._store.put_file(fragment_key, staged_path)

        fragment = TraceFragment(
            key=fragment_key,
            sha256=parquet_sha256,
            content_digest=content_digest,
            row_count=len(normalized_runs),
            project_id=request.project_id,
            project_name=request.project_name,
            origin=request.source,
            observed_at=request.requested_at,
        )
        catalog, added_count = self._publish_fragment(
            request, fragment, normalized_runs
        )
        return TraceCacheWriteResult(
            added_run_count=added_count,
            selected_run_count=len(normalized_runs),
            total_run_count=self._count_catalog(catalog, RunQuery(limit=None)),
            fragment_count=len(catalog.fragments),
            content_digest=content_digest,
        )

    def query(self, query: RunQuery) -> list[Run]:
        return self._query_catalog(self.read_catalog(), query)

    def count(self, query: RunQuery) -> int:
        return self._count_catalog(self.read_catalog(), query)

    def get(self, run_id: str, *, follow_children: bool) -> tuple[Run, list[Run]]:
        matches = self.query(RunQuery(run_id=run_id, limit=1))
        if not matches:
            raise LookupError(f"Local run not found: {run_id}")
        run = matches[0]
        children: list[Run] = []
        if follow_children:
            trace_id = str(run.trace_id or run.id)
            children = [
                candidate
                for candidate in reversed(
                    self.query(RunQuery(trace_id=trace_id, limit=None))
                )
                if str(candidate.id) != run_id
            ]
            run = run.model_copy(
                update={"child_run_ids": [child.id for child in children]}
            )
        return run, children

    def list_projects(self) -> list[TraceProjectSummary]:
        catalog = self.read_catalog()
        projects = sorted(
            {
                (fragment.project_id, fragment.project_name)
                for fragment in catalog.fragments
            },
            key=lambda identity: identity[1],
        )
        summaries: list[TraceProjectSummary] = []
        for project_id, project_name in projects:
            project_fragments = [
                fragment
                for fragment in catalog.fragments
                if fragment.project_id == project_id
                and fragment.project_name == project_name
            ]
            runs = self._query_catalog(
                catalog, RunQuery(project_id=project_id, limit=None)
            )
            start_times = [run.start_time for run in runs]
            summaries.append(
                TraceProjectSummary(
                    project_id=project_id,
                    project_name=project_name,
                    run_count=len(runs),
                    fragment_count=len(project_fragments),
                    oldest_run_start_time=min(start_times) if start_times else None,
                    newest_run_start_time=max(start_times) if start_times else None,
                    last_updated=max(
                        fragment.observed_at for fragment in project_fragments
                    ),
                    origins=tuple(
                        sorted(
                            {fragment.origin for fragment in project_fragments},
                            key=lambda origin: origin.value,
                        )
                    ),
                )
            )
        return summaries

    def evict(self, project_name: str | None = None) -> TraceEvictResult:
        """Atomically remove logical reachability without racing active readers.

        Immutable fragment objects are deliberately left for later garbage
        collection. Deleting bytes after releasing the catalog CAS lock could race
        a concurrent writer that re-published the same content-addressed fragment.
        """
        for _attempt in range(MAX_CATALOG_PUBLICATION_ATTEMPTS):
            catalog, expected_version = self._read_catalog_snapshot()
            before_count = self._count_catalog(catalog, RunQuery(limit=None))
            if project_name is None:
                retained_fragments: tuple[TraceFragment, ...] = ()
                retained_pulls: tuple[TracePullRecord, ...] = ()
            else:
                retained_fragments = tuple(
                    fragment
                    for fragment in catalog.fragments
                    if fragment.project_name != project_name
                )
                retained_pulls = tuple(
                    pull
                    for pull in catalog.pulls
                    if pull.request.project_name != project_name
                )
            candidate = TraceCatalog(
                fragments=retained_fragments,
                pulls=retained_pulls,
            )
            if candidate == catalog:
                return TraceEvictResult(
                    removed_run_count=0,
                    removed_fragment_count=0,
                    remaining_run_count=before_count,
                    remaining_fragment_count=len(catalog.fragments),
                )
            try:
                self._store.put_text_if_version(
                    CATALOG_KEY,
                    candidate.model_dump_json(indent=2),
                    expected_version,
                )
            except ConcurrentArchiveWriteError:
                continue
            remaining_count = self._count_catalog(candidate, RunQuery(limit=None))
            return TraceEvictResult(
                removed_run_count=before_count - remaining_count,
                removed_fragment_count=len(catalog.fragments)
                - len(candidate.fragments),
                remaining_run_count=remaining_count,
                remaining_fragment_count=len(candidate.fragments),
            )
        raise ConcurrentArchiveWriteError(
            "Local trace catalog changed during every eviction attempt"
        )

    def _publish_fragment(
        self,
        request: TracePullRequest,
        fragment: TraceFragment,
        runs: list[Run],
    ) -> tuple[TraceCatalog, int]:
        selected_ids = {str(run.id) for run in runs}
        for _attempt in range(MAX_CATALOG_PUBLICATION_ATTEMPTS):
            catalog, expected_version = self._read_catalog_snapshot()
            if any(
                current.content_digest == fragment.content_digest
                for current in catalog.fragments
            ):
                return catalog, 0
            existing_ids = {
                str(run.id)
                for run in self._query_catalog(catalog, RunQuery(limit=None))
            }
            added_count = len(selected_ids - existing_ids)
            pull = TracePullRecord(
                request=request,
                content_digest=fragment.content_digest,
                selected_run_count=len(runs),
                new_identity_count=added_count,
            )
            candidate = catalog.model_copy(
                update={
                    "fragments": (*catalog.fragments, fragment),
                    "pulls": (*catalog.pulls, pull),
                }
            )
            try:
                self._store.put_text_if_version(
                    CATALOG_KEY,
                    candidate.model_dump_json(indent=2),
                    expected_version,
                )
            except ConcurrentArchiveWriteError:
                continue
            return candidate, added_count
        raise ConcurrentArchiveWriteError(
            "Local trace catalog changed during every publication attempt"
        )

    def _publish_pull(
        self,
        request: TracePullRequest,
        *,
        content_digest: str | None,
        runs: tuple[Run, ...],
    ) -> TraceCatalog:
        for _attempt in range(MAX_CATALOG_PUBLICATION_ATTEMPTS):
            catalog, expected_version = self._read_catalog_snapshot()
            pull = TracePullRecord(
                request=request,
                content_digest=content_digest,
                selected_run_count=len(runs),
                new_identity_count=0,
            )
            candidate = catalog.model_copy(update={"pulls": (*catalog.pulls, pull)})
            try:
                self._store.put_text_if_version(
                    CATALOG_KEY,
                    candidate.model_dump_json(indent=2),
                    expected_version,
                )
            except ConcurrentArchiveWriteError:
                continue
            return candidate
        raise ConcurrentArchiveWriteError(
            "Local trace catalog changed during every publication attempt"
        )

    def _read_catalog_snapshot(self) -> tuple[TraceCatalog, str | None]:
        if not self._store.exists(CATALOG_KEY):
            return TraceCatalog(), None
        snapshot = self._store.get_text_with_version(CATALOG_KEY)
        return TraceCatalog.model_validate_json(snapshot.content), snapshot.version

    def _query_catalog(self, catalog: TraceCatalog, query: RunQuery) -> list[Run]:
        fragments = self._matching_fragments(catalog, query)
        if not fragments:
            return []
        paths = [self._approved_fragment_path(fragment) for fragment in fragments]
        where, where_parameters = parquet_where_clause(query)
        limit_sql = "" if query.limit is None or query.limit == 0 else " LIMIT ?"
        parameters: list[object] = [paths, *where_parameters]
        if limit_sql:
            parameters.append(query.limit)

        with archive_duckdb_connection(
            allowed_paths=[Path(path) for path in paths]
        ) as connection:
            connection.execute(
                "CREATE TEMP TABLE local_fragment_metadata("
                "path VARCHAR PRIMARY KEY, observed_at TIMESTAMPTZ, digest VARCHAR)"
            )
            for fragment, path in zip(fragments, paths, strict=True):
                connection.execute(
                    "INSERT INTO local_fragment_metadata VALUES (?, ?, ?)",
                    [path, fragment.observed_at, fragment.content_digest],
                )
            logical_inventory = (
                "SELECT * EXCLUDE (filename, _observed_at, _fragment_digest, _rank) "
                "FROM (SELECT runs.*, metadata.observed_at AS _observed_at, "
                "metadata.digest AS _fragment_digest, row_number() OVER ("
                "PARTITION BY CAST(runs.session_id AS VARCHAR), CAST(runs.id AS VARCHAR) "
                "ORDER BY metadata.observed_at DESC, metadata.digest DESC) AS _rank "
                "FROM read_parquet(?, union_by_name=true, filename=true) AS runs "
                "JOIN local_fragment_metadata AS metadata ON runs.filename = metadata.path) "
                "ranked WHERE _rank = 1"
            )
            cursor = connection.execute(
                f"SELECT * FROM ({logical_inventory}) inventory{where} "
                f"ORDER BY start_time DESC, CAST(id AS VARCHAR) ASC{limit_sql}",
                parameters,
            )
            columns = [description[0] for description in cursor.description]
            return [
                validated_parquet_run(dict(zip(columns, row, strict=True)))
                for row in cursor.fetchall()
            ]

    def _count_catalog(self, catalog: TraceCatalog, query: RunQuery) -> int:
        return len(
            self._query_catalog(catalog, query.model_copy(update={"limit": None}))
        )

    def _matching_fragments(
        self, catalog: TraceCatalog, query: RunQuery
    ) -> list[TraceFragment]:
        exact = query.project or query.project_name or query.project_name_exact
        fragments: list[TraceFragment] = []
        for fragment in catalog.fragments:
            if query.project_id is not None and fragment.project_id != query.project_id:
                continue
            if exact is not None and fragment.project_name != exact:
                continue
            if query.project_name_pattern is not None and not fnmatchcase(
                fragment.project_name, query.project_name_pattern
            ):
                continue
            if (
                query.project_name_regex is not None
                and re.search(query.project_name_regex, fragment.project_name) is None
            ):
                continue
            fragments.append(fragment)
        return fragments

    def _approved_fragment_path(self, fragment: TraceFragment) -> str:
        root = self._root.resolve()
        path = (self._root / fragment.key).resolve()
        if not path.is_relative_to(root):
            raise ValueError("Trace fragment points outside the local trace cache")
        if not path.is_file():
            raise ValueError(f"Local trace fragment is missing: {fragment.key}")
        return str(path)

    @staticmethod
    def _normalize_project_identity(
        request: TracePullRequest, runs: Iterable[Run]
    ) -> list[Run]:
        from uuid import UUID

        project_id = UUID(request.project_id)
        normalized: list[Run] = []
        for run in runs:
            if run.session_id is not None and run.session_id != project_id:
                raise ValueError(
                    "Run session_id does not match the explicit pull project identity"
                )
            normalized.append(run.model_copy(update={"session_id": project_id}))
        return normalized


def _content_digest(request: TracePullRequest, runs: list[Run]) -> str:
    digest = hashlib.sha256()
    digest.update(request.project_id.encode("utf-8"))
    digest.update(b"\0")
    digest.update(request.project_name.encode("utf-8"))
    for run in sorted(runs, key=lambda item: str(item.id)):
        digest.update(b"\0")
        digest.update(
            json.dumps(
                run.model_dump(mode="json"),
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        )
    return digest.hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
