"""Archive route configuration and strict project matching."""

from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatchcase
import os
from pathlib import Path
from typing import TypedDict


class RouteConfigDict(TypedDict):
    name: str
    project_pattern: str
    archive_uri: str


class ArchiveConfigDict(TypedDict):
    routes: list[RouteConfigDict]


class ArchiveRouteError(ValueError):
    """Base class for deterministic archive routing failures."""


class UnknownRouteError(ArchiveRouteError):
    """A selected route name does not exist exactly once."""


class UnmatchedProjectError(ArchiveRouteError):
    """A project is not covered by archive configuration."""


class AmbiguousProjectRouteError(ArchiveRouteError):
    """A project is covered by multiple archive routes."""


@dataclass(frozen=True)
class ArchiveRoute:
    name: str
    project_pattern: str
    archive_uri: str

    def matches(self, project_name: str) -> bool:
        return fnmatchcase(project_name, self.project_pattern)


@dataclass(frozen=True)
class ArchiveConfig:
    routes: tuple[ArchiveRoute, ...]

    def route_named(self, name: str) -> ArchiveRoute:
        matches = [route for route in self.routes if route.name == name]
        if len(matches) != 1:
            raise UnknownRouteError(f"Archive route must exist exactly once: {name}")
        return matches[0]

    def route_project(self, project_name: str) -> ArchiveRoute:
        matches = [route for route in self.routes if route.matches(project_name)]
        if not matches:
            raise UnmatchedProjectError(
                f"No archive route matches project: {project_name}"
            )
        if len(matches) > 1:
            names = ", ".join(route.name for route in matches)
            raise AmbiguousProjectRouteError(
                f"Project matches multiple archive routes: {project_name} ({names})"
            )
        return matches[0]


def load_archive_config(path: str | None = None) -> ArchiveConfig:
    """Load non-secret route configuration or the single-URI environment shortcut."""
    resolved_path = path or os.environ.get("LANGSMITH_ARCHIVE_CONFIG")
    if resolved_path is None:
        archive_uri = os.environ.get("LANGSMITH_ARCHIVE_URI")
        if archive_uri is None:
            raise ValueError("Set LANGSMITH_ARCHIVE_URI or LANGSMITH_ARCHIVE_CONFIG")
        return ArchiveConfig(
            routes=(
                ArchiveRoute(
                    name="default", project_pattern="**", archive_uri=archive_uri
                ),
            )
        )

    import yaml

    loaded: object = yaml.safe_load(Path(resolved_path).read_text(encoding="utf-8"))
    if not isinstance(loaded, dict) or "routes" not in loaded:
        raise ValueError("Archive config must contain a routes list")
    raw_routes: object = loaded["routes"]
    if not isinstance(raw_routes, list):
        raise ValueError("Archive config routes must be a list")

    routes: list[ArchiveRoute] = []
    for raw_route in raw_routes:
        if not isinstance(raw_route, dict):
            raise ValueError("Each archive route must be an object")
        required = {"name", "project_pattern", "archive_uri"}
        if set(raw_route) != required:
            raise ValueError(
                "Each archive route requires only name, project_pattern, archive_uri"
            )
        name = raw_route["name"]
        project_pattern = raw_route["project_pattern"]
        archive_uri = raw_route["archive_uri"]
        if not all(
            isinstance(value, str) for value in (name, project_pattern, archive_uri)
        ):
            raise ValueError("Archive route fields must be strings")
        routes.append(
            ArchiveRoute(
                name=name,
                project_pattern=project_pattern,
                archive_uri=archive_uri,
            )
        )
    if not routes:
        raise ValueError("Archive config must contain at least one route")
    if len({route.name for route in routes}) != len(routes):
        raise ValueError("Archive route names must be unique")
    return ArchiveConfig(routes=tuple(routes))
