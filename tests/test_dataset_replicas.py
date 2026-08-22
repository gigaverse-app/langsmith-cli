"""End-to-end invariants for exact, read-only dataset replicas."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
from pathlib import Path
from threading import Barrier
from unittest.mock import MagicMock, patch
from uuid import UUID

import pytest
from langsmith.schemas import Dataset, DatasetVersion, Example

from langsmith_cli.archive.storage import create_store
from langsmith_cli.dataset_replica.models import ReplicaDestination, ReplicaSource
from langsmith_cli.dataset_replica.repository import (
    DatasetReplicaAmbiguousError,
    DatasetReplicaConflictError,
    DatasetReplicaConfigurationError,
    DatasetReplicaError,
    DatasetReplicaIntegrityError,
    DatasetReplicaRepository,
    DatasetReplicaSchemaError,
    _parse_attachments,
    _parse_head,
    _parse_manifest,
)
from langsmith_cli.dataset_replica.service import (
    _select_version,
    default_local_dataset_directory,
    pull_dataset,
    repository_for,
    resolve_cloud_version,
)
from langsmith_cli.main import cli


DATASET_ID = UUID("ae99b6fa-a6db-4f1c-8868-bc6764f4c29e")
EXAMPLE_ID = UUID("3442bd7c-27a2-437a-a38c-f278e455d87b")
SECOND_EXAMPLE_ID = UUID("05da0305-224c-4b3c-9662-671146ee94a5")
VERSION_ONE = datetime(2026, 8, 20, 10, tzinfo=timezone.utc)
VERSION_TWO = VERSION_ONE + timedelta(days=1)


def replica_dataset() -> Dataset:
    return Dataset(
        id=DATASET_ID,
        name="evaluation-set",
        description="Strict replica fixture",
        data_type="kv",
        created_at=VERSION_ONE - timedelta(days=1),
        modified_at=VERSION_TWO,
        example_count=2,
        session_count=3,
        last_session_start_time=VERSION_TWO,
        inputs_schema={"type": "object", "required": ["question"]},
        outputs_schema={"type": "object", "required": ["answer"]},
        transformations=[
            {"path": ["inputs"], "transformation_type": "remove_extra_fields"}
        ],
        metadata={"owner": "evals"},
    )


def replica_example(
    example_id: UUID = EXAMPLE_ID,
    *,
    dataset_id: UUID = DATASET_ID,
    input_value: str = "why?",
    attachment: bytes | None = b"diagram-bytes",
) -> Example:
    attachments = None
    if attachment is not None:
        attachments = {
            "diagram.png": {
                "presigned_url": "https://cloud.invalid/expiring",
                "reader": io.BytesIO(attachment),
                "mime_type": "image/png",
            }
        }
    return Example(
        id=example_id,
        dataset_id=dataset_id,
        inputs={"question": input_value},
        outputs={"answer": "because"},
        metadata={"dataset_split": ["test"], "difficulty": "hard"},
        created_at=VERSION_ONE - timedelta(hours=1),
        modified_at=VERSION_ONE,
        source_run_id=UUID("6db22a4b-bbb7-45cb-90af-33d4f08a1870"),
        attachments=attachments,
    )


def _replace_manifest_and_head_digest(
    store, head_key: str, manifest_index: int, manifest: dict
) -> None:
    """Test helper for reaching validation below the authenticated manifest."""
    head = json.loads(store.get_text(head_key))
    manifest_key = head["versions"][manifest_index]["manifest_key"]
    manifest_text = json.dumps(manifest, sort_keys=True, separators=(",", ":"))
    store.put_text(manifest_key, manifest_text)
    head["versions"][manifest_index]["manifest_sha256"] = hashlib.sha256(
        manifest_text.encode("utf-8")
    ).hexdigest()
    store.put_text(head_key, json.dumps(head, sort_keys=True, separators=(",", ":")))


def test_repository_round_trips_sdk_models_and_attachment_bytes(tmp_path: Path):
    """INVARIANT: a replica preserves SDK fields and durable attachment bytes."""
    repository = DatasetReplicaRepository(create_store(str(tmp_path)))
    dataset = replica_dataset()
    version = DatasetVersion(tags=["baseline"], as_of=VERSION_ONE)

    result = repository.write_snapshot(dataset, version, [replica_example()])
    restored_dataset = repository.read_dataset(str(DATASET_ID))
    restored = repository.read_examples(str(DATASET_ID), include_attachments=True)[0]

    assert result.example_count == 1
    assert result.attachment_count == 1
    assert restored_dataset.model_dump(mode="json") == dataset.model_dump(mode="json")
    assert restored.model_dump(mode="json", exclude={"attachments"}) == replica_example(
        attachment=None
    ).model_dump(mode="json", exclude={"attachments"})
    assert restored.attachments is not None
    assert restored.attachments["diagram.png"]["mime_type"] == "image/png"
    assert restored.attachments["diagram.png"]["reader"].read() == b"diagram-bytes"
    assert "expiring" not in restored.attachments["diagram.png"]["presigned_url"]


def test_repository_is_idempotent_and_reads_historical_versions(tmp_path: Path):
    """INVARIANT: exact versions are immutable and latest can fast-forward."""
    repository = DatasetReplicaRepository(create_store(str(tmp_path)))
    dataset = replica_dataset()
    first = DatasetVersion(tags=["baseline"], as_of=VERSION_ONE)
    second = DatasetVersion(tags=["latest"], as_of=VERSION_TWO)

    repository.write_snapshot(
        dataset,
        first,
        [replica_example(attachment=None), replica_example(SECOND_EXAMPLE_ID)],
    )
    repeated = repository.write_snapshot(
        dataset,
        DatasetVersion(tags=["renamed-baseline"], as_of=VERSION_ONE),
        [replica_example(attachment=None), replica_example(SECOND_EXAMPLE_ID)],
    )
    repository.write_snapshot(dataset, second, [replica_example(attachment=None)])
    repository.sync_version_tags(
        str(DATASET_ID),
        [
            DatasetVersion(tags=None, as_of=VERSION_ONE),
            DatasetVersion(tags=["prod", "latest"], as_of=VERSION_TWO),
        ],
    )

    assert repeated.already_present is True
    assert len(repository.read_examples(str(DATASET_ID))) == 1
    assert (
        len(repository.read_examples(str(DATASET_ID), as_of=VERSION_ONE.isoformat()))
        == 2
    )
    versions = repository.list_versions(str(DATASET_ID))
    assert [version.as_of for version in versions] == [
        VERSION_TWO,
        VERSION_ONE,
    ]
    assert versions[0].tags == ["prod", "latest"]
    assert versions[1].tags is None
    assert len(repository.read_examples(str(DATASET_ID), as_of="prod")) == 1

    renamed = dataset.model_copy(update={"name": "renamed-evaluation-set"})
    rename_result = repository.write_snapshot(
        renamed,
        second,
        [replica_example(attachment=None)],
    )
    assert rename_result.already_present is True
    assert repository.read_dataset(str(DATASET_ID)).name == renamed.name


def test_version_selection_reads_only_the_selected_authenticated_manifest(
    tmp_path: Path,
) -> None:
    """INVARIANT: selecting one version never scans unrelated manifests."""
    store = create_store(str(tmp_path))
    repository = DatasetReplicaRepository(store)
    for offset, tag in enumerate(("oldest", "middle", "newest")):
        repository.write_snapshot(
            replica_dataset(),
            DatasetVersion(as_of=VERSION_ONE + timedelta(days=offset), tags=[tag]),
            [replica_example(input_value=tag, attachment=None)],
        )
    head = json.loads(store.get_text(f"datasets/heads/{DATASET_ID}.json"))
    selected_manifest_key = head["versions"][0]["manifest_key"]
    guarded_store = MagicMock(wraps=store)

    def read_selected_object(key: str) -> str:
        if "/manifests/" in key and key != selected_manifest_key:
            raise AssertionError("version selection read an unrelated manifest")
        return store.get_text(key)

    guarded_store.get_text.side_effect = read_selected_object

    restored = DatasetReplicaRepository(guarded_store).read_dataset(
        str(DATASET_ID), as_of="oldest"
    )

    assert restored.id == DATASET_ID


def test_same_timestamp_rejects_different_snapshot_content(tmp_path: Path) -> None:
    """INVARIANT: one dataset timestamp identifies exactly one snapshot."""
    repository = DatasetReplicaRepository(create_store(str(tmp_path)))
    version = DatasetVersion(tags=["baseline"], as_of=VERSION_ONE)
    repository.write_snapshot(
        replica_dataset(), version, [replica_example(input_value="first")]
    )

    with pytest.raises(DatasetReplicaConflictError, match="different content"):
        repository.write_snapshot(
            replica_dataset(), version, [replica_example(input_value="changed")]
        )

    restored = repository.read_examples(str(DATASET_ID))
    assert restored[0].inputs == {"question": "first"}


def test_dataset_catalog_metadata_is_mutable_outside_version_identity(
    tmp_path: Path,
) -> None:
    """INVARIANT: Dataset catalog state may change without rewriting a version."""
    repository = DatasetReplicaRepository(create_store(str(tmp_path)))
    version = DatasetVersion(tags=["prod"], as_of=VERSION_ONE)
    repository.write_snapshot(
        replica_dataset(), version, [replica_example(attachment=None)]
    )
    changed = replica_dataset().model_copy(
        update={
            "description": "Updated mutable catalog description",
            "modified_at": VERSION_TWO + timedelta(days=1),
        }
    )

    repeated = repository.write_snapshot(
        changed, version, [replica_example(attachment=None)]
    )

    assert repeated.already_present is True
    assert repository.read_dataset(str(DATASET_ID)).description == changed.description
    assert (
        repository.read_dataset(
            str(DATASET_ID), as_of=VERSION_ONE.isoformat()
        ).modified_at
        == changed.modified_at
    )
    assert repository.read_examples(str(DATASET_ID))[0].inputs == {"question": "why?"}


def test_snapshot_rejects_example_from_another_dataset(tmp_path: Path) -> None:
    """INVARIANT: every example in a snapshot belongs to its dataset ID."""
    repository = DatasetReplicaRepository(create_store(str(tmp_path)))
    other_dataset_id = UUID("e4970850-07ca-460c-a1b9-5ea1ea2a60de")

    with pytest.raises(DatasetReplicaConflictError, match="belongs to dataset"):
        repository.write_snapshot(
            replica_dataset(),
            DatasetVersion(as_of=VERSION_ONE),
            [replica_example(dataset_id=other_dataset_id, attachment=None)],
        )

    assert repository.statuses() == []

    duplicate = replica_example(attachment=None)
    with pytest.raises(DatasetReplicaConflictError, match="duplicate example ID"):
        repository.write_snapshot(
            replica_dataset(),
            DatasetVersion(as_of=VERSION_ONE),
            [duplicate, duplicate],
        )


def test_corrupt_snapshot_artifact_fails_integrity_check(tmp_path: Path) -> None:
    """INVARIANT: readers never deserialize bytes that fail the manifest digest."""
    store = create_store(str(tmp_path))
    repository = DatasetReplicaRepository(store)
    repository.write_snapshot(replica_dataset(), DatasetVersion(as_of=VERSION_ONE), [])
    head = json.loads(store.get_text(f"datasets/heads/{DATASET_ID}.json"))
    manifest = json.loads(store.get_text(head["versions"][0]["manifest_key"]))
    store.put_bytes(manifest["examples_key"], b"not parquet")

    with pytest.raises(DatasetReplicaIntegrityError, match="digest mismatch"):
        repository.read_examples(str(DATASET_ID))


def test_corrupt_attachment_fails_before_reader_is_returned(tmp_path: Path) -> None:
    """INVARIANT: attachment content is verified against its content address."""
    store = create_store(str(tmp_path))
    repository = DatasetReplicaRepository(store)
    repository.write_snapshot(
        replica_dataset(),
        DatasetVersion(as_of=VERSION_ONE),
        [replica_example()],
    )
    digest = hashlib.sha256(b"diagram-bytes").hexdigest()
    store.put_bytes(f"datasets/blobs/{digest}", b"corrupt")

    with pytest.raises(DatasetReplicaIntegrityError, match="attachment digest"):
        repository.read_examples(str(DATASET_ID), include_attachments=True)


def test_manifest_cross_references_and_counts_are_enforced(tmp_path: Path) -> None:
    """INVARIANT: a head cannot redirect a version to another dataset or row count."""
    store = create_store(str(tmp_path))
    repository = DatasetReplicaRepository(store)
    repository.write_snapshot(
        replica_dataset(),
        DatasetVersion(as_of=VERSION_ONE),
        [replica_example(attachment=None)],
    )
    head_key = f"datasets/heads/{DATASET_ID}.json"
    head = json.loads(store.get_text(head_key))
    manifest_key = head["versions"][0]["manifest_key"]
    manifest = json.loads(store.get_text(manifest_key))
    manifest["example_count"] = 2
    _replace_manifest_and_head_digest(store, head_key, 0, manifest)

    with pytest.raises(DatasetReplicaIntegrityError, match="example count"):
        repository.read_examples(str(DATASET_ID))

    manifest["dataset_id"] = "e4970850-07ca-460c-a1b9-5ea1ea2a60de"
    _replace_manifest_and_head_digest(store, head_key, 0, manifest)
    with pytest.raises(DatasetReplicaIntegrityError, match="dataset ID"):
        repository.read_dataset(str(DATASET_ID))

    manifest["dataset_id"] = str(DATASET_ID)
    manifest["version"]["as_of"] = VERSION_TWO.isoformat()
    _replace_manifest_and_head_digest(store, head_key, 0, manifest)
    with pytest.raises(DatasetReplicaIntegrityError, match="manifest version"):
        repository.read_dataset(str(DATASET_ID))

    manifest["version"]["as_of"] = VERSION_ONE.isoformat()
    manifest["example_count"] = 1
    manifest["attachment_count"] = 1
    _replace_manifest_and_head_digest(store, head_key, 0, manifest)
    with pytest.raises(DatasetReplicaIntegrityError, match="attachment count"):
        repository.read_examples(str(DATASET_ID))


def test_head_authenticates_manifest_bytes(tmp_path: Path) -> None:
    """INVARIANT: a version head authenticates the exact manifest it selected."""
    store = create_store(str(tmp_path))
    repository = DatasetReplicaRepository(store)
    repository.write_snapshot(
        replica_dataset(),
        DatasetVersion(as_of=VERSION_ONE),
        [replica_example(input_value="v1", attachment=None)],
    )
    repository.write_snapshot(
        replica_dataset(),
        DatasetVersion(as_of=VERSION_TWO),
        [replica_example(input_value="v2", attachment=None)],
    )
    head = json.loads(store.get_text(f"datasets/heads/{DATASET_ID}.json"))
    first_manifest_key = head["versions"][0]["manifest_key"]
    second_manifest_key = head["versions"][1]["manifest_key"]
    first_manifest = json.loads(store.get_text(first_manifest_key))
    second_manifest = json.loads(store.get_text(second_manifest_key))
    first_manifest["examples_key"] = second_manifest["examples_key"]
    first_manifest["examples_sha256"] = second_manifest["examples_sha256"]
    store.put_text(first_manifest_key, json.dumps(first_manifest))

    with pytest.raises(DatasetReplicaIntegrityError, match="manifest digest"):
        repository.read_examples(str(DATASET_ID), as_of=VERSION_ONE.isoformat())


def test_malformed_head_fails_as_typed_schema_error(tmp_path: Path) -> None:
    """INVARIANT: untrusted catalog JSON is validated before typed access."""
    store = create_store(str(tmp_path))
    store.put_text(
        f"datasets/heads/{DATASET_ID}.json",
        json.dumps(
            {
                "schema_version": 2,
                "dataset_id": str(DATASET_ID),
                "dataset": replica_dataset().model_dump(mode="json"),
                "latest_as_of": VERSION_ONE.isoformat(),
                "versions": [],
            }
        ),
    )

    with pytest.raises(DatasetReplicaSchemaError, match="at least one version"):
        DatasetReplicaRepository(store).read_dataset(str(DATASET_ID))


def test_untrusted_catalog_payload_shapes_fail_fast() -> None:
    """INVARIANT: every dynamic JSON envelope is checked before TypedDict casts."""
    version = {
        "as_of": VERSION_ONE.isoformat(),
        "manifest_key": "datasets/manifest.json",
        "manifest_sha256": "d" * 64,
        "tags": ["latest"],
    }
    valid_head = {
        "schema_version": 2,
        "dataset_id": str(DATASET_ID),
        "dataset": replica_dataset().model_dump(mode="json"),
        "latest_as_of": VERSION_ONE.isoformat(),
        "versions": [version],
    }
    with pytest.raises(DatasetReplicaError, match="JSON object"):
        _parse_head("[]")
    with pytest.raises(DatasetReplicaSchemaError, match="fields changed"):
        _parse_head("{}")
    with pytest.raises(DatasetReplicaError, match="Unsupported"):
        _parse_head(json.dumps({**valid_head, "schema_version": 3}))
    with pytest.raises(DatasetReplicaSchemaError, match="JSON object"):
        _parse_head(json.dumps({**valid_head, "versions": [1]}))
    with pytest.raises(DatasetReplicaSchemaError, match="unique and sorted"):
        _parse_head(
            json.dumps(
                {
                    **valid_head,
                    "versions": [
                        {**version, "as_of": VERSION_TWO.isoformat()},
                        version,
                    ],
                    "latest_as_of": VERSION_TWO.isoformat(),
                }
            )
        )
    with pytest.raises(DatasetReplicaSchemaError, match="newest version"):
        _parse_head(json.dumps({**valid_head, "latest_as_of": VERSION_TWO.isoformat()}))
    with pytest.raises(DatasetReplicaSchemaError, match="string array"):
        _parse_head(json.dumps({**valid_head, "versions": [{**version, "tags": [1]}]}))

    valid_manifest = {
        "schema_version": 2,
        "dataset_id": str(DATASET_ID),
        "version": {"as_of": VERSION_ONE.isoformat(), "tags": None},
        "examples_key": "datasets/examples.parquet",
        "examples_sha256": "b" * 64,
        "content_digest": "c" * 64,
        "example_count": 1,
        "attachment_count": 0,
        "published_at": VERSION_ONE.isoformat(),
    }
    with pytest.raises(DatasetReplicaSchemaError, match="version must be an object"):
        _parse_manifest(json.dumps({**valid_manifest, "version": 1}))
    with pytest.raises(DatasetReplicaSchemaError, match="SHA-256"):
        _parse_manifest(json.dumps({**valid_manifest, "content_digest": "short"}))
    with pytest.raises(DatasetReplicaSchemaError, match="non-negative integer"):
        _parse_manifest(json.dumps({**valid_manifest, "example_count": -1}))
    with pytest.raises(DatasetReplicaSchemaError, match="ISO timestamp"):
        _parse_manifest(json.dumps({**valid_manifest, "published_at": "yesterday"}))

    with pytest.raises(DatasetReplicaError, match="JSON array"):
        _parse_attachments("{}")
    with pytest.raises(DatasetReplicaSchemaError, match="JSON object"):
        _parse_attachments("[1]")
    with pytest.raises(DatasetReplicaSchemaError, match="fields changed"):
        _parse_attachments('[{"name":"file"}]')
    with pytest.raises(DatasetReplicaSchemaError, match="SHA-256"):
        _parse_attachments(
            json.dumps(
                [
                    {
                        "name": "file",
                        "mime_type": None,
                        "digest": "z" * 64,
                        "size": 1,
                    }
                ]
            )
        )
    with pytest.raises(DatasetReplicaSchemaError, match="non-negative integer"):
        _parse_attachments(
            json.dumps(
                [
                    {
                        "name": "file",
                        "mime_type": None,
                        "digest": "a" * 64,
                        "size": -1,
                    }
                ]
            )
        )


def test_repository_configuration_failures_are_typed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """INVARIANT: location failures are actionable and never raw KeyErrors."""
    monkeypatch.delenv("LANGSMITH_ARCHIVE_URI", raising=False)
    with pytest.raises(DatasetReplicaConfigurationError, match="--archive-uri"):
        repository_for(ReplicaSource.ARCHIVE, archive_uri=None, local_directory=None)
    with pytest.raises(DatasetReplicaConfigurationError, match="not a readable"):
        repository_for(ReplicaSource.CLOUD, archive_uri=None, local_directory=None)
    with pytest.raises(DatasetReplicaConfigurationError, match="Invalid archive"):
        repository_for(
            ReplicaSource.ARCHIVE,
            archive_uri="ftp://invalid.example/archive",
            local_directory=str(tmp_path),
        )


def test_cli_replica_errors_are_structured_for_agents_and_humans(
    runner, monkeypatch: pytest.MonkeyPatch
) -> None:
    """INVARIANT: typed replica errors cross the shared CLI boundary cleanly."""
    monkeypatch.delenv("LANGSMITH_ARCHIVE_URI", raising=False)

    json_result = runner.invoke(
        cli, ["--json", "datasets", "status", "--source", "archive"]
    )
    human_result = runner.invoke(cli, ["datasets", "status", "--source", "archive"])

    assert json_result.exit_code == 1
    assert json.loads(json_result.output)["error"] == (
        "DatasetReplicaConfigurationError"
    )
    assert human_result.exit_code == 1
    assert "Error:" in human_result.output
    assert "--archive-uri" in human_result.output


def test_historical_example_lookup_skips_datasets_without_that_version(
    tmp_path: Path,
) -> None:
    """INVARIANT: unrelated dataset history cannot hide a matching example."""
    repository = DatasetReplicaRepository(create_store(str(tmp_path)))
    repository.write_snapshot(
        replica_dataset(),
        DatasetVersion(as_of=VERSION_ONE),
        [replica_example(attachment=None)],
    )
    unrelated_id = UUID("e4970850-07ca-460c-a1b9-5ea1ea2a60de")
    unrelated = replica_dataset().model_copy(
        update={"id": unrelated_id, "name": "newer-dataset"}
    )
    repository.write_snapshot(unrelated, DatasetVersion(as_of=VERSION_TWO), [])

    restored = repository.read_example(str(EXAMPLE_ID), as_of=VERSION_ONE.isoformat())

    assert restored.id == EXAMPLE_ID


class _CoordinatedRepository(DatasetReplicaRepository):
    """Align the first head read while retaining the real local store and CAS."""

    def __init__(self, root: Path, barrier: Barrier) -> None:
        super().__init__(create_store(str(root)))
        self._barrier = barrier
        self._first_head_read = True

    def _read_head_for_update(self, dataset_id: str):
        result = super()._read_head_for_update(dataset_id)
        if self._first_head_read:
            self._first_head_read = False
            self._barrier.wait()
        return result


def test_concurrent_different_versions_are_both_published(tmp_path: Path) -> None:
    """INVARIANT: head CAS retries merge independent concurrent versions."""
    barrier = Barrier(2)

    def publish(as_of: datetime) -> None:
        repository = _CoordinatedRepository(tmp_path, barrier)
        repository.write_snapshot(
            replica_dataset(),
            DatasetVersion(as_of=as_of),
            [replica_example(input_value=as_of.isoformat(), attachment=None)],
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(publish, value) for value in (VERSION_ONE, VERSION_TWO)]
        for future in futures:
            future.result()

    versions = DatasetReplicaRepository(create_store(str(tmp_path))).list_versions(
        str(DATASET_ID)
    )
    assert {version.as_of for version in versions} == {VERSION_ONE, VERSION_TWO}


def test_concurrent_identical_version_is_idempotent(tmp_path: Path) -> None:
    """INVARIANT: identical concurrent publications produce one head, not conflict."""
    barrier = Barrier(2)

    def publish():
        return _CoordinatedRepository(tmp_path, barrier).write_snapshot(
            replica_dataset(),
            DatasetVersion(tags=["latest"], as_of=VERSION_ONE),
            [replica_example(attachment=None)],
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [
            future.result() for future in [pool.submit(publish), pool.submit(publish)]
        ]

    assert sorted(result.already_present for result in results) == [False, True]
    repository = DatasetReplicaRepository(create_store(str(tmp_path)))
    assert len(repository.list_versions(str(DATASET_ID))) == 1
    assert len(repository.read_examples(str(DATASET_ID))) == 1


def test_concurrent_divergent_same_version_cannot_publish_mixed_data(
    tmp_path: Path,
) -> None:
    """INVARIANT: the winning head references only its writer's immutable data."""
    barrier = Barrier(2)

    def publish(label: str):
        repository = _CoordinatedRepository(tmp_path, barrier)
        result = repository.write_snapshot(
            replica_dataset().model_copy(update={"name": label}),
            DatasetVersion(as_of=VERSION_ONE),
            [
                replica_example(
                    input_value=label * (250_000 if label == "A" else 2),
                    attachment=None,
                )
            ],
        )
        return label, result

    successes = []
    conflicts = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(publish, label) for label in ("A", "B")]
        for future in futures:
            try:
                successes.append(future.result())
            except DatasetReplicaConflictError as exc:
                conflicts.append(exc)

    assert len(successes) == 1
    assert len(conflicts) == 1
    winner = successes[0][0]
    repository = DatasetReplicaRepository(create_store(str(tmp_path)))
    assert repository.read_dataset(str(DATASET_ID)).name == winner
    stored_inputs = repository.read_examples(str(DATASET_ID))[0].inputs
    assert stored_inputs is not None
    stored_value = stored_inputs["question"]
    assert stored_value == winner * (250_000 if winner == "A" else 2)


