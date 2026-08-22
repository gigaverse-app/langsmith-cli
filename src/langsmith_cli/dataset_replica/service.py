"""Pure orchestration for cloud, archive, and local dataset replication."""

from __future__ import annotations

from datetime import timezone
import os
from pathlib import Path
from typing import TYPE_CHECKING, TypedDict

from langsmith_cli.archive.storage import create_store
from langsmith_cli.dataset_resolution import resolve_dataset
from langsmith_cli.dataset_replica.contracts import ReplicaWriteResult
from langsmith_cli.dataset_replica.models import (
    ReplicaDestination,
    ReplicaSource,
    parse_datetime,
)
from langsmith_cli.dataset_replica.repository import (
    DatasetReplicaConfigurationError,
    DatasetReplicaRepository,
)

if TYPE_CHECKING:
    from langsmith import Client
    from langsmith.schemas import DatasetVersion


class ReplicaWritePayload(TypedDict):
    dataset_id: str
    dataset_name: str
    as_of: str
    example_count: int
    attachment_count: int
    already_present: bool
    destination: str


def default_local_dataset_directory() -> Path:
    from platformdirs import user_cache_dir

    return Path(user_cache_dir("langsmith-cli", appauthor=False)) / "datasets"


def repository_for(
    source: ReplicaSource | ReplicaDestination,
    *,
    archive_uri: str | None,
    local_directory: str | None,
) -> DatasetReplicaRepository:
    if source is ReplicaSource.LOCAL or source is ReplicaDestination.LOCAL:
        uri = local_directory or str(default_local_dataset_directory())
    elif source is ReplicaSource.ARCHIVE or source is ReplicaDestination.ARCHIVE:
        if archive_uri is not None:
            uri = archive_uri
        elif "LANGSMITH_ARCHIVE_URI" in os.environ:
            uri = os.environ["LANGSMITH_ARCHIVE_URI"]
        else:
            raise DatasetReplicaConfigurationError(
                "Archive operations require --archive-uri or LANGSMITH_ARCHIVE_URI"
            )
    else:
        raise DatasetReplicaConfigurationError(
            "Cloud is not a readable replica repository"
        )
    try:
        store = create_store(uri)
    except (OSError, ValueError) as exc:
        raise DatasetReplicaConfigurationError(
            f"Invalid {source.value} replica location"
        ) from exc
    return DatasetReplicaRepository(store)


def pull_dataset(
    *,
    client: Client | None,
    dataset_name_or_id: str,
    source: ReplicaSource,
    destination: ReplicaDestination,
    as_of: str,
    all_versions: bool,
    archive_uri: str | None,
    local_directory: str | None,
) -> list[ReplicaWriteResult]:
    if all_versions and as_of != "latest":
        raise ValueError("--all-versions cannot be combined with --as-of")
    if (
        source is ReplicaSource.ARCHIVE and destination is ReplicaDestination.ARCHIVE
    ) or (source is ReplicaSource.LOCAL and destination is ReplicaDestination.LOCAL):
        raise ValueError("Dataset source and destination must differ")
    target = repository_for(
        destination,
        archive_uri=archive_uri,
        local_directory=local_directory,
    )
    if source is ReplicaSource.CLOUD:
        if client is None:
            raise ValueError("Cloud replication requires a LangSmith client")
        return _pull_from_cloud(
            client=client,
            target=target,
            dataset_name_or_id=dataset_name_or_id,
            as_of=as_of,
            all_versions=all_versions,
        )
    source_repository = repository_for(
        source,
        archive_uri=archive_uri,
        local_directory=local_directory,
    )
    if source_repository.base_uri == target.base_uri:
        raise ValueError(
            "Dataset source and destination resolve to the same repository"
        )
    versions = source_repository.list_versions(dataset_name_or_id)
    selected = versions if all_versions else [_select_version(versions, as_of)]
    dataset = source_repository.read_dataset(dataset_name_or_id)
    results: list[ReplicaWriteResult] = []
    for version in sorted(selected, key=lambda item: item.as_of):
        version_text = version.as_of.astimezone(timezone.utc).isoformat()
        examples = source_repository.iter_examples(
            dataset_name_or_id,
            as_of=version_text,
            include_attachments=True,
        )
        results.append(target.write_snapshot(dataset, version, examples))
    target.sync_version_tags(str(dataset.id), versions)
    return results


def write_result_payload(result: ReplicaWriteResult) -> ReplicaWritePayload:
    return {
        "dataset_id": result.dataset_id,
        "dataset_name": result.dataset_name,
        "as_of": result.as_of.isoformat(),
        "example_count": result.example_count,
        "attachment_count": result.attachment_count,
        "already_present": result.already_present,
        "destination": result.destination_uri,
    }


def resolve_cloud_version(
    client: Client, dataset_id: str, requested: str
) -> DatasetVersion:
    try:
        requested_time = parse_datetime(requested)
    except ValueError:
        return client.read_dataset_version(dataset_id=dataset_id, tag=requested)
    return client.read_dataset_version(dataset_id=dataset_id, as_of=requested_time)


def _pull_from_cloud(
    *,
    client: Client,
    target: DatasetReplicaRepository,
    dataset_name_or_id: str,
    as_of: str,
    all_versions: bool,
) -> list[ReplicaWriteResult]:
    dataset = resolve_dataset(client, dataset_name_or_id)
    available_versions = list(client.list_dataset_versions(dataset_id=dataset.id))
    versions = (
        available_versions
        if all_versions
        else [resolve_cloud_version(client, str(dataset.id), as_of)]
    )
    versions.sort(key=lambda item: item.as_of)
    results: list[ReplicaWriteResult] = []
    for version in versions:
        # INVARIANT: every page is read against one server-resolved exact version.
        # Without this bound, concurrent cloud edits could create a mixed snapshot.
        examples = client.list_examples(
            dataset_id=dataset.id,
            as_of=version.as_of,
            include_attachments=True,
            inline_s3_urls=False,
        )
        results.append(target.write_snapshot(dataset, version, examples))
    target.sync_version_tags(str(dataset.id), available_versions)
    return results


def _select_version(versions: list[DatasetVersion], requested: str) -> DatasetVersion:
    from langsmith_cli.dataset_replica.versioning import (
        SelectableVersion,
        VersionSelectionError,
        select_version,
    )

    selectable = [
        SelectableVersion(
            position=position,
            as_of=version.as_of,
            tags=tuple(version.tags or ()),
        )
        for position, version in enumerate(versions)
    ]
    try:
        selected = select_version(selectable, requested)
    except VersionSelectionError as exc:
        raise ValueError(str(exc)) from exc
    return versions[selected.position]
