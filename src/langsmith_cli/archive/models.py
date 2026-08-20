"""Strongly typed archive contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import NotRequired, TypedDict


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


@dataclass(frozen=True)
class PhaseRecord:
    status: PhaseStatus
    generation_id: str
    raw_key: str
    run_count: int
    verified_at: datetime
    error: str | None = None

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