def test_equivalent_timestamp_offsets_have_one_canonical_identity(
    tmp_path: Path,
) -> None:
    """INVARIANT: version identity is an instant, never timestamp spelling."""
    repository = DatasetReplicaRepository(create_store(str(tmp_path)))
    utc_version = DatasetVersion(as_of=VERSION_ONE)
    offset_version = DatasetVersion(
        as_of=VERSION_ONE.astimezone(timezone(timedelta(hours=2)))
    )

    first = repository.write_snapshot(
        replica_dataset(), utc_version, [replica_example(attachment=None)]
    )
    repeated = repository.write_snapshot(
        replica_dataset(), offset_version, [replica_example(attachment=None)]
    )

    assert first.already_present is False
    assert repeated.already_present is True
    assert [item.as_of for item in repository.list_versions(str(DATASET_ID))] == [
        VERSION_ONE
    ]


class _ChunkOnlyAttachmentReader(io.BytesIO):
    """Fail if publication tries to materialize an attachment in one read."""

    def read(self, size: int | None = -1) -> bytes:
        if size is None or size < 0:
            raise AssertionError("attachment reads must be chunk bounded")
        return super().read(size)


def test_attachment_publication_streams_bounded_chunks(tmp_path: Path) -> None:
    """INVARIANT: attachment size does not become an equivalent RAM allocation."""
    example = replica_example(attachment=None).model_copy(
        update={
            "attachments": {
                "large.bin": {
                    "presigned_url": "https://cloud.invalid/large",
                    "reader": _ChunkOnlyAttachmentReader(b"bounded-stream"),
                    "mime_type": "application/octet-stream",
                }
            }
        }
    )
    repository = DatasetReplicaRepository(create_store(str(tmp_path)))

    repository.write_snapshot(
        replica_dataset(), DatasetVersion(as_of=VERSION_ONE), [example]
    )

    restored = repository.read_examples(str(DATASET_ID), include_attachments=True)[0]
    assert restored.attachments is not None
    assert restored.attachments["large.bin"]["reader"].read() == b"bounded-stream"


