"""Strict data contracts for dataset replicas.

The public entities remain LangSmith's ``Dataset``, ``Example``, and
``DatasetVersion`` models. These small contracts describe only the on-disk index
and the JSON-compatible payload stored in Parquet.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, TypedDict


REPLICA_SCHEMA_VERSION = 1


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
    dataset_name: str
    version: VersionPayload
    dataset_key: str
    examples_key: str
    example_count: int
    attachment_count: int
    published_at: str


class HeadVersionPayload(TypedDict):
    as_of: str
    manifest_key: str
    tags: list[str] | None


class DatasetHeadPayload(TypedDict):
    schema_version: int
    dataset_id: str
    dataset_name: str
    latest_as_of: str
    versions: list[HeadVersionPayload]


@dataclass(frozen=True)
class AttachmentBlob:
    payload: AttachmentPayload
    content: bytes


@dataclass(frozen=True)
class SerializedExample:
    payload: ExamplePayload
    attachments: list[AttachmentPayload]
    blobs: list[AttachmentBlob]


@dataclass(frozen=True)
class ReplicaWriteResult:
    dataset_id: str
    dataset_name: str
    as_of: datetime
    example_count: int
    attachment_count: int
    already_present: bool
    destination_uri: str


@dataclass(frozen=True)
class ReplicaStatus:
    dataset_id: str
    dataset_name: str
    latest_as_of: datetime
    versions: int
    source_uri: str


def datetime_text(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
