"""Atomic Parquet publication and strict SDK-model reads for dataset replicas."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import tempfile
from typing import TYPE_CHECKING, Any, cast
from uuid import uuid4

from langsmith_cli.archive.duckdb import (
    ARCHIVE_PARQUET_COPY_OPTIONS,
    archive_duckdb_connection,
)
from langsmith_cli.archive.storage import ArchiveStore, ConcurrentArchiveWriteError
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
MAX_HEAD_PUBLICATION_ATTEMPTS = 16
DUCKDB_INSERT_BATCH_SIZE = 10_000
HEAD_KEYS = frozenset(
    {"schema_version", "dataset_id", "dataset_name", "latest_as_of", "versions"}
)
HEAD_VERSION_KEYS = frozenset({"as_of", "manifest_key", "tags"})
MANIFEST_KEYS = frozenset(
    {
        "schema_version",
        "dataset_id",
        "dataset_name",
        "version",
        "dataset_key",
        "dataset_sha256",
        "examples_key",
        "examples_sha256",
        "content_digest",
        "example_count",
        "attachment_count",
        "published_at",
    }
)
VERSION_KEYS = frozenset({"as_of", "tags"})
DATASET_PAYLOAD_KEYS = frozenset(
    {
        "name",
        "description",
        "data_type",
        "id",
        "created_at",
        "modified_at",
        "example_count",
        "session_count",
        "last_session_start_time",
        "inputs_schema",
        "outputs_schema",
        "transformations",
        "metadata",
    }
)
EXAMPLE_PAYLOAD_KEYS = frozenset(
    {
        "id",
        "dataset_id",
        "inputs",
        "outputs",
        "metadata",
        "created_at",
        "modified_at",
        "source_run_id",
    }
)
ATTACHMENT_KEYS = frozenset({"name", "mime_type", "digest", "size"})

DATASET_SDK_FIELDS = frozenset(
    {
        "created_at",
        "data_type",
        "description",
        "example_count",
        "id",
        "inputs_schema",
        "last_session_start_time",
        "metadata",
        "modified_at",
        "name",
        "outputs_schema",
        "session_count",
        "transformations",
    }
)
EXAMPLE_SDK_FIELDS = frozenset(
    {
        "attachments",
        "created_at",
        "dataset_id",
        "id",
        "inputs",
        "metadata",
        "modified_at",
        "outputs",
        "source_run_id",
    }
)
DATASET_VERSION_SDK_FIELDS = frozenset({"as_of", "tags"})
DATASET_TRANSFORMATION_SDK_FIELDS = frozenset({"path", "transformation_type"})


class DatasetReplicaError(RuntimeError):
    """Base error for malformed or ambiguous replicas."""


class DatasetReplicaNotFoundError(DatasetReplicaError):
    """A requested dataset, version, or example is absent."""


class DatasetReplicaAmbiguousError(DatasetReplicaError):
    """A name resolves to multiple stable dataset identities."""


class DatasetReplicaConflictError(DatasetReplicaError):
    """One immutable dataset version was supplied with different content."""


class DatasetReplicaSchemaError(DatasetReplicaError):
    """The installed LangSmith SDK no longer matches the replica contract."""


class DatasetReplicaIntegrityError(DatasetReplicaError):
    """Published replica bytes do not match their immutable manifest."""


class DatasetReplicaConfigurationError(DatasetReplicaError):
    """A replica location is missing required local/archive configuration."""


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
        _assert_sdk_contract()
        dataset_payload = _serialize_dataset(dataset)
        version_payload = _serialize_version(version)
        dataset_id = dataset_payload["id"]
        serialized_examples = [_serialize_example(example) for example in examples]
        _validate_example_membership(dataset_id, serialized_examples)
        content_digest = _snapshot_content_digest(dataset_payload, serialized_examples)

        # Fast idempotence path. Content is serialized and hashed before this
        # check: `(dataset_id, as_of)` is an immutable identity, not merely a
        # convenient object-store prefix.
        existing = self._publish_head(
            dataset_payload=dataset_payload,
            version=version,
            version_payload=version_payload,
            manifest_key=None,
            content_digest=content_digest,
            example_count=len(serialized_examples),
            attachment_count=sum(
                len(example.attachments) for example in serialized_examples
            ),
        )
        if existing is not None:
            return existing

        version_token = hashlib.sha256(
            version_payload["as_of"].encode("utf-8")
        ).hexdigest()
        version_prefix = f"datasets/{dataset_id}/versions/{version_token}"

        with tempfile.TemporaryDirectory(prefix="langsmith-dataset-replica-") as raw:
            staging = Path(raw)
            dataset_path = staging / "dataset.parquet"
            examples_path = staging / "examples.parquet"
            _write_dataset_parquet(dataset_path, dataset_payload)
            _write_examples_parquet(examples_path, serialized_examples)
            dataset_sha256 = _file_sha256(dataset_path)
            examples_sha256 = _file_sha256(examples_path)
            dataset_key = f"{version_prefix}/objects/dataset-{dataset_sha256}.parquet"
            examples_key = (
                f"{version_prefix}/objects/examples-{examples_sha256}.parquet"
            )
            # Content-addressed objects are immutable. Reusing an existing key is
            # safe because equal SHA-256 keys imply equal publication bytes.
            self._put_file_if_absent_verified(
                dataset_key,
                dataset_path,
                dataset_sha256,
                staging / "existing-dataset.parquet",
            )
            self._put_file_if_absent_verified(
                examples_key,
                examples_path,
                examples_sha256,
                staging / "existing-examples.parquet",
            )

        unique_blobs: dict[str, AttachmentBlob] = {}
        for example in serialized_examples:
            for blob in example.blobs:
                unique_blobs[blob.payload["digest"]] = blob
        for digest, blob in unique_blobs.items():
            blob_key = f"{BLOBS_PREFIX}/{digest}"
            if self._store.exists(blob_key):
                existing_content = self._store.get_bytes(blob_key)
                if _bytes_sha256(existing_content) != digest:
                    raise DatasetReplicaIntegrityError(
                        f"Replica attachment digest mismatch: {blob_key}"
                    )
            else:
                self._store.put_bytes(blob_key, blob.content)

        attachment_count = sum(
            len(example.attachments) for example in serialized_examples
        )
        # Manifests are immutable and uniquely staged. Only the head CAS below
        # makes one complete manifest reachable; losing writers leave harmless
        # unreachable objects rather than mutating a winner's snapshot.
        manifest_key = f"{version_prefix}/manifests/{uuid4().hex}.json"
        manifest: SnapshotManifestPayload = {
            "schema_version": REPLICA_SCHEMA_VERSION,
            "dataset_id": dataset_id,
            "dataset_name": dataset_payload["name"],
            "version": version_payload,
            "dataset_key": dataset_key,
            "dataset_sha256": dataset_sha256,
            "examples_key": examples_key,
            "examples_sha256": examples_sha256,
            "content_digest": content_digest,
            "example_count": len(serialized_examples),
            "attachment_count": attachment_count,
            "published_at": datetime.now(timezone.utc).isoformat(),
        }
        self._store.put_text(manifest_key, _json_text(manifest))
        result = self._publish_head(
            dataset_payload=dataset_payload,
            version=version,
            version_payload=version_payload,
            manifest_key=manifest_key,
            content_digest=content_digest,
            example_count=len(serialized_examples),
            attachment_count=attachment_count,
        )
        if result is None:
            raise DatasetReplicaError("Dataset head publication made no progress")
        return result

    def _publish_head(
        self,
        *,
        dataset_payload: DatasetPayload,
        version: DatasetVersion,
        version_payload: VersionPayload,
        manifest_key: str | None,
        content_digest: str,
        example_count: int,
        attachment_count: int,
    ) -> ReplicaWriteResult | None:
        """Publish or validate one immutable version through a bounded CAS loop.

        INVARIANT: the head is the sole reachability boundary. Writers may stage
        immutable objects concurrently, but every retry rereads and merges the
        newest head before attempting compare-and-swap publication.
        """
        dataset_id = dataset_payload["id"]
        for _attempt in range(MAX_HEAD_PUBLICATION_ATTEMPTS):
            old_head, expected_version = self._read_head_for_update(dataset_id)
            if old_head is not None:
                for item in old_head["versions"]:
                    if item["as_of"] != version_payload["as_of"]:
                        continue
                    existing_manifest = self._read_manifest_for_head(old_head, item)
                    if existing_manifest["content_digest"] != content_digest:
                        raise DatasetReplicaConflictError(
                            "Dataset version already exists with different content: "
                            f"{dataset_id} at {version_payload['as_of']}"
                        )
                    if (
                        item["tags"] != version_payload["tags"]
                        or old_head["dataset_name"] != dataset_payload["name"]
                    ):
                        versions = [dict(value) for value in old_head["versions"]]
                        for updated_item in versions:
                            if updated_item["as_of"] == version_payload["as_of"]:
                                updated_item["tags"] = version_payload["tags"]
                        updated_head: DatasetHeadPayload = {
                            **old_head,
                            "dataset_name": dataset_payload["name"],
                            "versions": cast(list[HeadVersionPayload], versions),
                        }
                        try:
                            self._store.put_text_if_version(
                                _head_key(dataset_id),
                                _json_text(updated_head),
                                expected_version,
                            )
                        except ConcurrentArchiveWriteError:
                            continue
                    return ReplicaWriteResult(
                        dataset_id=dataset_id,
                        dataset_name=dataset_payload["name"],
                        as_of=version.as_of,
                        example_count=existing_manifest["example_count"],
                        attachment_count=existing_manifest["attachment_count"],
                        already_present=True,
                        destination_uri=self.base_uri,
                    )

            if manifest_key is None:
                return None
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
            try:
                self._store.put_text_if_version(
                    _head_key(dataset_id), _json_text(head), expected_version
                )
            except ConcurrentArchiveWriteError:
                continue
            return ReplicaWriteResult(
                dataset_id=dataset_id,
                dataset_name=dataset_payload["name"],
                as_of=version.as_of,
                example_count=example_count,
                attachment_count=attachment_count,
                already_present=False,
                destination_uri=self.base_uri,
            )
        raise DatasetReplicaError(
            f"Dataset head stayed busy after {MAX_HEAD_PUBLICATION_ATTEMPTS} attempts: "
            f"{dataset_id}"
        )

    def _put_file_if_absent_verified(
        self,
        key: str,
        source: Path,
        expected_sha256: str,
        verification_path: Path,
    ) -> None:
        if not self._store.exists(key):
            self._store.put_file(key, source)
            return
        self._store.get_file(key, verification_path)
        if _file_sha256(verification_path) != expected_sha256:
            raise DatasetReplicaIntegrityError(f"Replica object digest mismatch: {key}")

    def list_datasets(self) -> list[Dataset]:
        return [self._read_dataset_from_head(head) for head in self._list_heads()]

    def read_dataset(self, name_or_id: str, as_of: str | None = None) -> Dataset:
        head = self._resolve_head(name_or_id)
        manifest = self._resolve_manifest(head, as_of)
        dataset = self._read_dataset(manifest)
        # Dataset names are mutable lookup labels in LangSmith, not replica
        # identities. The head carries the newest label for every exact version.
        return dataset.model_copy(update={"name": head["dataset_name"]})

    def list_versions(self, name_or_id: str) -> list[DatasetVersion]:
        from langsmith.schemas import DatasetVersion

        _assert_sdk_contract()
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
        tags_by_time = {version.as_of.isoformat(): version.tags for version in versions}
        for _attempt in range(MAX_HEAD_PUBLICATION_ATTEMPTS):
            head, expected_version = self._read_head_for_update(dataset_id)
            if head is None:
                return
            changed = False
            for item in head["versions"]:
                as_of = item["as_of"]
                if as_of in tags_by_time and item["tags"] != tags_by_time[as_of]:
                    item["tags"] = tags_by_time[as_of]
                    changed = True
            if not changed:
                return
            try:
                self._store.put_text_if_version(
                    _head_key(dataset_id), _json_text(head), expected_version
                )
            except ConcurrentArchiveWriteError:
                # Tags are mutable pointers, but they must never overwrite a
                # concurrently appended immutable version. Reread and merge.
                continue
            return
        raise DatasetReplicaError(
            f"Dataset tag head stayed busy after {MAX_HEAD_PUBLICATION_ATTEMPTS} "
            f"attempts: {dataset_id}"
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
            try:
                manifest = self._resolve_manifest(head, as_of)
            except DatasetReplicaNotFoundError:
                # A global example ID lookup spans independent dataset histories.
                # Absence of the requested version in one dataset says nothing
                # about whether another dataset contains the requested example.
                continue
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
            return self._read_manifest_for_head(head, selected)

        tag_matches: list[SnapshotManifestPayload] = []
        try:
            requested_time = parse_datetime(as_of)
        except ValueError:
            requested_time = None
        for item in reversed(head["versions"]):
            manifest = self._read_manifest_for_head(head, item)
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
        dataset = self._read_dataset(self._resolve_manifest(head, None))
        return dataset.model_copy(update={"name": head["dataset_name"]})

    def _read_dataset(self, manifest: SnapshotManifestPayload) -> Dataset:
        from langsmith.schemas import Dataset

        _assert_sdk_contract()
        with tempfile.TemporaryDirectory(prefix="langsmith-dataset-read-") as raw:
            path = Path(raw) / "dataset.parquet"
            self._download_verified(
                manifest["dataset_key"], manifest["dataset_sha256"], path
            )
            with archive_duckdb_connection() as connection:
                rows = connection.execute(
                    "SELECT payload_json FROM read_parquet(?)", [str(path)]
                ).fetchall()
        if len(rows) != 1:
            raise DatasetReplicaIntegrityError(
                f"Dataset Parquet must contain exactly one row: {manifest['dataset_key']}"
            )
        payload = _parse_dataset_payload(rows[0][0])
        if payload["id"] != manifest["dataset_id"]:
            raise DatasetReplicaIntegrityError(
                "Dataset Parquet ID does not match its manifest dataset ID"
            )
        if payload["name"] != manifest["dataset_name"]:
            raise DatasetReplicaIntegrityError(
                "Dataset Parquet name does not match its immutable manifest"
            )
        return Dataset(**payload)

    def _read_examples(
        self,
        manifest: SnapshotManifestPayload,
        *,
        include_attachments: bool,
    ) -> list[Example]:
        from langsmith.schemas import Example

        _assert_sdk_contract()
        with tempfile.TemporaryDirectory(prefix="langsmith-examples-read-") as raw:
            path = Path(raw) / "examples.parquet"
            self._download_verified(
                manifest["examples_key"], manifest["examples_sha256"], path
            )
            with archive_duckdb_connection() as connection:
                rows = connection.execute(
                    "SELECT payload_json, attachments_json "
                    "FROM read_parquet(?) ORDER BY created_at, id",
                    [str(path)],
                ).fetchall()
        result: list[Example] = []
        attachment_count = 0
        for payload_text, attachments_text in rows:
            payload = _parse_example_payload(payload_text)
            if payload["dataset_id"] != manifest["dataset_id"]:
                raise DatasetReplicaIntegrityError(
                    "Example dataset ID does not match its manifest dataset ID"
                )
            attachments_payload = _parse_attachments(attachments_text)
            attachment_count += len(attachments_payload)
            attachments = None
            if include_attachments and attachments_payload:
                attachments = {
                    item["name"]: {
                        "presigned_url": self._store.object_uri(
                            f"{BLOBS_PREFIX}/{item['digest']}"
                        ),
                        "reader": io.BytesIO(self._read_attachment(item)),
                        "mime_type": item["mime_type"],
                    }
                    for item in attachments_payload
                }
            result.append(Example(**payload, attachments=attachments))
        if len(result) != manifest["example_count"]:
            raise DatasetReplicaIntegrityError(
                "Replica example count does not match its manifest"
            )
        if attachment_count != manifest["attachment_count"]:
            raise DatasetReplicaIntegrityError(
                "Replica attachment count does not match its manifest"
            )
        return result

    def _download_verified(
        self, key: str, expected_sha256: str, destination: Path
    ) -> None:
        self._store.get_file(key, destination)
        actual_sha256 = _file_sha256(destination)
        if actual_sha256 != expected_sha256:
            raise DatasetReplicaIntegrityError(f"Replica object digest mismatch: {key}")

    def _read_attachment(self, payload: AttachmentPayload) -> bytes:
        key = f"{BLOBS_PREFIX}/{payload['digest']}"
        content = self._store.get_bytes(key)
        if (
            len(content) != payload["size"]
            or _bytes_sha256(content) != payload["digest"]
        ):
            raise DatasetReplicaIntegrityError(
                f"Replica attachment digest mismatch: {key}"
            )
        return content

    def _read_manifest(self, key: str) -> SnapshotManifestPayload:
        return _parse_manifest(self._store.get_text(key))

    def _read_manifest_for_head(
        self, head: DatasetHeadPayload, item: HeadVersionPayload
    ) -> SnapshotManifestPayload:
        manifest = self._read_manifest(item["manifest_key"])
        if manifest["dataset_id"] != head["dataset_id"]:
            raise DatasetReplicaIntegrityError(
                "Dataset head references a manifest with a different dataset ID"
            )
        if manifest["version"]["as_of"] != item["as_of"]:
            raise DatasetReplicaIntegrityError(
                "Dataset head version does not match its manifest version"
            )
        return manifest

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


def _assert_sdk_contract() -> None:
    from langsmith.schemas import (
        Dataset,
        DatasetTransformation,
        DatasetVersion,
        Example,
    )

    _assert_sdk_fields("Dataset", Dataset.model_fields, DATASET_SDK_FIELDS)
    _assert_sdk_fields("Example", Example.model_fields, EXAMPLE_SDK_FIELDS)
    _assert_sdk_fields(
        "DatasetVersion", DatasetVersion.model_fields, DATASET_VERSION_SDK_FIELDS
    )
    transformation_fields = (
        DatasetTransformation.__required_keys__
        | DatasetTransformation.__optional_keys__
    )
    if transformation_fields != DATASET_TRANSFORMATION_SDK_FIELDS:
        raise DatasetReplicaSchemaError(
            "DatasetTransformation SDK fields changed; update the replica schema "
            "explicitly"
        )


def _assert_sdk_fields(
    model_name: str, model_fields: Mapping[str, object], expected: frozenset[str]
) -> None:
    actual = frozenset(model_fields)
    if actual != expected:
        added = sorted(actual - expected)
        removed = sorted(expected - actual)
        raise DatasetReplicaSchemaError(
            f"{model_name} SDK fields changed; update the replica schema explicitly "
            f"(added={added}, removed={removed})"
        )


def _validate_example_membership(
    dataset_id: str, examples: list[SerializedExample]
) -> None:
    seen: set[str] = set()
    for example in examples:
        example_id = example.payload["id"]
        if example.payload["dataset_id"] != dataset_id:
            raise DatasetReplicaConflictError(
                f"Example {example_id} belongs to dataset "
                f"{example.payload['dataset_id']}, not {dataset_id}"
            )
        if example_id in seen:
            raise DatasetReplicaConflictError(
                f"Snapshot contains duplicate example ID: {example_id}"
            )
        seen.add(example_id)


def _snapshot_content_digest(
    dataset: DatasetPayload, examples: list[SerializedExample]
) -> str:
    # Names are mutable lookup labels. Stable Dataset ID plus the remaining
    # strict Dataset fields and exact examples define immutable version content.
    canonical_dataset = {key: value for key, value in dataset.items() if key != "name"}
    canonical_examples = sorted(
        (
            {"payload": example.payload, "attachments": example.attachments}
            for example in examples
        ),
        key=lambda item: item["payload"]["id"],
    )
    return _bytes_sha256(
        _json_text(
            {"dataset": canonical_dataset, "examples": canonical_examples}
        ).encode("utf-8")
    )


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
        ordered = sorted(
            examples,
            key=lambda item: (item.payload["created_at"], item.payload["id"]),
        )
        for start in range(0, len(ordered), DUCKDB_INSERT_BATCH_SIZE):
            batch = ordered[start : start + DUCKDB_INSERT_BATCH_SIZE]
            # Bind one typed array per column to avoid one Python↔DuckDB call per
            # Example while bounding each parameter batch's memory footprint.
            connection.execute(
                "INSERT INTO examples SELECT "
                "unnest(?::UUID[]), unnest(?::TIMESTAMPTZ[]), "
                "unnest(?::VARCHAR[]), unnest(?::VARCHAR[])",
                [
                    [example.payload["id"] for example in batch],
                    [example.payload["created_at"] for example in batch],
                    [_json_text(example.payload) for example in batch],
                    [_json_text(example.attachments) for example in batch],
                ],
            )
        connection.execute(
            f"COPY examples TO {_sql_literal(path)} " + ARCHIVE_PARQUET_COPY_OPTIONS
        )


def _sql_literal(path: Path) -> str:
    return "'" + str(path).replace("'", "''") + "'"


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _bytes_sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


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
    _require_exact_keys(raw, HEAD_KEYS, "Dataset head")
    if raw["schema_version"] != REPLICA_SCHEMA_VERSION:
        raise DatasetReplicaError("Unsupported dataset replica schema")
    _require_string(raw["dataset_id"], "Dataset head dataset_id")
    _require_string(raw["dataset_name"], "Dataset head dataset_name")
    latest_as_of = _require_datetime_text(
        raw["latest_as_of"], "Dataset head latest_as_of"
    )
    raw_versions = raw["versions"]
    if not isinstance(raw_versions, list) or not raw_versions:
        raise DatasetReplicaSchemaError(
            "Dataset head must contain at least one version"
        )
    parsed_versions: list[HeadVersionPayload] = []
    version_times: list[datetime] = []
    for raw_version in raw_versions:
        if not isinstance(raw_version, dict):
            raise DatasetReplicaSchemaError(
                "Dataset head version must be a JSON object"
            )
        _require_exact_keys(raw_version, HEAD_VERSION_KEYS, "Dataset head version")
        as_of = _require_datetime_text(
            raw_version["as_of"], "Dataset head version as_of"
        )
        _require_string(
            raw_version["manifest_key"], "Dataset head version manifest_key"
        )
        _require_tags(raw_version["tags"], "Dataset head version tags")
        parsed_versions.append(cast(HeadVersionPayload, raw_version))
        version_times.append(as_of)
    if version_times != sorted(version_times) or len(set(version_times)) != len(
        version_times
    ):
        raise DatasetReplicaSchemaError(
            "Dataset head versions must be unique and sorted by as_of"
        )
    if latest_as_of != version_times[-1]:
        raise DatasetReplicaSchemaError(
            "Dataset head latest_as_of must equal its newest version"
        )
    raw["versions"] = parsed_versions
    return cast(DatasetHeadPayload, raw)


def _parse_manifest(text: str) -> SnapshotManifestPayload:
    raw = _load_object(text, "Dataset manifest")
    _require_exact_keys(raw, MANIFEST_KEYS, "Dataset manifest")
    if raw["schema_version"] != REPLICA_SCHEMA_VERSION:
        raise DatasetReplicaError("Unsupported dataset replica schema")
    _require_string(raw["dataset_id"], "Dataset manifest dataset_id")
    _require_string(raw["dataset_name"], "Dataset manifest dataset_name")
    raw_version = raw["version"]
    if not isinstance(raw_version, dict):
        raise DatasetReplicaSchemaError("Dataset manifest version must be an object")
    _require_exact_keys(raw_version, VERSION_KEYS, "Dataset manifest version")
    _require_datetime_text(raw_version["as_of"], "Dataset manifest version as_of")
    _require_tags(raw_version["tags"], "Dataset manifest version tags")
    for field in ("dataset_key", "examples_key"):
        _require_string(raw[field], f"Dataset manifest {field}")
    for field in ("dataset_sha256", "examples_sha256", "content_digest"):
        _require_sha256(raw[field], f"Dataset manifest {field}")
    for field in ("example_count", "attachment_count"):
        value = raw[field]
        if type(value) is not int or value < 0:
            raise DatasetReplicaSchemaError(
                f"Dataset manifest {field} must be a non-negative integer"
            )
    _require_datetime_text(raw["published_at"], "Dataset manifest published_at")
    return cast(SnapshotManifestPayload, raw)


def _parse_dataset_payload(text: str) -> DatasetPayload:
    raw = _load_object(text, "Dataset payload")
    _require_exact_keys(raw, DATASET_PAYLOAD_KEYS, "Dataset payload")
    return cast(DatasetPayload, raw)


def _parse_example_payload(text: str) -> ExamplePayload:
    raw = _load_object(text, "Example payload")
    _require_exact_keys(raw, EXAMPLE_PAYLOAD_KEYS, "Example payload")
    return cast(ExamplePayload, raw)


def _parse_attachments(text: str) -> list[AttachmentPayload]:
    raw = json.loads(text)
    if not isinstance(raw, list):
        raise DatasetReplicaError("Attachment payload must be a JSON array")
    parsed: list[AttachmentPayload] = []
    for item in raw:
        if not isinstance(item, dict):
            raise DatasetReplicaSchemaError("Attachment entry must be a JSON object")
        _require_exact_keys(item, ATTACHMENT_KEYS, "Attachment entry")
        _require_string(item["name"], "Attachment name")
        mime_type = item["mime_type"]
        if mime_type is not None:
            _require_string(mime_type, "Attachment mime_type")
        _require_sha256(item["digest"], "Attachment digest")
        size = item["size"]
        if type(size) is not int or size < 0:
            raise DatasetReplicaSchemaError(
                "Attachment size must be a non-negative integer"
            )
        parsed.append(cast(AttachmentPayload, item))
    return parsed


def _require_exact_keys(
    raw: Mapping[str, object], expected: frozenset[str], kind: str
) -> None:
    actual = frozenset(raw)
    if actual != expected:
        raise DatasetReplicaSchemaError(
            f"{kind} fields changed "
            f"(missing={sorted(expected - actual)}, extra={sorted(actual - expected)})"
        )


def _require_string(value: object, kind: str) -> str:
    if not isinstance(value, str) or not value:
        raise DatasetReplicaSchemaError(f"{kind} must be a non-empty string")
    return value


def _require_datetime_text(value: object, kind: str) -> datetime:
    text = _require_string(value, kind)
    try:
        return parse_datetime(text)
    except ValueError as exc:
        raise DatasetReplicaSchemaError(f"{kind} must be an ISO timestamp") from exc


def _require_tags(value: object, kind: str) -> None:
    if value is None:
        return
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise DatasetReplicaSchemaError(f"{kind} must be a string array or null")


def _require_sha256(value: object, kind: str) -> str:
    text = _require_string(value, kind)
    if len(text) != 64:
        raise DatasetReplicaSchemaError(f"{kind} must be a SHA-256 hex digest")
    try:
        int(text, 16)
    except ValueError as exc:
        raise DatasetReplicaSchemaError(f"{kind} must be a SHA-256 hex digest") from exc
    return text