def test_sdk_contract_drift_fails_before_snapshot_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """INVARIANT: a new SDK field is never silently dropped from a replica."""
    drifted_fields = {**Dataset.model_fields, "future_field": object()}
    monkeypatch.setattr(Dataset, "model_fields", drifted_fields)
    repository = DatasetReplicaRepository(create_store(str(tmp_path)))

    with pytest.raises(DatasetReplicaSchemaError, match="Dataset SDK fields changed"):
        repository.write_snapshot(
            replica_dataset(), DatasetVersion(as_of=VERSION_ONE), []
        )

    assert repository.statuses() == []


def test_cli_pull_then_offline_list_uses_no_cloud_client(runner, tmp_path: Path):
    """INVARIANT: local reads do not instantiate the LangSmith client."""
    dataset = replica_dataset()
    version = DatasetVersion(tags=["latest"], as_of=VERSION_ONE)
    with patch("langsmith.Client") as client_class:
        client = client_class.return_value
        client.read_dataset.return_value = dataset
        client.read_dataset_version.return_value = version
        client.list_examples.return_value = iter([replica_example()])
        pulled = runner.invoke(
            cli,
            [
                "--json",
                "datasets",
                "pull",
                dataset.name,
                "--to",
                "local",
                "--local-dir",
                str(tmp_path),
            ],
        )
    assert pulled.exit_code == 0, pulled.output
    assert json.loads(pulled.output)[0]["example_count"] == 1
    assert client.list_examples.call_args.kwargs["as_of"] == VERSION_ONE

    with patch("langsmith.Client") as client_class:
        listed = runner.invoke(
            cli,
            [
                "--json",
                "datasets",
                "list",
                "--source",
                "local",
                "--local-dir",
                str(tmp_path),
            ],
        )
        filtered_datasets = runner.invoke(
            cli,
            [
                "--json",
                "datasets",
                "list",
                "--source",
                "local",
                "--local-dir",
                str(tmp_path),
                "--dataset-ids",
                str(DATASET_ID),
                "--data-type",
                "kv",
                "--name",
                dataset.name,
                "--name-contains",
                "evaluation",
                "--metadata",
                '{"owner":"evals"}',
            ],
        )
        examples = runner.invoke(
            cli,
            [
                "--json",
                "examples",
                "list",
                "--dataset",
                dataset.name,
                "--source",
                "local",
                "--local-dir",
                str(tmp_path),
            ],
        )
        filtered_examples = runner.invoke(
            cli,
            [
                "--json",
                "examples",
                "list",
                "--dataset",
                dataset.name,
                "--source",
                "local",
                "--local-dir",
                str(tmp_path),
                "--example-ids",
                str(EXAMPLE_ID),
                "--metadata",
                '{"difficulty":"hard"}',
                "--splits",
                "test",
            ],
        )
        dataset_details = runner.invoke(
            cli,
            [
                "--json",
                "datasets",
                "get",
                dataset.name,
                "--source",
                "local",
                "--local-dir",
                str(tmp_path),
            ],
        )
        versions = runner.invoke(
            cli,
            [
                "--json",
                "datasets",
                "versions",
                dataset.name,
                "--source",
                "local",
                "--local-dir",
                str(tmp_path),
            ],
        )
        status = runner.invoke(
            cli,
            [
                "--json",
                "datasets",
                "status",
                "--source",
                "local",
                "--local-dir",
                str(tmp_path),
            ],
        )
        example_details = runner.invoke(
            cli,
            [
                "--json",
                "examples",
                "get",
                str(EXAMPLE_ID),
                "--source",
                "local",
                "--local-dir",
                str(tmp_path),
                "--include-attachments",
                "--fields",
                "id,attachments",
            ],
        )
        human_status = runner.invoke(
            cli,
            [
                "datasets",
                "status",
                "--source",
                "local",
                "--local-dir",
                str(tmp_path),
            ],
        )
        unsupported_filter = runner.invoke(
            cli,
            [
                "--json",
                "examples",
                "list",
                "--source",
                "local",
                "--local-dir",
                str(tmp_path),
                "--filter",
                'eq(id, "x")',
            ],
        )
        same_source = runner.invoke(
            cli,
            [
                "--json",
                "datasets",
                "pull",
                dataset.name,
                "--source",
                "local",
                "--to",
                "local",
                "--local-dir",
                str(tmp_path),
            ],
        )
        client_class.assert_not_called()
    assert listed.exit_code == 0, listed.output
    assert json.loads(listed.output)[0]["name"] == dataset.name
    assert filtered_datasets.exit_code == 0, filtered_datasets.output
    assert len(json.loads(filtered_datasets.output)) == 1
    assert examples.exit_code == 0, examples.output
    assert json.loads(examples.output)[0]["inputs"] == {"question": "why?"}
    assert filtered_examples.exit_code == 0, filtered_examples.output
    assert len(json.loads(filtered_examples.output)) == 1
    assert dataset_details.exit_code == 0, dataset_details.output
    assert json.loads(dataset_details.output)["id"] == str(DATASET_ID)
    assert versions.exit_code == 0, versions.output
    assert json.loads(versions.output)[0]["tags"] == ["latest"]
    assert status.exit_code == 0, status.output
    assert json.loads(status.output)[0]["versions"] == 1
    assert example_details.exit_code == 0, example_details.output
    assert "diagram.png" in json.loads(example_details.output)["attachments"]
    assert human_status.exit_code == 0, human_status.output
    assert "evaluation-set" in human_status.output
    assert unsupported_filter.exit_code == 1
    assert "available only" in unsupported_filter.output
    assert same_source.exit_code == 1
    assert "must differ" in same_source.output


