"""Private local/S3 archive object storage."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from functools import cached_property
import hashlib
import os
from pathlib import Path
from pathlib import PurePosixPath
import tempfile
from typing import Any, Protocol
from urllib.parse import urlparse


class ArchiveStore(Protocol):
    @property
    def base_uri(self) -> str: ...

    def put_file(self, key: str, source: Path) -> None: ...
    def put_bytes(self, key: str, content: bytes) -> None: ...
    def put_text(self, key: str, content: str) -> None: ...
    def get_file(self, key: str, destination: Path) -> None: ...
    def get_bytes(self, key: str) -> bytes: ...
    def get_text(self, key: str) -> str: ...
    def get_text_with_version(self, key: str) -> TextObject: ...
    def put_text_if_version(
        self, key: str, content: str, expected_version: str | None
    ) -> None: ...
    def exists(self, key: str) -> bool: ...
    def list_keys(self, prefix: str) -> list[str]: ...
    def object_uri(self, key: str) -> str: ...


class ConcurrentArchiveWriteError(RuntimeError):
    """A stale writer attempted to replace a newer archive metadata object."""


@dataclass(frozen=True)
class TextObject:
    content: str
    version: str


@dataclass(frozen=True)
class LocalArchiveStore:
    root: Path
    base_uri: str

    def put_file(self, key: str, source: Path) -> None:
        import shutil

        _validate_store_key(key)
        target = self.root / key
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)

    def put_bytes(self, key: str, content: bytes) -> None:
        _validate_store_key(key)
        target = self.root / key
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)

    def put_text(self, key: str, content: str) -> None:
        _validate_store_key(key)
        target = self.root / key
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    def get_text(self, key: str) -> str:
        _validate_store_key(key)
        return (self.root / key).read_text(encoding="utf-8")

    def get_file(self, key: str, destination: Path) -> None:
        import shutil

        _validate_store_key(key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self.root / key, destination)

    def get_bytes(self, key: str) -> bytes:
        _validate_store_key(key)
        return (self.root / key).read_bytes()

    def get_text_with_version(self, key: str) -> TextObject:
        content = self.get_text(key)
        return TextObject(content=content, version=_content_version(content))

    def put_text_if_version(
        self, key: str, content: str, expected_version: str | None
    ) -> None:
        """Publish atomically while holding a cross-process local file lock."""
        _validate_store_key(key)
        target = self.root / key
        target.parent.mkdir(parents=True, exist_ok=True)
        # Lock files live outside object namespaces so manifest listings can never
        # mistake synchronization metadata for published archive data.
        lock_name = hashlib.sha256(key.encode("utf-8")).hexdigest() + ".lock"
        lock_path = self.root / ".locks" / lock_name
        with _exclusive_file_lock(lock_path):
            current_version = (
                _content_version(target.read_text(encoding="utf-8"))
                if target.is_file()
                else None
            )
            if current_version != expected_version:
                raise ConcurrentArchiveWriteError(
                    f"Archive metadata changed concurrently: {key}"
                )
            temporary_path: Path | None = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w",
                    encoding="utf-8",
                    dir=target.parent,
                    prefix=f".{target.name}.",
                    suffix=".tmp",
                    delete=False,
                ) as temporary:
                    temporary.write(content)
                    temporary.flush()
                    os.fsync(temporary.fileno())
                    temporary_path = Path(temporary.name)
                os.replace(temporary_path, target)
            finally:
                if temporary_path is not None and temporary_path.exists():
                    temporary_path.unlink()

    def exists(self, key: str) -> bool:
        _validate_store_key(key)
        return (self.root / key).is_file()

    def list_keys(self, prefix: str) -> list[str]:
        _validate_store_key(prefix)
        base = self.root / prefix
        if not base.exists():
            return []
        return sorted(
            # Object keys are POSIX on every backend. `str(Path)` would leak
            # backslashes on Windows and then fail the same key invariant readers
            # correctly enforce for S3/local parity.
            path.relative_to(self.root).as_posix()
            for path in base.rglob("*")
            if path.is_file()
        )

    def object_uri(self, key: str) -> str:
        _validate_store_key(key)
        return str((self.root / key).resolve())


@dataclass(frozen=True)
class S3ArchiveStore:
    bucket: str
    prefix: str
    base_uri: str

    def _full_key(self, key: str) -> str:
        _validate_store_key(key)
        return "/".join(part for part in (self.prefix.strip("/"), key) if part)

    @cached_property
    def client(self) -> Any:
        import boto3

        return boto3.client("s3")

    def put_file(self, key: str, source: Path) -> None:
        self.client.upload_file(str(source), self.bucket, self._full_key(key))

    def put_bytes(self, key: str, content: bytes) -> None:
        self.client.put_object(
            Bucket=self.bucket,
            Key=self._full_key(key),
            Body=content,
            ContentType="application/octet-stream",
        )

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

    def get_file(self, key: str, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        self.client.download_file(self.bucket, self._full_key(key), str(destination))

    def get_bytes(self, key: str) -> bytes:
        response = self.client.get_object(Bucket=self.bucket, Key=self._full_key(key))
        return response["Body"].read()

    def get_text_with_version(self, key: str) -> TextObject:
        response = self.client.get_object(Bucket=self.bucket, Key=self._full_key(key))
        return TextObject(
            content=response["Body"].read().decode("utf-8"),
            version=response["ETag"],
        )

    def put_text_if_version(
        self, key: str, content: str, expected_version: str | None
    ) -> None:
        from botocore.exceptions import ClientError

        common = {
            "Bucket": self.bucket,
            "Key": self._full_key(key),
            "Body": content.encode("utf-8"),
            "ContentType": "application/json",
        }
        try:
            if expected_version is None:
                self.client.put_object(**common, IfNoneMatch="*")
            else:
                self.client.put_object(**common, IfMatch=expected_version)
        except ClientError as exc:
            status = exc.response["ResponseMetadata"]["HTTPStatusCode"]
            if status in (409, 412):
                raise ConcurrentArchiveWriteError(
                    f"Archive metadata changed concurrently: {key}"
                ) from exc
            raise

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
        # Treat callers' prefix as an object namespace, not a raw string prefix;
        # `manifests` must never include sibling keys such as `manifests-old/...`.
        full_prefix = self._full_key(prefix).rstrip("/") + "/"
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
    # urlparse treats a Windows drive letter as a URI scheme. A drive-qualified
    # path is always local and must be classified before URI parsing.
    if _is_windows_drive_path(uri):
        root = Path(uri).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        return LocalArchiveStore(root=root, base_uri=str(root))
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
        # `urlparse()` leaves Windows file URIs as `/C:/...`; `Path` interprets
        # that form inconsistently. The stdlib conversion preserves drive roots
        # on Windows and remains an identity-style conversion on POSIX.
        from urllib.request import url2pathname

        root = Path(url2pathname(parsed.path)).expanduser().resolve()
    elif parsed.scheme == "":
        root = Path(uri).expanduser().resolve()
    else:
        raise ValueError(f"Unsupported archive URI scheme: {parsed.scheme}")
    root.mkdir(parents=True, exist_ok=True)
    return LocalArchiveStore(root=root, base_uri=str(root))


def _is_windows_drive_path(uri: str) -> bool:
    return len(uri) >= 3 and uri[0].isalpha() and uri[1] == ":" and uri[2] in "/\\"


def _validate_store_key(key: str) -> None:
    path = PurePosixPath(key)
    if (
        not key
        or key.startswith("/")
        or "\\" in key
        or any(part in ("", ".", "..") for part in key.split("/"))
        or path.as_posix() != key
    ):
        raise ValueError("Archive object key must be normalized and relative")


def _content_version(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


@contextmanager
def _exclusive_file_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as lock:
        if lock.tell() == 0:
            lock.write(b"\0")
            lock.flush()
        lock.seek(0)
        if os.name == "nt":
            import msvcrt

            try:
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise ConcurrentArchiveWriteError(
                    f"Archive metadata is being published concurrently: {path.name}"
                ) from exc
            try:
                yield
            finally:
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            return

        import fcntl

        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ConcurrentArchiveWriteError(
                f"Archive metadata is being published concurrently: {path.name}"
            ) from exc
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
