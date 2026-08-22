"""Shared dataset identity resolution without command-layer dependencies."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

if TYPE_CHECKING:
    from langsmith import Client
    from langsmith.schemas import Dataset


class DatasetResolutionError(ValueError):
    """A cloud Dataset identity cannot be resolved from the supplied reference."""


def resolve_dataset(client: Client, name_or_id: str) -> Dataset:
    """Resolve one cloud Dataset by stable UUID or human-facing name.

    Syntactic identity selection happens before the SDK call, so authorization,
    transport, and server failures propagate with their original typed errors.
    """
    from langsmith.utils import LangSmithNotFoundError

    try:
        UUID(name_or_id)
    except ValueError:
        try:
            return client.read_dataset(dataset_name=name_or_id)
        except LangSmithNotFoundError as exc:
            raise DatasetResolutionError(f"Dataset '{name_or_id}' not found.") from exc
    return client.read_dataset(dataset_id=name_or_id)