def test_source_specific_options_fail_instead_of_being_silently_ignored(
    runner, tmp_path: Path
) -> None:
    """INVARIANT: the uniform facade rejects unsupported source semantics."""
    with patch("langsmith.Client") as client_class:
        cloud_as_of = runner.invoke(
            cli,
            [
                "datasets",
                "get",
                str(DATASET_ID),
                "--source",
                "cloud",
                "--as-of",
                VERSION_ONE.isoformat(),
            ],
        )
        client_class.assert_not_called()
    offline_inline = runner.invoke(
        cli,
        [
            "examples",
            "list",
            "--source",
            "local",
            "--local-dir",
            str(tmp_path),
            "--inline-s3-urls",
        ],
    )

    assert cloud_as_of.exit_code == 1
    assert "only supported" in cloud_as_of.output
    assert offline_inline.exit_code == 1
    assert "only supported" in offline_inline.output


def test_archive_snapshot_can_be_pulled_to_local(tmp_path: Path):
    """INVARIANT: archive-to-local uses the same public SDK contracts."""
    archive = tmp_path / "archive"
    local = tmp_path / "local"
    source = DatasetReplicaRepository(create_store(str(archive)))
    source.write_snapshot(
        replica_dataset(),
        DatasetVersion(tags=["prod"], as_of=VERSION_ONE),
        [replica_example()],
    )

    results = pull_dataset(
        client=None,
        dataset_name_or_id=str(DATASET_ID),
        source=ReplicaSource.ARCHIVE,
        destination=ReplicaDestination.LOCAL,
        as_of="prod",
        all_versions=False,
        archive_uri=str(archive),
        local_directory=str(local),
    )
    restored = DatasetReplicaRepository(create_store(str(local))).read_examples(
        str(DATASET_ID), include_attachments=True
    )[0]

    assert results[0].already_present is False
    assert restored.attachments is not None
    assert restored.attachments["diagram.png"]["reader"].read() == b"diagram-bytes"


