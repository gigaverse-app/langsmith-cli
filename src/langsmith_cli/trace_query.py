"""Typed run-query contract shared by Parquet trace backends."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, field_validator, model_validator


TRACE_TEXT_FIELDS = frozenset({"inputs", "outputs", "error", "extra"})


class RunQuery(BaseModel):
    """Source-independent query subset implemented identically by DuckDB backends."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    project: str | None = None
    project_id: str | None = None
    project_name: str | None = None
    project_name_exact: str | None = None
    project_name_pattern: str | None = None
    project_name_regex: str | None = None
    since: datetime | None = None
    before: datetime | None = None
    limit: int | None = 20
    error: bool | None = None
    run_id: str | None = None
    trace_id: str | None = None
    trace_ids: tuple[str, ...] = ()
    run_type: str | None = None
    is_root: bool | None = None
    tags: tuple[str, ...] = ()
    text: str | None = None
    text_fields: tuple[str, ...] = ("inputs", "outputs", "error")
    text_regex: bool = False
    text_ignore_case: bool = False

    @field_validator("limit")
    @classmethod
    def _require_non_negative_limit(cls, value: int | None) -> int | None:
        if value is not None and value < 0:
            raise ValueError("Run query limit must be non-negative")
        return value

    @model_validator(mode="after")
    def _validate_text_search(self) -> "RunQuery":
        if self.trace_id is not None and self.trace_ids:
            raise ValueError("Use either trace_id or trace_ids, not both")
        if self.text is not None and not self.text_fields:
            raise ValueError("Run text search requires at least one field")
        invalid_fields = set(self.text_fields) - TRACE_TEXT_FIELDS
        if invalid_fields:
            invalid = ", ".join(sorted(invalid_fields))
            raise ValueError(f"Unsupported run text field(s): {invalid}")
        return self
