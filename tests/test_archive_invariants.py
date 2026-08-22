"""Publication, safety, and query invariants for trace archives."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, time, timedelta, timezone
from io import BytesIO
import json
from pathlib import Path
from threading import Barrier, Event, Thread
from typing import Any, Iterator

import pytest
from langsmith.schemas import Run

from conftest import create_run
from langsmith_cli.archive.duckdb import (
    DUCKDB_MEMORY_LIMIT,
    configure_duckdb_resources,
)
from langsmith_cli.archive.models import ArchivePhase
from langsmith_cli.archive.query import (
    ArchiveRunQuery,
    _date_partition_overlaps,
    _manifest_identity_from_key,
    _project_matches,
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


def test_queries_ignore_unpublished_raw_and_canonical_objects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """INVARIANT: manifests are the only publication pointers visible to readers."""
    archive_uri = str(tmp_path / "archive")
    monkeypatch.setenv("LANGSMITH_ARCHIVE_URI", archive_uri)
    store, manifest = _sync_primary(tmp_path, [create_run()])
    assert manifest.canonical_key is not None
    store.put_text("raw/orphan/runs.parquet", "not parquet")
    store.put_text("canonical/orphan/runs.parquet", "not parquet")

    runs = query_archive_runs(ArchiveRunQuery(project="dev/agent", limit=0))

    assert len(runs) == 1


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


def test_local_store_lists_backend_neutral_posix_object_keys(tmp_path: Path) -> None:
    store = create_store(str(tmp_path / "archive"))
    store.put_text("projects/project_id=one.json", "{}")
    assert store.list_keys("projects") == ["projects/project_id=one.json"]


def test_local_object_replacement_never_exposes_staged_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """INVARIANT: readers see the old object until the complete replacement commits."""
    import os

    store = create_store(str(tmp_path / "archive"))
    store.put_bytes("datasets/object", b"old-complete-object")
    replace_entered = Event()
    allow_replace = Event()
    real_replace = os.replace

    def controlled_replace(source: str | Path, destination: str | Path) -> None:
        replace_entered.set()
        assert allow_replace.wait(timeout=2)
        real_replace(source, destination)

    monkeypatch.setattr(os, "replace", controlled_replace)
    writer = Thread(
        target=store.put_bytes,
        args=("datasets/object", b"new-complete-object"),
    )
    writer.start()
    assert replace_entered.wait(timeout=2)
    assert store.get_bytes("datasets/object") == b"old-complete-object"
    allow_replace.set()
    writer.join(timeout=2)

    assert writer.is_alive() is False
    assert store.get_bytes("datasets/object") == b"new-complete-object"


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


def test_s3_binary_methods_use_the_same_normalized_object_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """INVARIANT: Parquet/blob methods preserve exact bytes and prefixed keys."""
    import boto3

    class BinaryS3Client:
        def __init__(self) -> None:
            self.objects: dict[tuple[str, str], bytes] = {}

        def upload_file(self, source: str, bucket: str, key: str) -> None:
            self.objects[(bucket, key)] = Path(source).read_bytes()

        def download_file(self, bucket: str, key: str, destination: str) -> None:
            Path(destination).write_bytes(self.objects[(bucket, key)])

        def put_object(
            self, *, Bucket: str, Key: str, Body: bytes, **_: object
        ) -> None:
            self.objects[(Bucket, Key)] = Body

        def get_object(self, *, Bucket: str, Key: str) -> dict[str, object]:
            return {"Body": BytesIO(self.objects[(Bucket, Key)])}

    client = BinaryS3Client()
    monkeypatch.setattr(boto3, "client", lambda service: client)
    store = S3ArchiveStore(
        bucket="archive-bucket",
        prefix="langsmith",
        base_uri="s3://archive-bucket/langsmith",
    )
    source = tmp_path / "source.parquet"
    source.write_bytes(b"parquet-bytes")
    destination = tmp_path / "nested" / "download.parquet"

    store.put_file("datasets/object.parquet", source)
    store.put_bytes("datasets/blob", b"attachment-bytes")
    store.get_file("datasets/object.parquet", destination)

    assert destination.read_bytes() == b"parquet-bytes"
    assert store.get_bytes("datasets/blob") == b"attachment-bytes"
    assert client.objects[("archive-bucket", "langsmith/datasets/blob")] == (
        b"attachment-bytes"
    )


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


def test_s3_store_supports_complete_object_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from botocore.exceptions import ClientError
    import boto3

    class ObjectClient:
        def __init__(self) -> None:
            self.objects: dict[str, bytes] = {}
            self.upload: tuple[str, str, str] | None = None

        def upload_file(self, source: str, bucket: str, key: str) -> None:
            self.upload = (source, bucket, key)
            self.objects[key] = Path(source).read_bytes()

        def download_file(self, bucket: str, key: str, destination: str) -> None:
            Path(destination).write_bytes(self.objects[key])

        def put_object(self, **kwargs: object) -> None:
            body = kwargs["Body"]
            assert isinstance(body, bytes)
            self.objects[str(kwargs["Key"])] = body

        def get_object(self, *, Bucket: str, Key: str) -> dict[str, object]:
            return {"Body": BytesIO(self.objects[Key])}

        def head_object(self, *, Bucket: str, Key: str) -> None:
            if Key not in self.objects:
                raise ClientError(
                    {
                        "Error": {"Code": "NotFound", "Message": "missing"},
                        "ResponseMetadata": {"HTTPStatusCode": 404},
                    },
                    "HeadObject",
                )

    client = ObjectClient()
    monkeypatch.setattr(boto3, "client", lambda service: client)
    store = S3ArchiveStore(
        bucket="archive-bucket", prefix="langsmith", base_uri="s3://archive-bucket"
    )
    source = tmp_path / "run.parquet"
    source.write_bytes(b"PAR1")

    store.put_file("raw/run.parquet", source)
    store.put_bytes("blobs/attachment", b"attachment")
    store.put_text("projects/project.json", "{}")
    destination = tmp_path / "downloaded.parquet"
    store.get_file("raw/run.parquet", destination)

    assert client.upload == (
        str(source),
        "archive-bucket",
        "langsmith/raw/run.parquet",
    )
    assert store.get_text("projects/project.json") == "{}"
    assert store.get_bytes("blobs/attachment") == b"attachment"
    assert destination.read_bytes() == b"PAR1"
    assert store.exists("projects/project.json") is True
    assert store.exists("projects/missing.json") is False
    assert store.object_uri("raw/run.parquet") == (
        "s3://archive-bucket/langsmith/raw/run.parquet"
    )


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"schema_version": 1},
        {"schema_version": "1", "project_id": PROJECT_ID, "project_name": "dev/agent"},
        {"schema_version": 1, "project_id": 7, "project_name": "dev/agent"},
    ],
)
def test_project_catalog_rejects_malformed_records(
    tmp_path: Path, payload: object
) -> None:
    store = create_store(str(tmp_path / "archive"))
    key = project_key(PROJECT_ID)
    store.put_text(key, json.dumps(payload))
    with pytest.raises(ValueError):
        ensure_project_record(store, PROJECT_ID, "dev/agent")


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ({"project_name": 7}, "must be a string"),
        ({"schema_version": "1"}, "must be an integer"),
        ({"canonical_run_count": "1"}, "must be an integer"),
        ({"sealed": "true"}, "must be a boolean"),
        ({"canonical_key": 7}, "string or null"),
        ({"primary": "verified"}, "phase must be an object"),
        ({"primary": {"status": "verified"}}, "invalid schema"),
        (
            {
                "primary": {
                    "status": 7,
                    "generation_id": "generation",
                    "raw_key": "raw/key.parquet",
                    "run_count": 1,
                    "verified_at": "2024-07-03T00:00:00+00:00",
                }
            },
            "primary.status must be a string",
        ),
        (
            {
                "primary": {
                    "status": "verified",
                    "generation_id": "generation",
                    "raw_key": "raw/key.parquet",
                    "run_count": "1",
                    "verified_at": "2024-07-03T00:00:00+00:00",
                }
            },
            "primary.run_count must be an integer",
        ),
        (
            {
                "primary": {
                    "status": "failed",
                    "generation_id": "generation",
                    "raw_key": "raw/key.parquet",
                    "run_count": 1,
                    "verified_at": "2024-07-03T00:00:00+00:00",
                    "error": 7,
                }
            },
            "primary.error must be a string",
        ),
    ],
)
def test_manifest_reader_rejects_malformed_metadata_at_storage_boundary(
    tmp_path: Path,
    mutation: dict[str, object],
    message: str,
) -> None:
    store, manifest = _sync_primary(tmp_path, [create_run()])
    key = manifest_key(PROJECT_ID, TRACE_DATE.isoformat())
    payload = manifest.to_dict()
    payload.update(mutation)  # type: ignore[typeddict-item]
    store.put_text(key, json.dumps(payload))

    with pytest.raises(ValueError, match=message):
        read_manifest(store, key)


def test_manifest_partition_parsing_and_date_pruning_are_exact() -> None:
    identity = _manifest_identity_from_key(
        f"manifests/project_id={PROJECT_ID}/date=2024-07-03.json"
    )
    assert identity == (PROJECT_ID, TRACE_DATE)
    assert _manifest_identity_from_key("manifests/not-a-partition.json") is None

    window_start = date(2024, 7, 4)
    assert (
        _date_partition_overlaps(
            TRACE_DATE,
            # A lower bound at the next midnight prunes the prior partition exactly.
            datetime.combine(window_start, time.min, tzinfo=timezone.utc),
            None,
        )
        is False
    )
    assert (
        _date_partition_overlaps(
            TRACE_DATE,
            None,
            datetime.combine(TRACE_DATE, time.min, tzinfo=timezone.utc),
        )
        is False
    )


@pytest.mark.parametrize(
    ("query", "project_id", "project_name", "matches"),
    [
        (ArchiveRunQuery(project_id="other"), PROJECT_ID, "dev/agent", False),
        (ArchiveRunQuery(project="dev/other"), PROJECT_ID, "dev/agent", False),
        (
            ArchiveRunQuery(project_name_pattern="stg/**"),
            PROJECT_ID,
            "dev/agent",
            False,
        ),
        (ArchiveRunQuery(project_name_regex=r"^stg/"), PROJECT_ID, "dev/agent", False),
        (ArchiveRunQuery(project_name_pattern="dev/**"), PROJECT_ID, "dev/agent", True),
    ],
)
def test_project_predicates_apply_every_configured_selector(
    query: ArchiveRunQuery,
    project_id: str,
    project_name: str,
    matches: bool,
) -> None:
    assert _project_matches(query, project_id, project_name) is matches


def test_archive_query_rejects_negative_limits() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        ArchiveRunQuery(limit=-1)


@pytest.mark.parametrize("uri", ["s3:///missing-bucket", "https://archive.invalid"])
def test_store_rejects_invalid_or_unsupported_uris(uri: str) -> None:
    with pytest.raises(ValueError):
        create_store(uri)


def test_store_factory_supports_s3_and_file_uris(tmp_path: Path) -> None:
    assert isinstance(create_store("s3://archive-bucket/prefix"), S3ArchiveStore)
    assert create_store(tmp_path.as_uri()).base_uri == str(tmp_path.resolve())


def test_duckdb_resources_are_bounded_and_unique_to_each_project_staging_area(
    tmp_path: Path,
) -> None:
    """Concurrent workers must neither share spill files nor claim host memory."""
    import duckdb

    first = duckdb.connect()
    second = duckdb.connect()
    try:
        configure_duckdb_resources(first, tmp_path / "project-a")
        configure_duckdb_resources(second, tmp_path / "project-b")

        first_path = first.execute(
            "SELECT current_setting('temp_directory')"
        ).fetchone()
        second_path = second.execute(
            "SELECT current_setting('temp_directory')"
        ).fetchone()
        assert first_path == (str(tmp_path / "project-a" / "duckdb-spill"),)
        assert second_path == (str(tmp_path / "project-b" / "duckdb-spill"),)
        assert first_path != second_path
        assert first.execute("SELECT current_setting('memory_limit')").fetchone() == (
            DUCKDB_MEMORY_LIMIT,
        )
        assert second.execute("SELECT current_setting('memory_limit')").fetchone() == (
            DUCKDB_MEMORY_LIMIT,
        )
    finally:
        first.close()
        second.close()


def test_archive_parquet_writers_bound_row_group_bytes() -> None:
    """
    Trace rows carry multi-megabyte JSON text (inputs/outputs), so a default
    row-count-sized row group buffers gigabytes before flushing — the buffer cannot
    spill, and real project-days OOMed a 1 GiB bound in-cluster even with
    insertion-order preservation off. Every archive COPY must use the shared
    byte-bounded options.
    """
    import inspect

    from langsmith_cli.archive import sync as sync_module
    from langsmith_cli.archive.duckdb import ARCHIVE_PARQUET_COPY_OPTIONS

    assert "ROW_GROUP_SIZE_BYTES" in ARCHIVE_PARQUET_COPY_OPTIONS
    assert "FORMAT PARQUET" in ARCHIVE_PARQUET_COPY_OPTIONS
    source = inspect.getsource(sync_module)
    assert "(FORMAT PARQUET" not in source, (
        "archive COPY statements must use ARCHIVE_PARQUET_COPY_OPTIONS, "
        "not inline Parquet options"
    )


def test_duckdb_connections_do_not_preserve_insertion_order(tmp_path: Path) -> None:
    """
    Insertion-order preservation blocks spilling for the canonicalization
    union+window pipeline, so DuckDB holds the whole project-day in memory and real
    Gigaverse days OOM even at a 2.0 GiB bound (1.9 GiB/2.0 GiB used, in-cluster).
    Row order is semantically irrelevant here: every archive read query orders
    explicitly (ORDER BY start_time) and dedup ranks explicitly by snapshot_rank,
    so the archive only ever relies on declared ordering.
    """
    import duckdb

    connection = duckdb.connect()
    try:
        configure_duckdb_resources(connection, tmp_path / "project-a")
        assert connection.execute(
            "SELECT current_setting('preserve_insertion_order')"
        ).fetchone() == (False,)
    finally:
        connection.close()


def test_duckdb_memory_limit_is_environment_configurable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Operators must be able to raise the per-connection bound without a code change.

    The 1 GiB default OOMed real Gigaverse dev/production daily syncs inside a
    Kubernetes pod (DuckDB "failed to allocate ... 916.1 MiB/1.0 GiB used" during
    canonicalization); the pod owner knows its memory budget, the library does not.
    """
    import duckdb

    monkeypatch.setenv("LANGSMITH_ARCHIVE_DUCKDB_MEMORY_LIMIT", "1.5 GiB")
    connection = duckdb.connect()
    try:
        configure_duckdb_resources(connection, tmp_path / "project-a")
        assert connection.execute(
            "SELECT current_setting('memory_limit')"
        ).fetchone() == ("1.5 GiB",)
    finally:
        connection.close()