def test_selected_version_transfer_synchronizes_unique_tag_pointers(
    tmp_path: Path,
) -> None:
    """INVARIANT: a LangSmith version tag resolves to at most one local version."""
    archive = tmp_path / "archive"
    local = tmp_path / "local"
    source = DatasetReplicaRepository(create_store(str(archive)))
    destination = DatasetReplicaRepository(create_store(str(local)))
    destination.write_snapshot(
        replica_dataset(),
        DatasetVersion(tags=["prod"], as_of=VERSION_ONE),
        [replica_example(input_value="v1", attachment=None)],
    )
    source.write_snapshot(
        replica_dataset(),
        DatasetVersion(tags=None, as_of=VERSION_ONE),
        [replica_example(input_value="v1", attachment=None)],
    )
    source.write_snapshot(
        replica_dataset(),
        DatasetVersion(tags=["prod"], as_of=VERSION_TWO),
        [replica_example(input_value="v2", attachment=None)],
    )

    pull_dataset(
        client=None,
        dataset_name_or_id=str(DATASET_ID),
        source=ReplicaSource.ARCHIVE,
        destination=ReplicaDestination.LOCAL,
        as_of="prod",
        all_versions=False,
        archive_uri=str(archive),
        local_directory=str(local),
    )

    versions = destination.list_versions(str(DATASET_ID))
    assert [(item.as_of, item.tags) for item in versions] == [
        (VERSION_TWO, ["prod"]),
        (VERSION_ONE, None),
    ]
    assert destination.read_examples(str(DATASET_ID), as_of="prod")[0].inputs == {
        "question": "v2"
    }


