"""Publication, safety, and query invariants for trace archives."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from io import BytesIO
import json
from pathlib import Path
from threading import Barrier, Thread
from typing import Any, Iterator

import pytest
from langsmith.schemas import Run

from conftest import create_run
from langsmith_cli.archive.models import ArchivePhase
from langsmith_cli.archive.query import (
    ArchiveRunQuery,
    count_archive_runs,
    query_archive_runs,
)
from langsmith_cli.archive.repository import (
    ensure_project_record,
    manifest_key,
    project_key,
    read_manifest,
    read_manifest_snapshot,
    write_manifest,
)
from langsmith_cli.archive.storage import (
    ConcurrentArchiveWriteError,
    S3ArchiveStore,
    _is_windows_drive_path,
    create_store,
)
from langsmith_cli.archive.sync import sync_project_day


PROJECT_ID = "f47ac10b-58cc-4372-a567-0e02b2c3d479"
TRACE_DATE = date(2024, 7, 3)


class FakeRunsClient:
    def __init__(self, runs: list[Run]) -> None:
        self.runs = runs
        self.calls = 0

    def list_runs(self, **kwargs: Any) -> Iterator[Run]:
        self.calls += 1
        return iter(self.runs)


def _sync_primary(tmp_path: Path, runs: list[Run]):
    store = create_store(str(tmp_path / "archive"))
    manifest = sync_project_day(
        FakeRunsClient(runs),
        store,
        project_id=PROJECT_ID,
        project_name="dev/agent",
        trace_date=TRACE_DATE,
        phase=ArchivePhase.PRIMARY,
    )
    return store, manifest


def test_manifest_publication_rejects_a_stale_writer(tmp_path: Path) -> None:
    store, _ = _sync_primary(tmp_path, [create_run()])
    key = manifest_key(PROJECT_ID, TRACE_DATE.isoformat())
    snapshot = read_manifest_snapshot(store, key, known_exists=True)
    assert snapshot is not None

    first = replace(
        snapshot.manifest,
        updated_at=snapshot.manifest.updated_at + timedelta(seconds=1),
    )
    write_manifest(store, key, first, expected_version=snapshot.version)

    stale = replace(
        snapshot.manifest,
        updated_at=snapshot.manifest.updated_at + timedelta(seconds=2),
    )
    with pytest.raises(ConcurrentArchiveWriteError, match="changed concurrently"):
        write_manifest(store, key, stale, expected_version=snapshot.version)

    assert read_manifest(store, key, known_exists=True) == first


def test_two_manifest_publishers_cannot_both_win(tmp_path: Path) -> None:
    store, _ = _sync_primary(tmp_path, [create_run()])
    key = manifest_key(PROJECT_ID, TRACE_DATE.isoformat())
    snapshot = read_manifest_snapshot(store, key, known_exists=True)
    assert snapshot is not None
    candidates = [
        replace(
            snapshot.manifest,
            updated_at=snapshot.manifest.updated_at + timedelta(seconds=offset),
        )
        for offset in (1, 2)
    ]
    barrier = Barrier(2)
    outcomes: list[str] = []

    def publish(candidate_index: int) -> None:
        barrier.wait()
        try:
            write_manifest(
                store,
                key,
                candidates[candidate_index],
                expected_version=snapshot.version,
            )
        except ConcurrentArchiveWriteError:
            outcomes.append("conflict")
        else:
            outcomes.append("published")

    threads = [Thread(target=publish, args=(index,)) for index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(outcomes) == ["conflict", "published"]
    assert read_manifest(store, key, known_exists=True) in candidates


def test_reconciliation_only_bootstrap_is_sealed_and_queryable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_uri = str(tmp_path / "archive")
    monkeypatch.setenv("LANGSMITH_ARCHIVE_URI", archive_uri)
    client = FakeRunsClient([create_run()])
    manifest = sync_project_day(
        client,
        create_store(archive_uri),
        project_id=PROJECT_ID,
        project_name="dev/agent",
        trace_date=TRACE_DATE,
        phase=ArchivePhase.RECONCILIATION,
    )
    assert manifest.primary is None
    assert manifest.sealed is True
    assert manifest.canonical_run_count == 1
    assert len(query_archive_runs(ArchiveRunQuery(project="dev/agent"))) == 1


def test_sealed_day_is_idempotent_and_cannot_be_unsealed(tmp_path: Path) -> None:
    store, _ = _sync_primary(tmp_path, [create_run()])
    sealed = sync_project_day(
        FakeRunsClient([create_run()]),
        store,
        project_id=PROJECT_ID,
        project_name="dev/agent",
        trace_date=TRACE_DATE,
        phase=ArchivePhase.RECONCILIATION,
    )
    retry_client = FakeRunsClient([create_run(outputs={"unexpected": True})])
    retried = sync_project_day(
        retry_client,
        store,
        project_id=PROJECT_ID,
        project_name="dev/agent",
        trace_date=TRACE_DATE,
        phase=ArchivePhase.PRIMARY,
    )
    assert retry_client.calls == 0
    assert retried == sealed
    assert retried.sealed is True


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("window_end", "2024-07-05T00:00:00+00:00", "exactly its UTC trace day"),
        ("canonical_key", "../another-prefix/runs.parquet", "relative object key"),
        ("canonical_run_count", -1, "must be non-negative"),
        ("schema_version", 99, "Unsupported archive schema version"),
    ],
)
def test_corrupt_manifest_fails_at_the_storage_boundary(
    tmp_path: Path, field: str, value: object, message: str
) -> None:
    store, _ = _sync_primary(tmp_path, [create_run()])
    key = manifest_key(PROJECT_ID, TRACE_DATE.isoformat())
    payload = json.loads(store.get_text(key))
    assert isinstance(payload, dict)
    payload[field] = value
    store.put_text(key, json.dumps(payload))

    with pytest.raises(ValueError, match=message):
        read_manifest(store, key, known_exists=True)


def test_snapshot_duplicate_run_ids_fail_before_publication(tmp_path: Path) -> None:
    run = create_run()
    store = create_store(str(tmp_path / "archive"))
    with pytest.raises(ValueError, match="duplicate run IDs"):
        sync_project_day(
            FakeRunsClient([run, run]),
            store,
            project_id=PROJECT_ID,
            project_name="dev/agent",
            trace_date=TRACE_DATE,
            phase=ArchivePhase.PRIMARY,
        )
    assert (
        read_manifest(store, manifest_key(PROJECT_ID, TRACE_DATE.isoformat())) is None
    )


def test_empty_archive_is_queryable_as_zero_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_uri = str(tmp_path / "archive")
    monkeypatch.setenv("LANGSMITH_ARCHIVE_URI", archive_uri)
    _sync_primary(tmp_path, [])
    query = ArchiveRunQuery(project="dev/agent", limit=0)
    assert query_archive_runs(query) == []
    assert count_archive_runs(query) == 0


def test_project_catalog_prunes_unrelated_manifest_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_uri = str(tmp_path / "archive")
    monkeypatch.setenv("LANGSMITH_ARCHIVE_URI", archive_uri)
    store, _ = _sync_primary(tmp_path, [create_run()])
    other_id = "22345678-1234-5678-1234-567812345678"
    sync_project_day(
        FakeRunsClient([create_run(id_str="32345678-1234-5678-1234-567812345678")]),
        store,
        project_id=other_id,
        project_name="dev/other",
        trace_date=TRACE_DATE,
        phase=ArchivePhase.PRIMARY,
    )
    # If exact-project discovery regresses to reading every manifest, this corrupt
    # unrelated object will fail the query instead of remaining safely pruned.
    store.put_text(manifest_key(other_id, TRACE_DATE.isoformat()), "not-json")

    runs = query_archive_runs(ArchiveRunQuery(project="dev/agent", limit=0))
    assert len(runs) == 1


def test_partial_catalog_backfill_keeps_legacy_projects_visible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_uri = str(tmp_path / "archive")
    monkeypatch.setenv("LANGSMITH_ARCHIVE_URI", archive_uri)
    store, _ = _sync_primary(tmp_path, [create_run()])
    legacy_id = "22345678-1234-5678-1234-567812345678"
    legacy_run = create_run(id_str="32345678-1234-5678-1234-567812345678")
    sync_project_day(
        FakeRunsClient([legacy_run]),
        store,
        project_id=legacy_id,
        project_name="dev/legacy",
        trace_date=TRACE_DATE,
        phase=ArchivePhase.PRIMARY,
    )
    # Simulate a rollout where manifests predate their catalog entry.
    (Path(archive_uri) / project_key(legacy_id)).unlink()

    runs = query_archive_runs(ArchiveRunQuery(project="dev/legacy", limit=0))
    assert [run.id for run in runs] == [legacy_run.id]


def test_project_catalog_rejects_silent_rename(tmp_path: Path) -> None:
    store = create_store(str(tmp_path / "archive"))
    ensure_project_record(store, PROJECT_ID, "dev/original")
    with pytest.raises(ValueError, match="identity changed"):
        ensure_project_record(store, PROJECT_ID, "dev/renamed")


def test_manifest_location_must_match_its_project_and_date(tmp_path: Path) -> None:
    store, _ = _sync_primary(tmp_path, [create_run()])
    correct = manifest_key(PROJECT_ID, TRACE_DATE.isoformat())
    wrong = manifest_key(PROJECT_ID, "2024-07-04")
    store.put_text(wrong, store.get_text(correct))
    with pytest.raises(ValueError, match="object key does not match"):
        read_manifest(store, wrong, known_exists=True)


def test_canonical_count_is_bounded_by_snapshot_counts(tmp_path: Path) -> None:
    store, _ = _sync_primary(tmp_path, [create_run()])
    key = manifest_key(PROJECT_ID, TRACE_DATE.isoformat())
    payload = json.loads(store.get_text(key))
    assert isinstance(payload, dict)
    payload["canonical_run_count"] = 2
    store.put_text(key, json.dumps(payload))
    with pytest.raises(ValueError, match="bounded by snapshot counts"):
        read_manifest(store, key, known_exists=True)


def test_non_root_filter_returns_only_children(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_uri = str(tmp_path / "archive")
    monkeypatch.setenv("LANGSMITH_ARCHIVE_URI", archive_uri)
    root = create_run()
    child = create_run(
        id_str="22345678-1234-5678-1234-567812345678",
        parent_run_id=str(root.id),
        trace_id=str(root.id),
    )
    _sync_primary(tmp_path, [root, child])
    runs = query_archive_runs(
        ArchiveRunQuery(project="dev/agent", is_root=False, limit=0)
    )
    assert [run.id for run in runs] == [child.id]


def test_archive_text_fields_are_an_explicit_allowlist() -> None:
    with pytest.raises(ValueError, match="Unsupported archive text field"):
        ArchiveRunQuery(text="x", text_fields=("inputs) OR true --",))
    with pytest.raises(ValueError, match="requires at least one field"):
        ArchiveRunQuery(text="x", text_fields=())


def test_literal_archive_search_respects_case_sensitivity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_uri = str(tmp_path / "archive")
    monkeypatch.setenv("LANGSMITH_ARCHIVE_URI", archive_uri)
    _sync_primary(tmp_path, [create_run(outputs={"message": "Hello Archive"})])

    sensitive = ArchiveRunQuery(
        project="dev/agent", text="hello archive", text_ignore_case=False
    )
    insensitive = replace(sensitive, text_ignore_case=True)
    assert query_archive_runs(sensitive) == []
    assert len(query_archive_runs(insensitive)) == 1


def test_store_rejects_object_key_traversal(tmp_path: Path) -> None:
    store = create_store(str(tmp_path / "archive"))
    with pytest.raises(ValueError, match="normalized and relative"):
        store.object_uri("../outside.parquet")


def test_windows_drive_paths_are_not_uri_schemes() -> None:
    assert _is_windows_drive_path(r"C:\archive\traces") is True
    assert _is_windows_drive_path("s3://bucket/traces") is False


def test_s3_conditional_publication_uses_etag_preconditions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RecordingS3Client:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def put_object(self, **kwargs: object) -> None:
            self.calls.append(kwargs)

    import boto3

    client = RecordingS3Client()
    monkeypatch.setattr(boto3, "client", lambda service: client)
    store = S3ArchiveStore(
        bucket="archive-bucket", prefix="langsmith", base_uri="s3://archive-bucket"
    )
    store.put_text_if_version("manifests/day.json", "{}", None)
    store.put_text_if_version("manifests/day.json", "{}", '"etag"')

    assert client.calls[0]["IfNoneMatch"] == "*"
    assert client.calls[1]["IfMatch"] == '"etag"'
    assert client.calls[0]["Key"] == "langsmith/manifests/day.json"


def test_s3_stale_write_is_a_typed_concurrency_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from botocore.exceptions import ClientError
    import boto3

    class ConflictingS3Client:
        def put_object(self, **kwargs: object) -> None:
            raise ClientError(
                {
                    "Error": {"Code": "PreconditionFailed", "Message": "stale"},
                    "ResponseMetadata": {"HTTPStatusCode": 412},
                },
                "PutObject",
            )

    monkeypatch.setattr(boto3, "client", lambda service: ConflictingS3Client())
    store = S3ArchiveStore(
        bucket="archive-bucket", prefix="langsmith", base_uri="s3://archive-bucket"
    )
    with pytest.raises(ConcurrentArchiveWriteError, match="changed concurrently"):
        store.put_text_if_version("manifests/day.json", "{}", '"old-etag"')


def test_s3_reads_versions_and_lists_only_the_manifest_namespace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import boto3

    class FakePaginator:
        def __init__(self) -> None:
            self.prefix: str | None = None

        def paginate(self, *, Bucket: str, Prefix: str):
            self.prefix = Prefix
            return iter(
                [
                    {},
                    {
                        "Contents": [
                            {"Key": "langsmith/manifests/project=b/date=2.json"},
                            {"Key": "langsmith/manifests/project=a/date=1.json"},
                        ]
                    },
                ]
            )

    class ReadingS3Client:
        def __init__(self) -> None:
            self.paginator = FakePaginator()

        def get_object(self, *, Bucket: str, Key: str) -> dict[str, object]:
            return {"Body": BytesIO(b"{}"), "ETag": '"version"'}

        def get_paginator(self, operation: str) -> FakePaginator:
            assert operation == "list_objects_v2"
            return self.paginator

    client = ReadingS3Client()
    monkeypatch.setattr(boto3, "client", lambda service: client)
    store = S3ArchiveStore(
        bucket="archive-bucket", prefix="langsmith", base_uri="s3://archive-bucket"
    )

    stored = store.get_text_with_version("manifests/day.json")
    assert stored.content == "{}"
    assert stored.version == '"version"'
    assert store.list_keys("manifests") == [
        "manifests/project=a/date=1.json",
        "manifests/project=b/date=2.json",
    ]
    assert client.paginator.prefix == "langsmith/manifests/"
