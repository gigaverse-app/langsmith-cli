"""Strongly typed archive contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from enum import Enum
from pathlib import PurePosixPath
from typing import NotRequired, TypedDict
from uuid import UUID


ARCHIVE_SCHEMA_VERSION = 2
SUPPORTED_ARCHIVE_SCHEMA_VERSIONS = frozenset({1, ARCHIVE_SCHEMA_VERSION})


class ArchivePhase(str, Enum):
    PRIMARY = "primary"
    RECONCILIATION = "reconciliation"


class PhaseStatus(str, Enum):
    PENDING = "pending"
    EXPORTING = "exporting"
    VERIFIED = "verified"
    FAILED = "failed"


class PhaseRecordDict(TypedDict):
    status: str
    generation_id: str
    raw_key: str
    run_count: int
    verified_at: str
    error: NotRequired[str]


class ArchiveManifestDict(TypedDict):
    schema_version: int
    project_id: str
    project_name: str
    trace_date: str
    window_start: str
    window_end: str
    primary: PhaseRecordDict | None
    reconciliation: PhaseRecordDict | None
    canonical_key: str | None
    canonical_run_count: int
    sealed: bool
    updated_at: str


class ArchiveProjectDict(TypedDict):
    schema_version: int
    project_id: str
    project_name: str


@dataclass(frozen=True)
class ArchiveProject:
    """Stable route-local catalog entry used to prune manifest discovery."""

    schema_version: int
    project_id: str
    project_name: str

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ValueError(
                f"Unsupported archive project schema version: {self.schema_version}"
            )
        _require_uuid(self.project_id, "archive project_id")
        if not self.project_name:
            raise ValueError("Archive project_name must not be empty")

    def to_dict(self) -> ArchiveProjectDict:
        return {
            "schema_version": self.schema_version,
            "project_id": self.project_id,
            "project_name": self.project_name,
        }

    @classmethod
    def from_dict(cls, data: ArchiveProjectDict) -> ArchiveProject:
        return cls(
            schema_version=data["schema_version"],
            project_id=data["project_id"],
            project_name=data["project_name"],
        )


@dataclass(frozen=True)
class PhaseRecord:
    status: PhaseStatus
    generation_id: str
    raw_key: str
    run_count: int
    verified_at: datetime
    error: str | None = None

    def __post_init__(self) -> None:
        _require_uuid(self.generation_id, "phase generation_id")
        _require_relative_object_key(self.raw_key, "phase raw_key")
        if self.run_count < 0:
            raise ValueError("Phase run_count must be non-negative")
        _require_aware_datetime(self.verified_at, "phase verified_at")

    def to_dict(self) -> PhaseRecordDict:
        data: PhaseRecordDict = {
            "status": self.status.value,
            "generation_id": self.generation_id,
            "raw_key": self.raw_key,
            "run_count": self.run_count,
            "verified_at": self.verified_at.isoformat(),
        }
        if self.error is not None:
            data["error"] = self.error
        return data

    @classmethod
    def from_dict(cls, data: PhaseRecordDict) -> PhaseRecord:
        return cls(
            status=PhaseStatus(data["status"]),
            generation_id=data["generation_id"],
            raw_key=data["raw_key"],
            run_count=data["run_count"],
            verified_at=datetime.fromisoformat(data["verified_at"]),
            error=data["error"] if "error" in data else None,
        )


@dataclass(frozen=True)
class ArchiveManifest:
    schema_version: int
    project_id: str
    project_name: str
    trace_date: date
    window_start: datetime
    window_end: datetime
    primary: PhaseRecord | None
    reconciliation: PhaseRecord | None
    canonical_key: str | None
    canonical_run_count: int
    sealed: bool
    updated_at: datetime

    def __post_init__(self) -> None:
        """Enforce structural invariants for both working and published manifests."""
        if self.schema_version not in SUPPORTED_ARCHIVE_SCHEMA_VERSIONS:
            raise ValueError(
                f"Unsupported archive schema version: {self.schema_version}"
            )
        _require_uuid(self.project_id, "manifest project_id")
        if not self.project_name:
            raise ValueError("Manifest project_name must not be empty")

        expected_start = datetime.combine(
            self.trace_date, time.min, tzinfo=timezone.utc
        )
        expected_end = expected_start + timedelta(days=1)
        if self.window_start != expected_start or self.window_end != expected_end:
            raise ValueError("Manifest window must be exactly its UTC trace day")
        _require_aware_datetime(self.updated_at, "manifest updated_at")
        if self.canonical_run_count < 0:
            raise ValueError("Manifest canonical_run_count must be non-negative")

        self._validate_phase_key(ArchivePhase.PRIMARY, self.primary)
        self._validate_phase_key(ArchivePhase.RECONCILIATION, self.reconciliation)
        if self.canonical_key is not None:
            _require_relative_object_key(self.canonical_key, "manifest canonical_key")
            prefix = (
                f"canonical/project_id={self.project_id}/"
                f"date={self.trace_date.isoformat()}/generation="
            )
            if not self.canonical_key.startswith(prefix):
                raise ValueError("Manifest canonical_key does not match project/date")
            generation_and_name = self.canonical_key.removeprefix(prefix)
            generation_id, separator, filename = generation_and_name.partition("/")
            if separator != "/" or filename != "runs.parquet":
                raise ValueError("Manifest canonical_key has an invalid layout")
            _require_uuid(generation_id, "canonical generation_id")
        elif self.canonical_run_count != 0:
            raise ValueError(
                "Manifest without canonical_key must have zero canonical rows"
            )
        if self.sealed and not self._is_verified(self.reconciliation):
            raise ValueError("A sealed manifest requires verified reconciliation")

    @staticmethod
    def _is_verified(record: PhaseRecord | None) -> bool:
        return record is not None and record.status is PhaseStatus.VERIFIED

    def _validate_phase_key(
        self, phase: ArchivePhase, record: PhaseRecord | None
    ) -> None:
        if record is None:
            return
        expected = (
            f"raw/project_id={self.project_id}/date={self.trace_date.isoformat()}/"
            f"phase={phase.value}/generation={record.generation_id}/runs.parquet"
        )
        if record.raw_key != expected:
            raise ValueError(
                f"Manifest {phase.value} raw_key does not match project/date/phase"
            )

    def validate_publishable(self) -> None:
        """Enforce invariants that must hold at the manifest publication boundary."""
        records = [
            record
            for record in (self.primary, self.reconciliation)
            if record is not None
        ]
        if not records:
            raise ValueError("Published manifest requires a verified snapshot")
        if any(record.status is not PhaseStatus.VERIFIED for record in records):
            raise ValueError("Published manifest phases must be verified")
        if self.canonical_key is None:
            raise ValueError("Published manifest requires canonical_key")

        counts = [record.run_count for record in records]
        # Deduplicating a union cannot produce fewer rows than its largest unique
        # input or more rows than the sum of all inputs. This catches truncated or
        # accidentally duplicated canonical publications without reading Parquet.
        if not max(counts) <= self.canonical_run_count <= sum(counts):
            raise ValueError("Canonical count must be bounded by snapshot counts")
        reconciliation_verified = self._is_verified(self.reconciliation)
        if self.sealed is not reconciliation_verified:
            raise ValueError(
                "Manifest is sealed exactly when reconciliation is verified"
            )

    def phase(self, phase: ArchivePhase) -> PhaseRecord | None:
        if phase is ArchivePhase.PRIMARY:
            return self.primary
        return self.reconciliation

    def with_phase(self, phase: ArchivePhase, record: PhaseRecord) -> ArchiveManifest:
        return ArchiveManifest(
            schema_version=self.schema_version,
            project_id=self.project_id,
            project_name=self.project_name,
            trace_date=self.trace_date,
            window_start=self.window_start,
            window_end=self.window_end,
            primary=record if phase is ArchivePhase.PRIMARY else self.primary,
            reconciliation=(
                record if phase is ArchivePhase.RECONCILIATION else self.reconciliation
            ),
            canonical_key=self.canonical_key,
            canonical_run_count=self.canonical_run_count,
            sealed=self.sealed,
            updated_at=record.verified_at,
        )

    def to_dict(self) -> ArchiveManifestDict:
        return {
            "schema_version": self.schema_version,
            "project_id": self.project_id,
            "project_name": self.project_name,
            "trace_date": self.trace_date.isoformat(),
            "window_start": self.window_start.isoformat(),
            "window_end": self.window_end.isoformat(),
            "primary": self.primary.to_dict() if self.primary is not None else None,
            "reconciliation": (
                self.reconciliation.to_dict()
                if self.reconciliation is not None
                else None
            ),
            "canonical_key": self.canonical_key,
            "canonical_run_count": self.canonical_run_count,
            "sealed": self.sealed,
            "updated_at": self.updated_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: ArchiveManifestDict) -> ArchiveManifest:
        primary_data = data["primary"]
        reconciliation_data = data["reconciliation"]
        return cls(
            schema_version=data["schema_version"],
            project_id=data["project_id"],
            project_name=data["project_name"],
            trace_date=date.fromisoformat(data["trace_date"]),
            window_start=datetime.fromisoformat(data["window_start"]),
            window_end=datetime.fromisoformat(data["window_end"]),
            primary=(PhaseRecord.from_dict(primary_data) if primary_data else None),
            reconciliation=(
                PhaseRecord.from_dict(reconciliation_data)
                if reconciliation_data
                else None
            ),
            canonical_key=data["canonical_key"],
            canonical_run_count=data["canonical_run_count"],
            sealed=data["sealed"],
            updated_at=datetime.fromisoformat(data["updated_at"]),
        )


def _require_uuid(value: str, field: str) -> None:
    try:
        parsed = UUID(value)
    except ValueError as exc:
        raise ValueError(f"{field} must be a UUID") from exc
    if str(parsed) != value.lower():
        raise ValueError(f"{field} must use canonical UUID format")


def _require_aware_datetime(value: datetime, field: str) -> None:
    if (
        value.tzinfo is None
        or value.utcoffset() is None
        or value.utcoffset() != timedelta(0)
    ):
        raise ValueError(f"{field} must be timezone-aware UTC")


def _require_relative_object_key(value: str, field: str) -> None:
    path = PurePosixPath(value)
    if (
        not value
        or value.startswith("/")
        or "\\" in value
        or any(part in ("", ".", "..") for part in value.split("/"))
        or path.as_posix() != value
    ):
        raise ValueError(f"{field} must be a normalized relative object key")