def test_global_offline_list_skips_histories_without_requested_version(
    runner, tmp_path: Path
) -> None:
    """INVARIANT: independent histories cannot abort a global as-of query."""
    repository = DatasetReplicaRepository(create_store(str(tmp_path)))
    repository.write_snapshot(
        replica_dataset(),
        DatasetVersion(as_of=VERSION_ONE),
        [replica_example(attachment=None)],
    )
    unrelated_id = UUID("e4970850-07ca-460c-a1b9-5ea1ea2a60de")
    unrelated = replica_dataset().model_copy(
        update={"id": unrelated_id, "name": "only-newer"}
    )
    repository.write_snapshot(unrelated, DatasetVersion(as_of=VERSION_TWO), [])

    result = runner.invoke(
        cli,
        [
            "--json",
            "examples",
            "list",
            "--source",
            "local",
            "--local-dir",
            str(tmp_path),
            "--as-of",
            VERSION_ONE.isoformat(),
        ],
    )

    assert result.exit_code == 0, result.output
    assert [item["id"] for item in json.loads(result.output)] == [str(EXAMPLE_ID)]


def test_same_name_datasets_are_ambiguous_instead_of_overwriting(tmp_path: Path):
    """INVARIANT: names are labels; stable dataset IDs are storage identities."""
    repository = DatasetReplicaRepository(create_store(str(tmp_path)))
    first = replica_dataset()
    second = first.model_copy(
        update={"id": UUID("e4970850-07ca-460c-a1b9-5ea1ea2a60de")}
    )
    repository.write_snapshot(first, DatasetVersion(as_of=VERSION_ONE), [])
    repository.write_snapshot(second, DatasetVersion(as_of=VERSION_ONE), [])

    with pytest.raises(DatasetReplicaAmbiguousError, match="ambiguous"):
        repository.read_dataset(first.name)


