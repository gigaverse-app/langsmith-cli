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


class DuckConnection(Protocol):
    def execute(self, query: str, parameters: object | None = None) -> Any: ...


def configure_duckdb_resources(
    connection: DuckConnection, staging_directory: Path
) -> None:
    """Bound memory and isolate spill files inside one connection boundary."""
    spill_directory = staging_directory / "duckdb-spill"
    spill_directory.mkdir(parents=True, exist_ok=True)
    connection.execute("SET memory_limit = ?", [duckdb_memory_limit()])
    connection.execute("SET temp_directory = ?", [str(spill_directory)])
    # Insertion-order preservation blocks spilling for the canonicalization
    # union+window pipeline, holding a whole project-day in memory (real days OOMed
    # a 2.0 GiB bound in-cluster). The archive never relies on implicit row order:
    # reads order explicitly (ORDER BY start_time) and dedup ranks explicitly by
    # snapshot_rank. Tested by
    # test_duckdb_connections_do_not_preserve_insertion_order.
    connection.execute("SET preserve_insertion_order = false")


@contextmanager
def archive_duckdb_connection(
    staging_directory: Path | None = None,
) -> Iterator[DuckConnection]:
    """Open DuckDB with a unique spill directory and deterministic cleanup."""
    import duckdb

    with tempfile.TemporaryDirectory(
        prefix="langsmith-duckdb-", dir=staging_directory
    ) as connection_staging:
        connection = duckdb.connect()
        try:
            configure_duckdb_resources(connection, Path(connection_staging))
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
