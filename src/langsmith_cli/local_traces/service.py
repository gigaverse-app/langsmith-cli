"""Source routing and explicit local trace materialization services."""

from __future__ import annotations

import os
from pathlib import Path

from langsmith_cli.local_traces.models import TraceSource


LOCAL_TRACE_DIRECTORY_ENV = "LANGSMITH_CLI_RUN_CACHE_DIR"


def default_local_trace_directory() -> Path:
    configured = os.environ.get(LOCAL_TRACE_DIRECTORY_ENV)
    if configured is not None and configured.strip():
        return Path(configured).expanduser()
    from platformdirs import user_cache_dir

    return Path(user_cache_dir("langsmith-cli", appauthor=False)) / "runs"


def local_trace_repository():
    from langsmith_cli.local_traces.repository import LocalTraceRepository

    return LocalTraceRepository(default_local_trace_directory())


def resolve_trace_source(source: str | None, archive: bool) -> TraceSource:
    """Resolve the named source and the compatibility alias without ambiguity."""
    if source is not None and archive:
        raise ValueError("Use either --source or --archive, not both")
    if archive:
        return TraceSource.ARCHIVE
    return TraceSource(source) if source is not None else TraceSource.CLOUD