def test_version_selection_and_cloud_requirements_fail_fast(tmp_path: Path):
    """INVARIANT: version selection never silently falls back to another head."""
    old = DatasetVersion(tags=["baseline"], as_of=VERSION_ONE)
    latest = DatasetVersion(tags=["latest"], as_of=VERSION_TWO)
    versions = [latest, old]

    assert default_local_dataset_directory().name == "datasets"
    assert _select_version(versions, "latest") is latest
    assert _select_version(versions, VERSION_ONE.isoformat()) is old
    assert _select_version(versions, "baseline") is old
    with pytest.raises(ValueError, match="no versions"):
        _select_version([], "latest")
    with pytest.raises(ValueError, match="tag not found"):
        _select_version(versions, "missing")
    with pytest.raises(ValueError, match="at or before"):
        _select_version(versions, "2020-01-01T00:00:00+00:00")
    with pytest.raises(ValueError, match="requires a LangSmith client"):
        pull_dataset(
            client=None,
            dataset_name_or_id=str(DATASET_ID),
            source=ReplicaSource.CLOUD,
            destination=ReplicaDestination.LOCAL,
            as_of="latest",
            all_versions=False,
            archive_uri=None,
            local_directory=str(tmp_path),
        )

    client = MagicMock()
    client.read_dataset_version.return_value = old
    selected = resolve_cloud_version(client, str(DATASET_ID), VERSION_ONE.isoformat())
    assert selected is old
    assert client.read_dataset_version.call_args.kwargs["as_of"] == VERSION_ONE


@pytest.mark.parametrize("source", [ReplicaSource.ARCHIVE, ReplicaSource.LOCAL])
def test_replica_list_filters_and_sorts_before_pagination(
    runner, tmp_path: Path, source: ReplicaSource
) -> None:
    """INVARIANT: source-independent list semantics page the final result set."""
    root = tmp_path / source.value
    repository = DatasetReplicaRepository(create_store(str(root)))
    first = replica_example(attachment=None)
    second = replica_example(SECOND_EXAMPLE_ID, attachment=None).model_copy(
        update={"created_at": first.created_at + timedelta(hours=1)}
    )
    repository.write_snapshot(
        replica_dataset(), DatasetVersion(as_of=VERSION_ONE), [first, second]
    )
    zeta_id = UUID("ffffffff-ffff-4fff-8fff-ffffffffffff")
    repository.write_snapshot(
        replica_dataset().model_copy(update={"id": zeta_id, "name": "zeta"}),
        DatasetVersion(as_of=VERSION_ONE),
        [],
    )
    location_options = (
        ["--archive-uri", str(root)]
        if source is ReplicaSource.ARCHIVE
        else ["--local-dir", str(root)]
    )

    sorted_datasets = runner.invoke(
        cli,
        [
            "--json",
            "datasets",
            "list",
            "--source",
            source.value,
            *location_options,
            "--sort-by",
            "-name",
            "--limit",
            "1",
        ],
    )
    matching_dataset = runner.invoke(
        cli,
        [
            "--json",
            "datasets",
            "list",
            "--source",
            source.value,
            *location_options,
            "--name-pattern",
            "z*",
            "--limit",
            "1",
        ],
    )
    sorted_examples = runner.invoke(
        cli,
        [
            "--json",
            "examples",
            "list",
            "--dataset",
            str(DATASET_ID),
            "--source",
            source.value,
            *location_options,
            "--sort-by",
            "-created_at",
            "--offset",
            "1",
            "--limit",
            "1",
        ],
    )
    excluded_examples = runner.invoke(
        cli,
        [
            "--json",
            "examples",
            "list",
            "--dataset",
            str(DATASET_ID),
            "--source",
            source.value,
            *location_options,
            "--exclude",
            str(EXAMPLE_ID),
            "--limit",
            "1",
        ],
    )

    assert sorted_datasets.exit_code == 0, sorted_datasets.output
    assert [item["name"] for item in json.loads(sorted_datasets.output)] == ["zeta"]
    assert matching_dataset.exit_code == 0, matching_dataset.output
    assert [item["name"] for item in json.loads(matching_dataset.output)] == ["zeta"]
    assert sorted_examples.exit_code == 0, sorted_examples.output
    assert [item["id"] for item in json.loads(sorted_examples.output)] == [
        str(EXAMPLE_ID)
    ]
    assert excluded_examples.exit_code == 0, excluded_examples.output
    assert [item["id"] for item in json.loads(excluded_examples.output)] == [
        str(SECOND_EXAMPLE_ID)
    ]


