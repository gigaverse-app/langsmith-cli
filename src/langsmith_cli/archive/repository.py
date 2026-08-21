"""Typed archive metadata repository and publication contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import json
import re
from typing import Callable, TypeVar, cast

from langsmith_cli.archive.models import (
    ArchiveManifest,
    ArchiveManifestDict,
    ArchiveProject,
    ArchiveProjectDict,
)
from langsmith_cli.archive.storage import (
    ArchiveStore,
    ConcurrentArchiveWriteError,
)


@dataclass(frozen=True)
class ManifestSnapshot:
    manifest: ArchiveManifest
    version: str


MetadataRecord = TypeVar("MetadataRecord")
MAX_METADATA_READ_WORKERS = 16
_MANIFEST_KEY_RE = re.compile(
    r"^manifests/project_id=([^/]+)/date=(\d{4}-\d{2}-\d{2})\.json$"
)


def manifest_key(project_id: str, trace_date: str) -> str:
    return f"manifests/project_id={project_id}/date={trace_date}.json"


def manifest_identity_from_key(key: str) -> tuple[str, date] | None:
    """Parse the immutable project/date identity encoded in a manifest key."""
    match = _MANIFEST_KEY_RE.fullmatch(key)
    return (
        (match.group(1), date.fromisoformat(match.group(2)))
        if match is not None
        else None
    )


def project_key(project_id: str) -> str:
    return f"projects/project_id={project_id}.json"


def ensure_project_record(
    store: ArchiveStore, project_id: str, project_name: str
) -> ArchiveProject:
    """Create an immutable project catalog entry or verify the existing identity."""
    project = ArchiveProject(
        schema_version=1, project_id=project_id, project_name=project_name
    )
    key = project_key(project_id)
    if store.exists(key):
        existing = _read_project_record(store, key)
        if existing != project:
            raise ValueError(
                "Archived project identity changed; migrate it before syncing"
            )
        return existing

    content = json.dumps(project.to_dict(), ensure_ascii=False, sort_keys=True)
    try:
        store.put_text_if_version(key, content, None)
    except ConcurrentArchiveWriteError:
        # A same-project worker may win the create race. Accept only the exact same
        # immutable identity; a rename or route collision remains a hard failure.
        existing = _read_project_record(store, key)
        if existing != project:
            raise ValueError("Archived project identity changed concurrently") from None
        return existing
    return project


def list_project_records(store: ArchiveStore) -> tuple[ArchiveProject, ...]:
    keys = store.list_keys("projects")
    return _read_independent_metadata(
        keys, lambda key: _read_project_record(store, key)
    )


def read_manifests(store: ArchiveStore, keys: list[str]) -> tuple[ArchiveManifest, ...]:
    """Read independent manifests with bounded remote I/O concurrency."""

    def read_known_manifest(key: str) -> ArchiveManifest:
        manifest = read_manifest(store, key, known_exists=True)
        if manifest is None:
            raise RuntimeError(f"Listed archive manifest disappeared: {key}")
        return manifest

    return _read_independent_metadata(keys, read_known_manifest)


def read_manifest(
    store: ArchiveStore, key: str, *, known_exists: bool = False
) -> ArchiveManifest | None:
    snapshot = read_manifest_snapshot(store, key, known_exists=known_exists)
    return snapshot.manifest if snapshot is not None else None


def read_manifest_snapshot(
    store: ArchiveStore, key: str, *, known_exists: bool = False
) -> ManifestSnapshot | None:
    if not known_exists and not store.exists(key):
        return None
    stored = store.get_text_with_version(key)
    raw: object = json.loads(stored.content)
    payload = _validate_manifest_payload(raw, key)
    manifest = ArchiveManifest.from_dict(payload)
    manifest.validate_publishable()
    _validate_manifest_location(key, manifest)
    return ManifestSnapshot(manifest=manifest, version=stored.version)


def write_manifest(
    store: ArchiveStore,
    key: str,
    manifest: ArchiveManifest,
    *,
    expected_version: str | None,
) -> None:
    manifest.validate_publishable()
    _validate_manifest_location(key, manifest)
    payload: ArchiveManifestDict = manifest.to_dict()
    store.put_text_if_version(
        key,
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        expected_version,
    )


def _validate_manifest_location(key: str, manifest: ArchiveManifest) -> None:
    expected = manifest_key(manifest.project_id, manifest.trace_date.isoformat())
    if key != expected:
        raise ValueError("Manifest object key does not match its project/date")


def _read_project_record(store: ArchiveStore, key: str) -> ArchiveProject:
    raw: object = json.loads(store.get_text(key))
    if not isinstance(raw, dict):
        raise ValueError(f"Archive project record is not an object: {key}")
    if set(raw) != {"schema_version", "project_id", "project_name"}:
        raise ValueError(f"Archive project record has an invalid schema: {key}")
    if type(raw["schema_version"]) is not int:
        raise ValueError("Archive project schema_version must be an integer")
    if not isinstance(raw["project_id"], str) or not isinstance(
        raw["project_name"], str
    ):
        raise ValueError("Archive project identifiers must be strings")
    project = ArchiveProject.from_dict(cast(ArchiveProjectDict, raw))
    if key != project_key(project.project_id):
        raise ValueError("Archive project object key does not match its project_id")
    return project


def _read_independent_metadata(
    keys: list[str], reader: Callable[[str], MetadataRecord]
) -> tuple[MetadataRecord, ...]:
    if len(keys) < 2:
        return tuple(reader(key) for key in keys)

    # Metadata objects are immutable, tiny, and independent. One bounded reader
    # serves project catalogs, status, and archived queries so those paths cannot
    # drift back to one S3 round trip at a time as the archive grows.
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(
        max_workers=min(MAX_METADATA_READ_WORKERS, len(keys))
    ) as executor:
        return tuple(executor.map(reader, keys))


def _validate_manifest_payload(raw: object, key: str) -> ArchiveManifestDict:
    if not isinstance(raw, dict):
        raise ValueError(f"Archive manifest is not an object: {key}")
    required = {
        "schema_version",
        "project_id",
        "project_name",
        "trace_date",
        "window_start",
        "window_end",
        "primary",
        "reconciliation",
        "canonical_key",
        "canonical_run_count",
        "sealed",
        "updated_at",
    }
    if set(raw) != required:
        raise ValueError(f"Archive manifest has an invalid schema: {key}")
    for field in (
        "project_id",
        "project_name",
        "trace_date",
        "window_start",
        "window_end",
        "updated_at",
    ):
        if not isinstance(raw[field], str):
            raise ValueError(f"Archive manifest field must be a string: {field}")
    if type(raw["schema_version"]) is not int:
        raise ValueError("Archive manifest schema_version must be an integer")
    if type(raw["canonical_run_count"]) is not int:
        raise ValueError("Archive manifest canonical_run_count must be an integer")
    if type(raw["sealed"]) is not bool:
        raise ValueError("Archive manifest sealed must be a boolean")
    canonical_key = raw["canonical_key"]
    if canonical_key is not None and not isinstance(canonical_key, str):
        raise ValueError("Archive manifest canonical_key must be a string or null")
    _validate_phase_payload(raw["primary"], "primary")
    _validate_phase_payload(raw["reconciliation"], "reconciliation")
    return cast(ArchiveManifestDict, raw)


def _validate_phase_payload(raw: object, field: str) -> None:
    if raw is None:
        return
    if not isinstance(raw, dict):
        raise ValueError(f"Archive manifest {field} phase must be an object")
    required = {"status", "generation_id", "raw_key", "run_count", "verified_at"}
    allowed = required | {"error"}
    if not required <= set(raw) <= allowed:
        raise ValueError(f"Archive manifest {field} phase has an invalid schema")
    for phase_field in ("status", "generation_id", "raw_key", "verified_at"):
        if not isinstance(raw[phase_field], str):
            raise ValueError(f"Archive manifest {field}.{phase_field} must be a string")
    if type(raw["run_count"]) is not int:
        raise ValueError(f"Archive manifest {field}.run_count must be an integer")
    if "error" in raw and not isinstance(raw["error"], str):
        raise ValueError(f"Archive manifest {field}.error must be a string")
