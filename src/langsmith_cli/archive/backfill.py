"""Import completed range exports into the canonical daily archive."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

from langsmith_cli.archive.bulk import BulkExportSnapshot
from langsmith_cli.archive.models import ArchivePhase
from langsmith_cli.archive.repository import (
    ManifestSnapshot,
    manifest_key,
    read_manifest_snapshot,
)
from langsmith_cli.archive.storage import ArchiveStore
from langsmith_cli.archive.sync import sync_project_day


@dataclass(frozen=True)
class BackfillImportResult:
    export_id: str
    imported_days: int
    skipped_days: int
    canonical_run_count: int


@dataclass(frozen=True)
class _CompletedDayExporter:
    project_id: str
    snapshot: BulkExportSnapshot

    def export_window(
        self,
        *,
        project_id: str,
        start_time: datetime,
        end_time: datetime,
        excluded_export_ids: frozenset[str],
    ) -> BulkExportSnapshot:
        if project_id != self.project_id:
            raise ValueError("Backfill snapshot project identity changed")
        if start_time != self.snapshot.start_time or end_time != self.snapshot.end_time:
            raise ValueError("Backfill snapshot window changed")
        return self.snapshot


def import_backfill_snapshot(
    store: ArchiveStore,
    *,
    project_id: str,
    project_name: str,
    snapshot: BulkExportSnapshot,
) -> BackfillImportResult:
    """Publish one completed range export as sealed canonical project-days."""
    if snapshot.start_time.time() != time.min or snapshot.end_time.time() != time.min:
        raise ValueError("Backfill range must align to UTC day boundaries")
    start_date = snapshot.start_time.date()
    end_date = snapshot.end_time.date()
    manifest_keys = set(store.list_keys(f"manifests/project_id={project_id}"))
    imported_days = 0
    skipped_days = 0
    canonical_run_count = 0
    trace_date = start_date
    while trace_date < end_date:
        key = manifest_key(project_id, trace_date.isoformat())
        existing: ManifestSnapshot | None = (
            read_manifest_snapshot(store, key, known_exists=True)
            if key in manifest_keys
            else None
        )
        # INVARIANT: resume may skip work, never identity validation. Otherwise a
        # rename/route move could make sealed data look successfully backfilled
        # under a project name that the published manifest does not contain.
        if existing is not None and existing.manifest.project_name != project_name:
            raise ValueError(
                "Archived project name changed; migrate its manifest before backfill"
            )
        if existing is not None and existing.manifest.sealed:
            skipped_days += 1
            canonical_run_count += existing.manifest.canonical_run_count
            trace_date += timedelta(days=1)
            continue
        day_snapshot = snapshot.for_utc_date(trace_date)
        manifest = sync_project_day(
            None,
            store,
            project_id=project_id,
            project_name=project_name,
            trace_date=trace_date,
            phase=ArchivePhase.RECONCILIATION,
            existing_snapshot=existing,
            manifest_known_absent=existing is None,
            bulk_exporter=_CompletedDayExporter(project_id, day_snapshot),
        )
        imported_days += 1
        canonical_run_count += manifest.canonical_run_count
        manifest_keys.add(key)
        trace_date += timedelta(days=1)
    return BackfillImportResult(
        export_id=snapshot.export_id,
        imported_days=imported_days,
        skipped_days=skipped_days,
        canonical_run_count=canonical_run_count,
    )


def backfill_window(start_date: date, end_date: date) -> tuple[datetime, datetime]:
    if end_date <= start_date:
        raise ValueError("Backfill end date must be after start date")
    return (
        datetime.combine(start_date, time.min, tzinfo=timezone.utc),
        datetime.combine(end_date, time.min, tzinfo=timezone.utc),
    )
