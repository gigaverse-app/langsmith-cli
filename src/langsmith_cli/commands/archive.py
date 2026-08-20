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
    skipped: bool
    canonical_run_count: int
    sealed: bool


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
