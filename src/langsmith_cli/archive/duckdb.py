"""Shared DuckDB setup for local and S3-backed archive operations."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
import tempfile
from typing import Any, Protocol


class DuckConnection(Protocol):
    def execute(self, query: str, parameters: object | None = None) -> Any: ...


def configure_duckdb_temp_directory(
    connection: DuckConnection, staging_directory: Path
) -> None:
    """Keep DuckDB spill files inside one process/project staging boundary."""
    spill_directory = staging_directory / "duckdb-spill"
    spill_directory.mkdir(parents=True, exist_ok=True)
    connection.execute("SET temp_directory = ?", [str(spill_directory)])


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
            configure_duckdb_temp_directory(connection, Path(connection_staging))
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