def test_duckdb_memory_limit_rejects_garbage_at_configuration_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A typo in the override must fail loudly when the connection is configured, not corrupt a sync later."""
    import duckdb

    monkeypatch.setenv("LANGSMITH_ARCHIVE_DUCKDB_MEMORY_LIMIT", "lots-of-ram")
    connection = duckdb.connect()
    try:
        with pytest.raises(duckdb.Error):
            configure_duckdb_resources(connection, tmp_path / "project-a")
    finally:
        connection.close()


def test_s3_store_propagates_non_concurrency_service_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from botocore.exceptions import ClientError
    import boto3

    class FailingClient:
        def put_object(self, **kwargs: object) -> None:
            raise self._error("PutObject")

        def head_object(self, **kwargs: object) -> None:
            raise self._error("HeadObject")

        @staticmethod
        def _error(operation: str) -> ClientError:
            return ClientError(
                {
                    "Error": {"Code": "InternalError", "Message": "retry upstream"},
                    "ResponseMetadata": {"HTTPStatusCode": 500},
                },
                operation,
            )

    monkeypatch.setattr(boto3, "client", lambda service: FailingClient())
    store = S3ArchiveStore(
        bucket="archive-bucket", prefix="langsmith", base_uri="s3://archive-bucket"
    )

    with pytest.raises(ClientError):
        store.put_text_if_version("manifests/day.json", "{}", None)
    with pytest.raises(ClientError):
        store.exists("manifests/day.json")
