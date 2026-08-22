"""End-to-end invariants for exact, read-only dataset replicas."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import UUID

import pytest
from langsmith.schemas import Dataset, DatasetVersion, Example

from langsmith_cli.archive.storage import create_store
from langsmith_cli.dataset_replica.models import ReplicaDestination, ReplicaSource
from langsmith_cli.dataset_replica.repository import (
    DatasetReplicaAmbiguousError,
    DatasetReplicaRepository,
)
from langsmith_cli.dataset_replica.service import (
    _select_version,
    default_local_dataset_directory,
    pull_dataset,
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
        dataset_id=DATASET_ID,
        inputs={"question": "why?"},
        outputs={"answer": "because"},
        metadata={"dataset_split": ["test"], "difficulty": "hard"},
        created_at=VERSION_ONE - timedelta(hours=1),
        modified_at=VERSION_ONE,
        source_run_id=UUID("6db22a4b-bbb7-45cb-90af-33d4f08a1870"),
        attachments=attachments,
    )


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
        [replica_example(attachment=None)],
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
    with pytest.raises(ValueError, match="not found or ambiguous"):
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
