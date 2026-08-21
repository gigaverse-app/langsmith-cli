"""Shared DuckDB setup for local and S3-backed archive operations."""

from __future__ import annotations

from typing import Any, Protocol


class DuckConnection(Protocol):
    def execute(self, query: str, parameters: object | None = None) -> Any: ...


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
