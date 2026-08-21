"""Organization-operated trace archive commands."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import TypedDict

import click

from langsmith_cli.archive.config import (
    ArchiveRoute,
    UnmatchedProjectError,
    UnknownRouteError,
    load_archive_config,
)
from langsmith_cli.archive.backfill import backfill_window, import_backfill_snapshot
from langsmith_cli.archive.bulk import BulkExportJob, LangSmithBulkExporter
from langsmith_cli.archive.models import (
    ArchiveManifestDict,
    ArchivePhase,
    ArchiveProject,
)
from langsmith_cli.archive.repository import (
    list_project_records,
    read_manifest,
    read_manifest_snapshot,
)
from langsmith_cli.archive.storage import ConcurrentArchiveWriteError, create_store
from langsmith_cli.archive.sync import due_trace_dates, sync_project_day
from langsmith_cli.output import json_dumps
from langsmith_cli.utils import get_or_create_client, is_json_context


class SyncResultDict(TypedDict):
    route: str
    archive_uri: str
    project_name: str
    project_id: str
    trace_date: str
    phase: str
    provider: str
    skipped: bool
    canonical_run_count: int
    sealed: bool


class BackfillProjectResultDict(TypedDict):
    project_name: str
    project_id: str
    export_id: str
    imported_days: int
    skipped_days: int
    canonical_run_count: int


@click.group()
def archive() -> None:
    """Export traces to organization-owned Parquet archives."""


def _selected_routes(
    config_path: str | None, route_name: str | None, all_routes: bool
) -> tuple[ArchiveRoute, ...]:
    config = load_archive_config(config_path)
    if route_name is not None and all_routes:
        raise click.ClickException("Use either --route or --all-routes, not both")
    if route_name is not None:
        try:
            return (config.route_named(route_name),)
        except UnknownRouteError as exc:
            raise click.ClickException(str(exc)) from exc
    if all_routes or len(config.routes) == 1:
        return config.routes
    raise click.ClickException(
        "Config has multiple archive routes; select --route NAME or --all-routes"
    )


@archive.command("sync")
@click.option("--config", "config_path", type=click.Path(exists=True, dir_okay=False))
@click.option("--route", "route_name", help="Sync one named archive route.")
@click.option("--all-routes", is_flag=True, help="Sync every configured route.")
@click.option("--project", "projects", multiple=True, help="Exact project to sync.")
@click.option("--retention-days", default=14, show_default=True, type=int)
@click.option(
    "--bulk-export-destination-id",
    envvar="LANGSMITH_BULK_EXPORT_DESTINATION_ID",
    help="Use a LangSmith-managed Bulk Export destination UUID.",
)
@click.option(
    "--date",
    "trace_date_text",
    help="Export one exact UTC trace date (YYYY-MM-DD); requires --phase.",
)
@click.option(
    "--phase",
    "phase_text",
    type=click.Choice([phase.value for phase in ArchivePhase]),
    help="Explicit phase for --date.",
)
@click.option(
    "--today",
    "today_text",
    help="Override current UTC date (YYYY-MM-DD); intended for repair/testing.",
)
@click.pass_context
def sync_archive(
    ctx: click.Context,
    config_path: str | None,
    route_name: str | None,
    all_routes: bool,
    projects: tuple[str, ...],
    retention_days: int,
    bulk_export_destination_id: str | None,
    trace_date_text: str | None,
    phase_text: str | None,
    today_text: str | None,
) -> None:
    """Run due D+2 primary and D+(retention-2) reconciliation exports."""
    routes = _selected_routes(config_path, route_name, all_routes)
    today = (
        date.fromisoformat(today_text)
        if today_text is not None
        else datetime.now(timezone.utc).date()
    )
    if (trace_date_text is None) != (phase_text is None):
        raise click.ClickException("--date and --phase must be provided together")
    due = (
        ((date.fromisoformat(trace_date_text), ArchivePhase(phase_text)),)
        if trace_date_text is not None and phase_text is not None
        else due_trace_dates(today, retention_days)
    )
    client = get_or_create_client(ctx)
    if bulk_export_destination_id is not None and len(routes) != 1:
        raise click.ClickException(
            "Bulk Export requires exactly one selected archive route"
        )
    bulk_exporter = (
        LangSmithBulkExporter.from_langsmith_client(
            client,
            destination_id=bulk_export_destination_id,
            archive_uri=routes[0].archive_uri,
        )
        if bulk_export_destination_id is not None
        else None
    )

    if projects:
        project_models = [client.read_project(project_name=name) for name in projects]
    else:
        project_models = list(client.list_projects(limit=None))

    results: list[SyncResultDict] = []
    unmatched_projects: list[str] = []
    config = load_archive_config(config_path)
    selected_names = {route.name for route in routes}
    stores = {route.name: create_store(route.archive_uri) for route in routes}
    manifest_keys = {
        route.name: set(stores[route.name].list_keys("manifests")) for route in routes
    }
    project_records: dict[str, dict[str, ArchiveProject]] = {
        route.name: {
            project.project_id: project
            for project in list_project_records(stores[route.name])
        }
        for route in routes
    }
    for project in project_models:
        try:
            matched_route = config.route_project(project.name)
        except UnmatchedProjectError as exc:
            if projects:
                raise click.ClickException(str(exc)) from exc
            unmatched_projects.append(project.name)
            continue
        if matched_route.name not in selected_names:
            if projects:
                raise click.ClickException(
                    f"Project {project.name} belongs to route {matched_route.name}, "
                    f"not the selected route"
                )
            continue

        store = stores[matched_route.name]
        for trace_date, phase in due:
            before_key = (
                f"manifests/project_id={project.id}/date={trace_date.isoformat()}.json"
            )
            before_snapshot = (
                read_manifest_snapshot(store, before_key, known_exists=True)
                if before_key in manifest_keys[matched_route.name]
                else None
            )
            skipped = (
                before_snapshot is not None
                and before_snapshot.manifest.phase(phase) is not None
            )
            try:
                manifest = sync_project_day(
                    client,
                    store,
                    project_id=str(project.id),
                    project_name=project.name,
                    trace_date=trace_date,
                    phase=phase,
                    existing_snapshot=before_snapshot,
                    manifest_known_absent=before_snapshot is None,
                    existing_project=project_records[matched_route.name].get(
                        str(project.id)
                    ),
                    project_record_checked=True,
                    bulk_exporter=bulk_exporter,
                )
            except ConcurrentArchiveWriteError as exc:
                raise click.ClickException(
                    "Archive manifest changed concurrently; rerun the sync safely"
                ) from exc
            manifest_keys[matched_route.name].add(before_key)
            project_records[matched_route.name][str(project.id)] = ArchiveProject(
                schema_version=1,
                project_id=str(project.id),
                project_name=project.name,
            )
            results.append(
                {
                    "route": matched_route.name,
                    "archive_uri": matched_route.archive_uri,
                    "project_name": project.name,
                    "project_id": str(project.id),
                    "trace_date": trace_date.isoformat(),
                    "phase": phase.value,
                    "provider": (
                        "bulk_export" if bulk_exporter is not None else "runs_api"
                    ),
                    "skipped": skipped,
                    "canonical_run_count": manifest.canonical_run_count,
                    "sealed": manifest.sealed,
                }
            )

    if is_json_context(ctx):
        click.echo(
            json_dumps(
                {
                    "processed": results,
                    "unmatched_projects": sorted(unmatched_projects),
                }
            )
        )
        return
    logger = ctx.obj["logger"]
    logger.success(f"Processed {len(results)} archive project-day phase(s)")
    if unmatched_projects:
        logger.warning(
            f"Skipped {len(unmatched_projects)} project(s) without an archive route"
        )


@archive.command("backfill")
@click.option("--config", "config_path", type=click.Path(exists=True, dir_okay=False))
@click.option("--route", "route_name", required=True, help="Backfill one route.")
@click.option("--project", "projects", multiple=True, help="Exact project to backfill.")
@click.option("--start-date", required=True, help="Inclusive UTC date (YYYY-MM-DD).")
@click.option("--end-date", required=True, help="Exclusive UTC date (YYYY-MM-DD).")
@click.option(
    "--bulk-export-destination-id",
    envvar="LANGSMITH_BULK_EXPORT_DESTINATION_ID",
    required=True,
    help="LangSmith-managed Bulk Export destination UUID.",
)
@click.option(
    "--bulk-export-timeout-hours",
    default=73.0,
    show_default=True,
    type=click.FloatRange(min=0, min_open=True),
    help="Maximum wait per historical project export.",
)
@click.pass_context
def backfill_archive(
    ctx: click.Context,
    config_path: str | None,
    route_name: str,
    projects: tuple[str, ...],
    start_date: str,
    end_date: str,
    bulk_export_destination_id: str,
    bulk_export_timeout_hours: float,
) -> None:
    """Export a historical range once and publish sealed daily partitions."""
    routes = _selected_routes(config_path, route_name, False)
    route = routes[0]
    window_start, window_end = backfill_window(
        date.fromisoformat(start_date), date.fromisoformat(end_date)
    )
    client = get_or_create_client(ctx)
    exporter = LangSmithBulkExporter.from_langsmith_client(
        client,
        destination_id=bulk_export_destination_id,
        archive_uri=route.archive_uri,
        timeout_seconds=bulk_export_timeout_hours * 60 * 60,
    )
    if projects:
        project_models = [client.read_project(project_name=name) for name in projects]
    else:
        project_models = list(client.list_projects(limit=None))
    config = load_archive_config(config_path)
    selected_projects: list[tuple[str, str]] = []
    unmatched_projects: list[str] = []
    for project in project_models:
        try:
            matched_route = config.route_project(project.name)
        except UnmatchedProjectError as exc:
            if projects:
                raise click.ClickException(str(exc)) from exc
            unmatched_projects.append(project.name)
            continue
        if matched_route.name != route.name:
            if projects:
                raise click.ClickException(
                    f"Project {project.name} belongs to route {matched_route.name}, "
                    f"not the selected route"
                )
            continue
        if project.name is None:
            raise click.ClickException("LangSmith project is missing its name")
        selected_projects.append((str(project.id), project.name))

    pending: list[tuple[str, str, BulkExportJob]] = []
    for project_id, project_name in selected_projects:
        job = exporter.begin_window(
            project_id=project_id,
            start_time=window_start,
            end_time=window_end,
            excluded_export_ids=frozenset(),
        )
        pending.append((project_id, project_name, job))

    store = create_store(route.archive_uri)
    results: list[BackfillProjectResultDict] = []
    for project_id, project_name, job in pending:
        snapshot = exporter.complete_export(job)
        imported = import_backfill_snapshot(
            store,
            project_id=project_id,
            project_name=project_name,
            snapshot=snapshot,
        )
        results.append(
            {
                "project_name": project_name,
                "project_id": project_id,
                "export_id": imported.export_id,
                "imported_days": imported.imported_days,
                "skipped_days": imported.skipped_days,
                "canonical_run_count": imported.canonical_run_count,
            }
        )

    payload = {
        "route": route.name,
        "archive_uri": route.archive_uri,
        "start_date": start_date,
        "end_date": end_date,
        "projects": results,
        "unmatched_projects": sorted(unmatched_projects),
    }
    if is_json_context(ctx):
        click.echo(json_dumps(payload))
        return
    logger = ctx.obj["logger"]
    logger.success(
        f"Backfilled {len(results)} project(s) from {start_date} to {end_date}"
    )


@archive.command("status")
@click.option("--config", "config_path", type=click.Path(exists=True, dir_okay=False))
@click.option("--route", "route_name")
@click.option("--all-routes", is_flag=True)
@click.pass_context
def archive_status(
    ctx: click.Context,
    config_path: str | None,
    route_name: str | None,
    all_routes: bool,
) -> None:
    """List published archive manifests."""
    routes = _selected_routes(config_path, route_name, all_routes)
    manifests: list[ArchiveManifestDict] = []
    for route in routes:
        store = create_store(route.archive_uri)
        for key in store.list_keys("manifests"):
            manifest = read_manifest(store, key, known_exists=True)
            if manifest is not None:
                manifests.append(manifest.to_dict())
    if is_json_context(ctx):
        click.echo(json_dumps(manifests))
        return
    logger = ctx.obj["logger"]
    logger.info(f"Found {len(manifests)} archive manifest(s)")
