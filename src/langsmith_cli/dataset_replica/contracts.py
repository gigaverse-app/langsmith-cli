"""Strict Pydantic contracts loaded only for dataset-replica operations.

Normal cloud CLI startup imports only the lightweight enums and TypedDict wire
schemas from ``models``. Repository commands load this module lazily when they
actually read or publish a replica.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from langsmith_cli.dataset_replica.models import (
    AttachmentPayload,
    ExamplePayload,
)


class ReplicaContract(BaseModel):
    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        extra="forbid",
        frozen=True,
        strict=True,
    )


class SerializedExample(ReplicaContract):
    payload: ExamplePayload
    attachments: list[AttachmentPayload]


class StagedAttachment(ReplicaContract):
    path: Path


class StagedSnapshot(ReplicaContract):
    example_count: int
    attachment_count: int
    content_digest: str
    attachments: dict[str, StagedAttachment]


class ReplicaWriteResult(ReplicaContract):
    dataset_id: str
    dataset_name: str
    as_of: datetime
    example_count: int
    attachment_count: int
    already_present: bool
    destination_uri: str


class ReplicaStatus(ReplicaContract):
    dataset_id: str
    dataset_name: str
    latest_as_of: datetime
    versions: int
    source_uri: str
