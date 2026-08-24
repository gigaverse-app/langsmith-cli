"""Shared DuckDB setup for local and S3-backed archive operations."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import os
from pathlib import Path
import tempfile
from typing import Any, Protocol


# This is deliberately per connection. Bulk backfill opens several independent
# in-memory databases, and DuckDB's host-relative default lets each one claim most
# of the same host memory before spilling.
DUCKDB_MEMORY_LIMIT = "1.0 GiB"

# The default bound protects shared hosts, but real project-days can exceed it during
# canonicalization (observed: a Kubernetes daily sync OOMed at "916.1 MiB/1.0 GiB used"),
# and only the operator knows the host's actual memory budget. The override still
# applies per connection; an invalid value fails at configuration time via DuckDB's own
# SET validation rather than corrupting a sync later.
DUCKDB_MEMORY_LIMIT_ENV = "LANGSMITH_ARCHIVE_DUCKDB_MEMORY_LIMIT"


def duckdb_memory_limit() -> str:
    configured = os.environ.get(DUCKDB_MEMORY_LIMIT_ENV, "").strip()
    return configured or DUCKDB_MEMORY_LIMIT


# DuckDB defaults its thread count to the HOST's cores, not the container's CPU quota
# (8 threads inside a 1-CPU pod). Each thread holds its own read/write buffers — for
# JSON staging up to maximum_object_size per thread — so oversubscribed threads
# multiply the working set until real project-days OOM. The pod owner knows its CPU
# quota; the library does not, so this is an operator override with DuckDB's own
# default left untouched when unset.
DUCKDB_THREADS_ENV = "LANGSMITH_ARCHIVE_DUCKDB_THREADS"


def duckdb_threads() -> int | None:
    configured = os.environ.get(DUCKDB_THREADS_ENV, "").strip()
    return int(configured) if configured else None


# Trace rows carry multi-megabyte JSON text (inputs/outputs), so DuckDB's default
# row-COUNT-sized row groups buffer gigabytes before flushing, and that buffer cannot
# spill (real project-days OOMed a 1 GiB bound in-cluster). Bounding row groups by
# BYTES keeps the write buffer proportional to this constant instead of to payload
# width. Effective only with preserve_insertion_order=false, which
# configure_duckdb_resources also sets.
# Tested by test_archive_parquet_writers_bound_row_group_bytes.
ARCHIVE_PARQUET_COPY_OPTIONS = (
    "(FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE_BYTES '128MB')"
)


class DuckConnection(Protocol):
    def execute(self, query: str, parameters: object | None = None) -> Any: ...
    def fetchmany(self, size: int = 1) -> list[tuple[Any, ...]]: ...


def configure_duckdb_resources(
    connection: DuckConnection,
    staging_directory: Path,
    *,
    allowed_paths: list[Path] | None = None,
) -> None:
    """Bound memory and isolate spill files inside one connection boundary."""
    spill_directory = staging_directory / "duckdb-spill"
    spill_directory.mkdir(parents=True, exist_ok=True)
    connection.execute("SET memory_limit = ?", [duckdb_memory_limit()])
    connection.execute("SET temp_directory = ?", [str(spill_directory)])
    # INVARIANT: naive Parquet TIMESTAMP values represent LangSmith UTC. Pinning
    # DuckDB prevents host timezone from changing comparisons against aware CLI
    # bounds (a UTC row otherwise compared incorrectly on non-UTC workstations).
    connection.execute("SET TimeZone = 'UTC'")
    if allowed_paths is not None:
        # INVARIANT: local-cache SQL can read only catalog-approved fragments and
        # its private spill directory. Disabling autoload and external access
        # prevents a local query from becoming an implicit network/file read.
        approved = [str(path.resolve()) for path in allowed_paths]
        approved.append(str(spill_directory.resolve()))
        connection.execute("SET allowed_paths = ?", [approved])
        connection.execute("SET autoinstall_known_extensions = false")
        connection.execute("SET autoload_known_extensions = false")
        connection.execute("SET enable_external_access = false")
    # Insertion-order preservation blocks spilling for the canonicalization
    # union+window pipeline, holding a whole project-day in memory (real days OOMed
    # a 2.0 GiB bound in-cluster). The archive never relies on implicit row order:
    # reads order explicitly (ORDER BY start_time) and dedup ranks explicitly by
    # snapshot_rank. Tested by
    # test_duckdb_connections_do_not_preserve_insertion_order.
    connection.execute("SET preserve_insertion_order = false")
    threads = duckdb_threads()
    if threads is not None:
        connection.execute("SET threads = ?", [threads])


@contextmanager
def archive_duckdb_connection(
    staging_directory: Path | None = None,
    *,
    database_path: Path | None = None,
    allowed_paths: list[Path] | None = None,
) -> Iterator[DuckConnection]:
    """Open DuckDB with a unique spill directory and deterministic cleanup.

    Most archive transforms use an in-memory catalog. Callers ingesting an
    unbounded stream can opt into a temporary on-disk database so buffered table
    pages remain evictable under DuckDB's memory limit.
    """
    import duckdb

    with tempfile.TemporaryDirectory(
        prefix="langsmith-duckdb-", dir=staging_directory
    ) as connection_staging:
        connection = duckdb.connect(
            str(database_path) if database_path is not None else ":memory:"
        )
        try:
            configure_duckdb_resources(
                connection,
                Path(connection_staging),
                allowed_paths=allowed_paths,
            )
            yield connection
        finally:
            connection.close()


def configure_duckdb_s3(connection: DuckConnection, uris: list[str]) -> None:
    """Use the workload's AWS credential chain only when an S3 URI is present."""
    if not any(uri.startswith("s3://") for uri in uris):
        return
    # DuckDB loads httpfs on first S3 access. This secret delegates authentication
    # to the same short-lived workload identity used by boto3; no key is persisted.
    connection.execute(
        "CREATE OR REPLACE SECRET langsmith_archive_s3 "
        "(TYPE s3, PROVIDER credential_chain)"
    )