def test_cloud_list_defers_pagination_for_client_side_operations(runner) -> None:
    """INVARIANT: cloud and replica sources page the same final ordering."""
    first_dataset = replica_dataset()
    second_dataset = first_dataset.model_copy(
        update={
            "id": UUID("ffffffff-ffff-4fff-8fff-ffffffffffff"),
            "name": "zeta",
        }
    )
    first_example = replica_example(attachment=None)
    second_example = replica_example(SECOND_EXAMPLE_ID, attachment=None).model_copy(
        update={"created_at": first_example.created_at + timedelta(hours=1)}
    )
    with patch("langsmith.Client") as client_class:
        client = client_class.return_value
        client.list_datasets.return_value = iter([first_dataset, second_dataset])
        client.list_examples.return_value = iter([first_example, second_example])

        datasets_result = runner.invoke(
            cli,
            [
                "--json",
                "datasets",
                "list",
                "--sort-by",
                "-name",
                "--limit",
                "1",
            ],
        )
        examples_result = runner.invoke(
            cli,
            [
                "--json",
                "examples",
                "list",
                "--dataset",
                str(DATASET_ID),
                "--sort-by",
                "-created_at",
                "--offset",
                "1",
                "--limit",
                "1",
            ],
        )

    assert datasets_result.exit_code == 0, datasets_result.output
    assert [item["name"] for item in json.loads(datasets_result.output)] == ["zeta"]
    assert client.list_datasets.call_args.kwargs["limit"] is None
    assert examples_result.exit_code == 0, examples_result.output
    assert [item["id"] for item in json.loads(examples_result.output)] == [
        str(EXAMPLE_ID)
    ]
    assert client.list_examples.call_args.kwargs["limit"] is None
    assert client.list_examples.call_args.kwargs["offset"] == 0


def test_cloud_filter_only_page_stops_after_enough_survivors(runner) -> None:
    """INVARIANT: only global sorting may require full cloud materialization."""
    excluded = replica_example(attachment=None)
    included = replica_example(SECOND_EXAMPLE_ID, attachment=None)

    def examples():
        yield excluded
        yield included
        raise AssertionError("filter-only pagination consumed beyond its final page")

    with patch("langsmith.Client") as client_class:
        client = client_class.return_value
        client.list_examples.return_value = examples()

        result = runner.invoke(
            cli,
            [
                "--json",
                "examples",
                "list",
                "--exclude",
                str(EXAMPLE_ID),
                "--limit",
                "1",
            ],
        )

    assert result.exit_code == 0, result.output
    assert [item["id"] for item in json.loads(result.output)] == [
        str(SECOND_EXAMPLE_ID)
    ]


@pytest.mark.parametrize(
    "arguments",
    [
        ["datasets", "list", "--limit", "0"],
        ["datasets", "list", "--limit", "-1"],
        ["examples", "list", "--limit", "0"],
        ["examples", "list", "--limit", "-1"],
        ["examples", "list", "--offset", "-1"],
    ],
)
def test_replica_list_rejects_invalid_page_bounds(
    runner, tmp_path: Path, arguments: list[str]
) -> None:
    """INVARIANT: pagination bounds cannot acquire backend-specific meanings."""
    result = runner.invoke(
        cli,
        [
            "--json",
            *arguments,
            "--source",
            "local",
            "--local-dir",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 2
    assert "Invalid value" in result.output


def test_pull_rejects_conflicting_version_selectors_and_same_repository(
    runner, tmp_path: Path
) -> None:
    """INVARIANT: a transfer names one selection and two distinct stores."""
    repository = DatasetReplicaRepository(create_store(str(tmp_path)))
    repository.write_snapshot(replica_dataset(), DatasetVersion(as_of=VERSION_ONE), [])

    conflicting_selection = runner.invoke(
        cli,
        [
            "--json",
            "datasets",
            "pull",
            str(DATASET_ID),
            "--source",
            "archive",
            "--to",
            "local",
            "--archive-uri",
            str(tmp_path),
            "--local-dir",
            str(tmp_path / "local"),
            "--all-versions",
            "--as-of",
            VERSION_ONE.isoformat(),
        ],
    )
    same_repository = runner.invoke(
        cli,
        [
            "--json",
            "datasets",
            "pull",
            str(DATASET_ID),
            "--source",
            "archive",
            "--to",
            "local",
            "--archive-uri",
            str(tmp_path),
            "--local-dir",
            str(tmp_path),
        ],
    )

    assert conflicting_selection.exit_code == 1
    assert "cannot be combined" in conflicting_selection.output
    assert same_repository.exit_code == 1
    assert "same repository" in same_repository.output


@pytest.mark.parametrize(
    "versions",
    [
        [
            DatasetVersion(tags=["old"], as_of=VERSION_ONE),
            DatasetVersion(tags=["new"], as_of=VERSION_TWO),
        ],
        [
            DatasetVersion(tags=["new"], as_of=VERSION_TWO),
            DatasetVersion(tags=["old"], as_of=VERSION_ONE),
        ],
    ],
)
def test_latest_version_selection_is_independent_of_backend_order(
    versions: list[DatasetVersion],
) -> None:
    """INVARIANT: latest means max(as_of), not the backend's first row."""
    assert _select_version(versions, "latest").as_of == VERSION_TWO
