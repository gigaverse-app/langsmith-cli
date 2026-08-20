"""Private local/S3 archive object storage."""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property
import json
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlparse

from langsmith_cli.archive.models import ArchiveManifest, ArchiveManifestDict


class ArchiveStore(Protocol):
    @property
    def base_uri(self) -> str: ...

    def put_file(self, key: str, source: Path) -> None: ...
    def put_text(self, key: str, content: str) -> None: ...
    def get_text(self, key: str) -> str: ...
    def exists(self, key: str) -> bool: ...
    def list_keys(self, prefix: str) -> list[str]: ...
    def object_uri(self, key: str) -> str: ...


@dataclass(frozen=True)
class LocalArchiveStore:
    root: Path
    base_uri: str

    def put_file(self, key: str, source: Path) -> None:
        import shutil

        target = self.root / key
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)

    def put_text(self, key: str, content: str) -> None:
        target = self.root / key
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    def get_text(self, key: str) -> str:
        return (self.root / key).read_text(encoding="utf-8")

    def exists(self, key: str) -> bool:
        return (self.root / key).is_file()

    def list_keys(self, prefix: str) -> list[str]:
        base = self.root / prefix
        if not base.exists():
            return []
        return sorted(
            str(path.relative_to(self.root))
            for path in base.rglob("*")
            if path.is_file()
        )

    def object_uri(self, key: str) -> str:
        return str((self.root / key).resolve())


@dataclass(frozen=True)
class S3ArchiveStore:
    bucket: str
    prefix: str
    base_uri: str

    def _full_key(self, key: str) -> str:
        return "/".join(part for part in (self.prefix.strip("/"), key) if part)

    @cached_property
    def client(self) -> Any:
        import boto3

        return boto3.client("s3")

    def put_file(self, key: str, source: Path) -> None:
        self.client.upload_file(str(source), self.bucket, self._full_key(key))

    def put_text(self, key: str, content: str) -> None:
        self.client.put_object(
            Bucket=self.bucket,
            Key=self._full_key(key),
            Body=content.encode("utf-8"),
            ContentType="application/json",
        )

    def get_text(self, key: str) -> str:
        response = self.client.get_object(Bucket=self.bucket, Key=self._full_key(key))
        return response["Body"].read().decode("utf-8")

    def exists(self, key: str) -> bool:
        from botocore.exceptions import ClientError

        try:
            self.client.head_object(Bucket=self.bucket, Key=self._full_key(key))
        except ClientError as exc:
            status = exc.response["ResponseMetadata"]["HTTPStatusCode"]
            if status == 404:
                return False
            raise
        return True

    def list_keys(self, prefix: str) -> list[str]:
        full_prefix = self._full_key(prefix)
        paginator = self.client.get_paginator("list_objects_v2")
        keys: list[str] = []
        strip_prefix = self.prefix.strip("/")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=full_prefix):
            if "Contents" not in page:
                continue
            for item in page["Contents"]:
                full_key: str = item["Key"]
                relative = (
                    full_key[len(strip_prefix) :].lstrip("/")
                    if strip_prefix
                    else full_key
                )
                keys.append(relative)
        return sorted(keys)

    def object_uri(self, key: str) -> str:
        return f"s3://{self.bucket}/{self._full_key(key)}"


def create_store(uri: str) -> ArchiveStore:
    parsed = urlparse(uri)
    if parsed.scheme == "s3":
        if not parsed.netloc:
            raise ValueError(f"Invalid S3 archive URI: {uri}")
        return S3ArchiveStore(
            bucket=parsed.netloc,
            prefix=parsed.path.strip("/"),
            base_uri=uri.rstrip("/"),
        )
    if parsed.scheme == "file":
        root = Path(parsed.path).expanduser().resolve()
    elif parsed.scheme == "":
        root = Path(uri).expanduser().resolve()
    else:
        raise ValueError(f"Unsupported archive URI scheme: {parsed.scheme}")
    root.mkdir(parents=True, exist_ok=True)
    return LocalArchiveStore(root=root, base_uri=str(root))


def manifest_key(project_id: str, trace_date: str) -> str:
    return f"manifests/project_id={project_id}/date={trace_date}.json"


def read_manifest(
    store: ArchiveStore, key: str, *, known_exists: bool = False
) -> ArchiveManifest | None:
    if not known_exists and not store.exists(key):
        return None
    raw: object = json.loads(store.get_text(key))
    if not isinstance(raw, dict):
        raise ValueError(f"Archive manifest is not an object: {key}")
    return ArchiveManifest.from_dict(raw)  # type: ignore[arg-type]


def write_manifest(store: ArchiveStore, key: str, manifest: ArchiveManifest) -> None:
    payload: ArchiveManifestDict = manifest.to_dict()
    store.put_text(
        key, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    )
