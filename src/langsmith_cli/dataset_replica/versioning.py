"""One version-selection contract for repository reads and transfers."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, field_validator

from langsmith_cli.dataset_replica.models import parse_datetime


class SelectableVersion(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    position: int
    as_of: datetime
    tags: tuple[str, ...] = ()

    @field_validator("as_of")
    @classmethod
    def require_aware_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Dataset version timestamps must be timezone-aware")
        return value


class VersionSelectionError(ValueError):
    """Base error for an absent or ambiguous version reference."""


class NoVersionsError(VersionSelectionError):
    pass


class VersionNotFoundError(VersionSelectionError):
    pass


class VersionAmbiguousError(VersionSelectionError):
    pass


def select_version(
    versions: list[SelectableVersion], requested: str | None
) -> SelectableVersion:
    """Resolve latest, an exact tag, or the newest timestamp at/before a bound."""
    if not versions:
        raise NoVersionsError("Dataset replica has no versions")
    if requested is None or requested == "latest":
        return max(versions, key=lambda item: item.as_of)

    try:
        requested_time = parse_datetime(requested)
    except ValueError:
        matching = [item for item in versions if requested in item.tags]
        if not matching:
            raise VersionNotFoundError(f"Dataset version tag not found: {requested}")
        if len(matching) > 1:
            raise VersionAmbiguousError(
                f"Dataset version tag is ambiguous: {requested}"
            )
        return matching[0]

    eligible = [item for item in versions if item.as_of <= requested_time]
    if not eligible:
        raise VersionNotFoundError(
            f"No dataset version exists at or before {requested}"
        )
    return max(eligible, key=lambda item: item.as_of)
