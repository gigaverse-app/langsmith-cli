"""Strict contracts for the additive local trace inventory."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from pathlib import PurePosixPath

from pydantic import BaseModel, ConfigDict, field_validator, model_validator


LOCAL_TRACE_SCHEMA_VERSION = 1


class TraceSource(str, Enum):
    CLOUD = "cloud"
    ARCHIVE = "archive"
    LOCAL = "local"


class TraceDestination(str, Enum):
    LOCAL = "local"


class TraceCacheStatus(str, Enum):
    HEALTHY = "healthy"


class TracePullRequest(BaseModel):
    """Auditable description of one explicit transfer into the local inventory."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source: TraceSource
    project_id: str
    project_name: str
    requested_at: datetime
    since: datetime | None = None
    before: datetime | None = None
    filter: str | None = None
    trace_ids: tuple[str, ...] = ()

    @field_validator("requested_at", "since", "before")
    @classmethod
    def _normalize_datetime(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Trace pull timestamps must be timezone-aware")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def _require_remote_source_and_bounds(self) -> "TracePullRequest":
        if self.source is TraceSource.LOCAL:
            raise ValueError("A local trace pull must originate outside local")
        if not self.project_id or not self.project_name:
            raise ValueError("Trace pulls require stable project ID and name")
        if (
            self.since is not None
            and self.before is not None
            and self.since >= self.before
        ):
            raise ValueError("Trace pull since must be earlier than before")
        return self


class TraceSelection(BaseModel):
    """Typed source selector before remote rows reveal their project UUID."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source: TraceSource
    project_name: str
    requested_at: datetime
    since: datetime | None = None
    before: datetime | None = None
    filter: str | None = None
    limit: int | None = 100

    @field_validator("requested_at", "since", "before")
    @classmethod
    def _normalize_selection_datetime(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Trace selection timestamps must be timezone-aware")
        return value.astimezone(timezone.utc)

    @field_validator("limit")
    @classmethod
    def _require_non_negative_selection_limit(cls, value: int | None) -> int | None:
        if value is not None and value < 0:
            raise ValueError("Trace selection limit must be non-negative")
        return value

    @model_validator(mode="after")
    def _validate_selection(self) -> "TraceSelection":
        if self.source is TraceSource.LOCAL:
            raise ValueError("Local is a destination, not a remote pull source")
        if not self.project_name:
            raise ValueError("Trace selection requires an exact project name")
        if (
            self.since is not None
            and self.before is not None
            and self.since >= self.before
        ):
            raise ValueError("Trace selection since must be earlier than before")
        return self


class TraceFragment(BaseModel):
    """One immutable Parquet delta approved by the active catalog."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str
    sha256: str
    content_digest: str
    row_count: int
    project_id: str
    project_name: str
    origin: TraceSource
    observed_at: datetime

    @field_validator("key")
    @classmethod
    def _require_cache_fragment_key(cls, value: str) -> str:
        path = PurePosixPath(value)
        if (
            path.is_absolute()
            or ".." in path.parts
            or path.as_posix() != value
            or path.suffix != ".parquet"
            or path.parts[:2] != ("traces", "fragments")
        ):
            raise ValueError("Trace fragment key points outside the local trace cache")
        return value

    @field_validator("sha256", "content_digest")
    @classmethod
    def _require_sha256(cls, value: str) -> str:
        if len(value) != 64 or any(
            character not in "0123456789abcdef" for character in value
        ):
            raise ValueError("Trace fragment digests must be lowercase SHA-256")
        return value

    @field_validator("row_count")
    @classmethod
    def _require_rows(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("Trace fragments must contain at least one row")
        return value

    @field_validator("observed_at")
    @classmethod
    def _normalize_observed_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Trace fragment observation time must be timezone-aware")
        return value.astimezone(timezone.utc)


class TracePullRecord(BaseModel):
    """Coverage-ledger record for a successfully published explicit pull."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    request: TracePullRequest
    content_digest: str | None
    selected_run_count: int
    new_identity_count: int


class TraceCatalog(BaseModel):
    """Atomic reachability boundary for every queryable local trace fragment."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = LOCAL_TRACE_SCHEMA_VERSION
    fragments: tuple[TraceFragment, ...] = ()
    pulls: tuple[TracePullRecord, ...] = ()

    @model_validator(mode="after")
    def _validate_catalog_identity(self) -> "TraceCatalog":
        if self.schema_version != LOCAL_TRACE_SCHEMA_VERSION:
            raise ValueError(
                f"Unsupported local trace catalog schema: {self.schema_version}"
            )
        keys = [fragment.key for fragment in self.fragments]
        digests = [fragment.content_digest for fragment in self.fragments]
        if len(keys) != len(set(keys)) or len(digests) != len(set(digests)):
            raise ValueError("Local trace catalog contains duplicate fragments")
        return self


class TraceCacheWriteResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    added_run_count: int
    selected_run_count: int
    total_run_count: int
    fragment_count: int
    content_digest: str | None


class TraceProjectSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    project_id: str
    project_name: str
    run_count: int
    fragment_count: int
    oldest_run_start_time: datetime | None
    newest_run_start_time: datetime | None
    last_updated: datetime
    origins: tuple[TraceSource, ...]


class TraceEvictResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    removed_run_count: int
    removed_fragment_count: int
    remaining_run_count: int
    remaining_fragment_count: int


class ProjectTraceCacheWriteResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    project: str
    result: TraceCacheWriteResult


class TraceCacheHealth(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: TraceCacheStatus
    fragment_count: int
    run_count: int
