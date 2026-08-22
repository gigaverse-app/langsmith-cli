"""Atomic Parquet publication and strict SDK-model reads for dataset replicas."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import tempfile
from typing import TYPE_CHECKING, Any, cast

from langsmith_cli.archive.duckdb import (
    ARCHIVE_PARQUET_COPY_OPTIONS,
    archive_duckdb_connection,
)
from langsmith_cli.archive.storage import ArchiveStore
from langsmith_cli.dataset_replica.models import (
    AttachmentBlob,
    AttachmentPayload,
    DatasetHeadPayload,
    DatasetPayload,
    DatasetTransformationPayload,
    ExamplePayload,
    HeadVersionPayload,
    REPLICA_SCHEMA_VERSION,
    ReplicaStatus,
    ReplicaWriteResult,
    SerializedExample,
    SnapshotManifestPayload,
    VersionPayload,
    datetime_text,
    parse_datetime,
)

if TYPE_CHECKING:
    from langsmith.schemas import (
        Dataset,
        DatasetTransformation,
        DatasetVersion,
        Example,
    )


HEADS_PREFIX = "datasets/heads"
BLOBS_PREFIX = "datasets/blobs"


class DatasetReplicaError(RuntimeError):
    """Base error for malformed or ambiguous replicas."""


class DatasetReplicaNotFoundError(DatasetReplicaError):
    """A requested dataset, version, or example is absent."""


class DatasetReplicaAmbiguousError(DatasetReplicaError):
    """A name resolves to multiple stable dataset identities."""


class DatasetReplicaRepository:
    """Read and atomically publish exact dataset versions in one object store."""

    def __init__(self, store: ArchiveStore) -> None:
        self._store = store

    @property
    def base_uri(self) -> str:
        return self._store.base_uri

    def write_snapshot(
        self,
        dataset: Dataset,
        version: DatasetVersion,
        examples: Iterable[Example],
    ) -> ReplicaWriteResult:
        dataset_payload = _serialize_dataset(dataset)
        version_payload = _serialize_version(version)
        dataset_id = dataset_payload["id"]
        old_head, expected_version = self._read_head_for_update(dataset_id)
        if old_head is not None:
            for item in old_head["versions"]:
                if item["as_of"] == version_payload["as_of"]:
                    if (
                        item["tags"] != version_payload["tags"]
                        or old_head["dataset_name"] != dataset_payload["name"]
                    ):
                        item["tags"] = version_payload["tags"]
                        updated_head: DatasetHeadPayload = {
                            **old_head,
                            "dataset_name": dataset_payload["name"],
                        }
                        self._store.put_text_if_version(
                            _head_key(dataset_id),
                            _json_text(updated_head),
                            expected_version,
                        )
                    manifest = self._read_manifest(item["manifest_key"])
                    return ReplicaWriteResult(
                        dataset_id=dataset_id,
                        dataset_name=dataset_payload["name"],
                        as_of=version.as_of,
                        example_count=manifest["example_count"],
                        attachment_count=manifest["attachment_count"],
                        already_present=True,
                        destination_uri=self.base_uri,
                    )

        serialized_examples = [_serialize_example(example) for example in examples]
        version_token = hashlib.sha256(
            version_payload["as_of"].encode("utf-8")
        ).hexdigest()
        version_prefix = f"datasets/{dataset_id}/versions/{version_token}"
        dataset_key = f"{version_prefix}/dataset.parquet"
        examples_key = f"{version_prefix}/examples.parquet"
        manifest_key = f"{version_prefix}/manifest.json"

        with tempfile.TemporaryDirectory(prefix="langsmith-dataset-replica-") as raw:
            staging = Path(raw)
            dataset_path = staging / "dataset.parquet"
            examples_path = staging / "examples.parquet"
            _write_dataset_parquet(dataset_path, dataset_payload)
            _write_examples_parquet(examples_path, serialized_examples)
            self._store.put_file(dataset_key, dataset_path)
            self._store.put_file(examples_key, examples_path)

        unique_blobs: dict[str, AttachmentBlob] = {}
        for example in serialized_examples:
            for blob in example.blobs:
                unique_blobs[blob.payload["digest"]] = blob
        for digest, blob in unique_blobs.items():
            blob_key = f"{BLOBS_PREFIX}/{digest}"
            if not self._store.exists(blob_key):
                self._store.put_bytes(blob_key, blob.content)

        attachment_count = sum(
            len(example.attachments) for example in serialized_examples
        )
        manifest: SnapshotManifestPayload = {
            "schema_version": REPLICA_SCHEMA_VERSION,
            "dataset_id": dataset_id,
            "dataset_name": dataset_payload["name"],
            "version": version_payload,
            "dataset_key": dataset_key,
            "examples_key": examples_key,
            "example_count": len(serialized_examples),
            "attachment_count": attachment_count,
            "published_at": datetime.now(timezone.utc).isoformat(),
        }
        self._store.put_text(manifest_key, _json_text(manifest))

        versions = [] if old_head is None else list(old_head["versions"])
        versions.append(
            HeadVersionPayload(
                as_of=version_payload["as_of"],
                manifest_key=manifest_key,
                tags=version_payload["tags"],
            )
        )
        versions.sort(key=lambda item: parse_datetime(item["as_of"]))
        head: DatasetHeadPayload = {
            "schema_version": REPLICA_SCHEMA_VERSION,
            "dataset_id": dataset_id,
            "dataset_name": dataset_payload["name"],
            "latest_as_of": versions[-1]["as_of"],
            "versions": versions,
        }
        self._store.put_text_if_version(
            _head_key(dataset_id), _json_text(head), expected_version
        )
        return ReplicaWriteResult(
            dataset_id=dataset_id,
            dataset_name=dataset_payload["name"],
            as_of=version.as_of,
            example_count=len(serialized_examples),
            attachment_count=attachment_count,
            already_present=False,
            destination_uri=self.base_uri,
        )

    def list_datasets(self) -> list[Dataset]:
        return [self._read_dataset_from_head(head) for head in self._list_heads()]

    def read_dataset(self, name_or_id: str, as_of: str | None = None) -> Dataset:
        head = self._resolve_head(name_or_id)
        manifest = self._resolve_manifest(head, as_of)
        return self._read_dataset(manifest)

    def list_versions(self, name_or_id: str) -> list[DatasetVersion]:
        from langsmith.schemas import DatasetVersion

        head = self._resolve_head(name_or_id)
        versions: list[DatasetVersion] = []
        for item in reversed(head["versions"]):
            versions.append(
                DatasetVersion(tags=item["tags"], as_of=parse_datetime(item["as_of"]))
            )
        return versions

    def sync_version_tags(
        self, dataset_id: str, versions: Iterable[DatasetVersion]
    ) -> None:
        """Refresh mutable tag pointers without rewriting immutable snapshots."""
        head, expected_version = self._read_head_for_update(dataset_id)
        if head is None:
            return
        tags_by_time = {version.as_of.isoformat(): version.tags for version in versions}
        changed = False
        for item in head["versions"]:
            as_of = item["as_of"]
            if as_of in tags_by_time and item["tags"] != tags_by_time[as_of]:
                item["tags"] = tags_by_time[as_of]
                changed = True
        if changed:
            self._store.put_text_if_version(
                _head_key(dataset_id), _json_text(head), expected_version
            )

    def read_examples(
        self,
        name_or_id: str,
        *,
        as_of: str | None = None,
        include_attachments: bool = False,
    ) -> list[Example]:
        head = self._resolve_head(name_or_id)
        manifest = self._resolve_manifest(head, as_of)
        return self._read_examples(manifest, include_attachments=include_attachments)

    def read_example(
        self,
        example_id: str,
        *,
        as_of: str | None = None,
        include_attachments: bool = False,
    ) -> Example:
        matches: list[Example] = []
        for head in self._list_heads():
            manifest = self._resolve_manifest(head, as_of)
            for example in self._read_examples(
                manifest, include_attachments=include_attachments
            ):
                if str(example.id) == example_id:
                    matches.append(example)
        if not matches:
            raise DatasetReplicaNotFoundError(f"Example not found: {example_id}")
        if len(matches) > 1:
            raise DatasetReplicaAmbiguousError(
                f"Example ID exists in multiple dataset replicas: {example_id}"
            )
        return matches[0]

    def statuses(self) -> list[ReplicaStatus]:
        return [
            ReplicaStatus(
                dataset_id=head["dataset_id"],
                dataset_name=head["dataset_name"],
                latest_as_of=parse_datetime(head["latest_as_of"]),
                versions=len(head["versions"]),
                source_uri=self.base_uri,
            )
            for head in self._list_heads()
        ]

    def _list_heads(self) -> list[DatasetHeadPayload]:
        return [
            _parse_head(self._store.get_text(key))
            for key in self._store.list_keys(HEADS_PREFIX)
            if key.endswith(".json")
        ]

    def _resolve_head(self, name_or_id: str) -> DatasetHeadPayload:
        heads = [
            head
            for head in self._list_heads()
            if head["dataset_id"] == name_or_id or head["dataset_name"] == name_or_id
        ]
        if not heads:
            raise DatasetReplicaNotFoundError(f"Dataset not found: {name_or_id}")
        if len(heads) > 1:
            raise DatasetReplicaAmbiguousError(
                f"Dataset name is ambiguous; use an ID: {name_or_id}"
            )
        return heads[0]

    def _resolve_manifest(
        self, head: DatasetHeadPayload, as_of: str | None
    ) -> SnapshotManifestPayload:
        if as_of is None or as_of == "latest":
            selected = head["versions"][-1]
            return self._read_manifest(selected["manifest_key"])

        tag_matches: list[SnapshotManifestPayload] = []
        try:
            requested_time = parse_datetime(as_of)
        except ValueError:
            requested_time = None
        for item in reversed(head["versions"]):
            manifest = self._read_manifest(item["manifest_key"])
            tags = item["tags"]
            if tags is not None and as_of in tags:
                tag_matches.append(manifest)
            if (
                requested_time is not None
                and parse_datetime(item["as_of"]) <= requested_time
            ):
                return manifest
        if len(tag_matches) == 1:
            return tag_matches[0]
        if len(tag_matches) > 1:
            raise DatasetReplicaAmbiguousError(f"Version tag is ambiguous: {as_of}")
        raise DatasetReplicaNotFoundError(f"Dataset version not found: {as_of}")

    def _read_dataset_from_head(self, head: DatasetHeadPayload) -> Dataset:
        return self._read_dataset(self._resolve_manifest(head, None))

    def _read_dataset(self, manifest: SnapshotManifestPayload) -> Dataset:
        from langsmith.schemas import Dataset

        with tempfile.TemporaryDirectory(prefix="langsmith-dataset-read-") as raw:
            path = Path(raw) / "dataset.parquet"
            self._store.get_file(manifest["dataset_key"], path)
            with archive_duckdb_connection() as connection:
                row = connection.execute(
                    "SELECT payload_json FROM read_parquet(?)", [str(path)]
                ).fetchone()
        if row is None:
            raise DatasetReplicaError("Dataset Parquet contains no row")
        return Dataset(**_parse_dataset_payload(row[0]))

    def _read_examples(
        self,
        manifest: SnapshotManifestPayload,
        *,
        include_attachments: bool,
    ) -> list[Example]:
        from langsmith.schemas import Example

        with tempfile.TemporaryDirectory(prefix="langsmith-examples-read-") as raw:
            path = Path(raw) / "examples.parquet"
            self._store.get_file(manifest["examples_key"], path)
            with archive_duckdb_connection() as connection:
                rows = connection.execute(
                    "SELECT payload_json, attachments_json "
                    "FROM read_parquet(?) ORDER BY created_at, id",
                    [str(path)],
                ).fetchall()
        result: list[Example] = []
        for payload_text, attachments_text in rows:
            payload = _parse_example_payload(payload_text)
            attachments_payload = _parse_attachments(attachments_text)
            attachments = None
            if include_attachments and attachments_payload:
                attachments = {
                    item["name"]: {
                        "presigned_url": self._store.object_uri(
                            f"{BLOBS_PREFIX}/{item['digest']}"
                        ),
                        "reader": io.BytesIO(
                            self._store.get_bytes(f"{BLOBS_PREFIX}/{item['digest']}")
                        ),
                        "mime_type": item["mime_type"],
                    }
                    for item in attachments_payload
                }
            result.append(Example(**payload, attachments=attachments))
        return result

    def _read_manifest(self, key: str) -> SnapshotManifestPayload:
        return _parse_manifest(self._store.get_text(key))

    def _read_head_for_update(
        self, dataset_id: str
    ) -> tuple[DatasetHeadPayload | None, str | None]:
        key = _head_key(dataset_id)
        if not self._store.exists(key):
            return None, None
        stored = self._store.get_text_with_version(key)
        return _parse_head(stored.content), stored.version


def _serialize_dataset(dataset: Dataset) -> DatasetPayload:
    data_type = dataset.data_type.value if dataset.data_type is not None else None
    transformations = (
        [_serialize_transformation(item) for item in dataset.transformations]
        if dataset.transformations is not None
        else None
    )
    return {
        "name": dataset.name,
        "description": dataset.description,
        "data_type": data_type,
        "id": str(dataset.id),
        "created_at": dataset.created_at.isoformat(),
        "modified_at": datetime_text(dataset.modified_at),
        "example_count": dataset.example_count,
        "session_count": dataset.session_count,
        "last_session_start_time": datetime_text(dataset.last_session_start_time),
        "inputs_schema": dataset.inputs_schema,
        "outputs_schema": dataset.outputs_schema,
        "transformations": transformations,
        "metadata": dataset.metadata,
    }


def _serialize_transformation(
    transformation: DatasetTransformation,
) -> DatasetTransformationPayload:
    payload: DatasetTransformationPayload = {}
    if "path" in transformation:
        payload["path"] = transformation["path"]
    if "transformation_type" in transformation:
        payload["transformation_type"] = transformation["transformation_type"]
    return payload


def _serialize_version(version: DatasetVersion) -> VersionPayload:
    return {"tags": version.tags, "as_of": version.as_of.isoformat()}


def _serialize_example(example: Example) -> SerializedExample:
    payload: ExamplePayload = {
        "id": str(example.id),
        "dataset_id": str(example.dataset_id),
        "inputs": example.inputs,
        "outputs": example.outputs,
        "metadata": example.metadata,
        "created_at": example.created_at.isoformat(),
        "modified_at": datetime_text(example.modified_at),
        "source_run_id": (
            str(example.source_run_id) if example.source_run_id is not None else None
        ),
    }
    attachments: list[AttachmentPayload] = []
    blobs: list[AttachmentBlob] = []
    if example.attachments is not None:
        for name, attachment in sorted(example.attachments.items()):
            content = attachment["reader"].read()
            if not isinstance(content, bytes):
                raise DatasetReplicaError("Attachment reader must return bytes")
            digest = hashlib.sha256(content).hexdigest()
            attachment_payload: AttachmentPayload = {
                "name": name,
                "mime_type": attachment["mime_type"],
                "digest": digest,
                "size": len(content),
            }
            attachments.append(attachment_payload)
            blobs.append(AttachmentBlob(payload=attachment_payload, content=content))
    return SerializedExample(payload=payload, attachments=attachments, blobs=blobs)


def _write_dataset_parquet(path: Path, payload: DatasetPayload) -> None:
    with archive_duckdb_connection() as connection:
        connection.execute("CREATE TABLE dataset (payload_json VARCHAR NOT NULL)")
        connection.execute("INSERT INTO dataset VALUES (?)", [_json_text(payload)])
        connection.execute(
            f"COPY dataset TO {_sql_literal(path)} " + ARCHIVE_PARQUET_COPY_OPTIONS
        )


def _write_examples_parquet(path: Path, examples: list[SerializedExample]) -> None:
    with archive_duckdb_connection() as connection:
        connection.execute(
            "CREATE TABLE examples ("
            "id UUID NOT NULL, created_at TIMESTAMPTZ NOT NULL, "
            "payload_json VARCHAR NOT NULL, attachments_json VARCHAR NOT NULL)"
        )
        for example in examples:
            connection.execute(
                "INSERT INTO examples VALUES (?, ?, ?, ?)",
                [
                    example.payload["id"],
                    example.payload["created_at"],
                    _json_text(example.payload),
                    _json_text(example.attachments),
                ],
            )
        connection.execute(
            f"COPY examples TO {_sql_literal(path)} " + ARCHIVE_PARQUET_COPY_OPTIONS
        )


def _sql_literal(path: Path) -> str:
    return "'" + str(path).replace("'", "''") + "'"


def _json_text(payload: object) -> str:
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def _head_key(dataset_id: str) -> str:
    return f"{HEADS_PREFIX}/{dataset_id}.json"


def _load_object(text: str, kind: str) -> dict[str, Any]:
    raw = json.loads(text)
    if not isinstance(raw, dict):
        raise DatasetReplicaError(f"{kind} must be a JSON object")
    return raw


def _parse_head(text: str) -> DatasetHeadPayload:
    raw = _load_object(text, "Dataset head")
    if raw["schema_version"] != REPLICA_SCHEMA_VERSION:
        raise DatasetReplicaError("Unsupported dataset replica schema")
    return cast(DatasetHeadPayload, raw)


def _parse_manifest(text: str) -> SnapshotManifestPayload:
    raw = _load_object(text, "Dataset manifest")
    if raw["schema_version"] != REPLICA_SCHEMA_VERSION:
        raise DatasetReplicaError("Unsupported dataset replica schema")
    return cast(SnapshotManifestPayload, raw)


def _parse_dataset_payload(text: str) -> DatasetPayload:
    return cast(DatasetPayload, _load_object(text, "Dataset payload"))


def _parse_example_payload(text: str) -> ExamplePayload:
    return cast(ExamplePayload, _load_object(text, "Example payload"))


def _parse_attachments(text: str) -> list[AttachmentPayload]:
    raw = json.loads(text)
    if not isinstance(raw, list):
        raise DatasetReplicaError("Attachment payload must be a JSON array")
    return cast(list[AttachmentPayload], raw)
