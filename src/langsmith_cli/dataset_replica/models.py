"""Strict data contracts for dataset replicas.

The public entities remain LangSmith's ``Dataset``, ``Example``, and
``DatasetVersion`` models. These small contracts describe only the on-disk index
and the JSON-compatible payload stored in Parquet.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, TypedDict


REPLICA_SCHEMA_VERSION = 2


class ReplicaSource(str, Enum):
    CLOUD = "cloud"
    ARCHIVE = "archive"
    LOCAL = "local"


class ReplicaDestination(str, Enum):
    ARCHIVE = "archive"
    LOCAL = "local"


class AttachmentPayload(TypedDict):
    name: str
    mime_type: str | None
    digest: str
    size: int


class DatasetPayload(TypedDict):
    name: str
    description: str | None
    data_type: str | None
    id: str
    created_at: str
    modified_at: str | None
    example_count: int | None
    session_count: int | None
    last_session_start_time: str | None
    inputs_schema: dict[str, Any] | None
    outputs_schema: dict[str, Any] | None
    transformations: list[DatasetTransformationPayload] | None
    metadata: dict[str, Any] | None


class ExamplePayload(TypedDict):
    id: str
    dataset_id: str
    inputs: dict[str, Any] | None
    outputs: dict[str, Any] | None
    metadata: dict[str, Any] | None
    created_at: str
    modified_at: str | None
    source_run_id: str | None


class DatasetTransformationPayload(TypedDict, total=False):
    path: list[str]
    transformation_type: str


class VersionPayload(TypedDict):
    tags: list[str] | None
    as_of: str


class SnapshotManifestPayload(TypedDict):
    schema_version: int
    dataset_id: str
    version: VersionPayload
    examples_key: str
    examples_sha256: str
    content_digest: str
    example_count: int
    attachment_count: int
    published_at: str


class HeadVersionPayload(TypedDict):
    as_of: str
    manifest_key: str
    manifest_sha256: str
    tags: list[str] | None


class DatasetHeadPayload(TypedDict):
    schema_version: int
    dataset_id: str
    dataset: DatasetPayload
    latest_as_of: str
    versions: list[HeadVersionPayload]


def datetime_text(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Replica timestamps must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat()


def parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Replica timestamps must be timezone-aware")
    return parsed.astimezone(timezone.utc)
