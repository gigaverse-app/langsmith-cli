"""Typed list semantics shared by cloud, archive, and local dataset reads.

The SDK can perform some predicates and pages server-side, while replica stores
read complete snapshots. These contracts keep the observable CLI order fixed:
filter first, sort second, and paginate exactly once at the end.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable
from enum import Enum
from typing import Generic, TypeVar

from langsmith.schemas import DataType, Dataset, Example
from pydantic import BaseModel, ConfigDict, JsonValue

from langsmith_cli.utils import (
    apply_exclude_filter,
    apply_name_filters,
    sort_items,
)


Item = TypeVar("Item")


class DatasetSortField(str, Enum):
    NAME = "name"
    CREATED_AT = "created_at"
    EXAMPLE_COUNT = "example_count"


class ExampleSortField(str, Enum):
    CREATED_AT = "created_at"
    MODIFIED_AT = "modified_at"


class ReplicaListQuery(BaseModel, Generic[Item], ABC):
    """Strict base for source-independent list operations."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    limit: int
    offset: int = 0
    exclude: tuple[str, ...] = ()
    descending: bool = False

    def _page(self, items: list[Item]) -> list[Item]:
        """Apply the user page once, after every client-side operation."""
        return items[self.offset : self.offset + self.limit]

    @property
    @abstractmethod
    def requires_unbounded_cloud_fetch(self) -> bool:
        """Whether client-side work requires reading beyond the SDK page."""

    @property
    @abstractmethod
    def requires_global_materialization(self) -> bool:
        """Whether an operation, currently sorting, needs every eligible item."""

    @abstractmethod
    def _filter_replica(self, items: list[Item]) -> list[Item]:
        """Apply predicates the replica backend cannot execute itself."""

    @abstractmethod
    def _apply_client_operations(self, items: list[Item]) -> list[Item]:
        """Apply operations that remain client-side for every backend."""

    @property
    def cloud_limit(self) -> int | None:
        return None if self.requires_unbounded_cloud_fetch else self.limit

    def apply_to_replica(self, items: list[Item]) -> list[Item]:
        return self._page(self._apply_client_operations(self._filter_replica(items)))

    def apply_to_cloud_response(self, items: Iterable[Item]) -> list[Item]:
        # Cloud predicates remain authoritative. Reapplying their semantics
        # locally would create exactly the source drift this facade prevents.
        if not self.requires_unbounded_cloud_fetch:
            # The SDK already applied offset. Limit again to protect test fakes
            # and custom Client implementations that return too many rows.
            result: list[Item] = []
            for item in items:
                result.append(item)
                if len(result) == self.limit:
                    break
            return result
        if self.requires_global_materialization:
            return self._page(self._apply_client_operations(list(items)))

        # Filter-only queries consume lazily. Once offset + limit survivors have
        # arrived, later SDK pages cannot affect the selected page.
        survivors: list[Item] = []
        required = self.offset + self.limit
        for item in items:
            survivors.extend(self._apply_client_operations([item]))
            if len(survivors) == required:
                break
        return self._page(survivors)


class DatasetListQuery(ReplicaListQuery[Dataset]):
    dataset_ids: tuple[str, ...] | None = None
    data_type: DataType | None = None
    dataset_name: str | None = None
    name_contains: str | None = None
    metadata: dict[str, JsonValue] | None = None
    name_pattern: str | None = None
    name_regex: str | None = None
    sort_field: DatasetSortField | None = None

    @classmethod
    def from_options(
        cls,
        *,
        dataset_ids: list[str] | None,
        limit: int,
        data_type: str | None,
        dataset_name: str | None,
        name_contains: str | None,
        metadata: dict[str, JsonValue] | None,
        name_pattern: str | None,
        name_regex: str | None,
        sort_by: str | None,
        exclude: tuple[str, ...],
    ) -> DatasetListQuery:
        sort_field, descending = _parse_sort(sort_by, DatasetSortField)
        return cls(
            dataset_ids=tuple(dataset_ids) if dataset_ids is not None else None,
            limit=limit,
            data_type=DataType(data_type) if data_type is not None else None,
            dataset_name=dataset_name,
            name_contains=name_contains,
            metadata=metadata,
            name_pattern=name_pattern,
            name_regex=name_regex,
            sort_field=sort_field,
            descending=descending,
            exclude=exclude,
        )

    @property
    def requires_unbounded_cloud_fetch(self) -> bool:
        """Whether the SDK page must be deferred until after local operations."""
        return bool(
            self.name_pattern
            or self.name_regex
            or self.exclude
            or self.sort_field is not None
        )

    @property
    def requires_global_materialization(self) -> bool:
        return self.sort_field is not None

    def _filter_replica(self, items: list[Dataset]) -> list[Dataset]:
        selected = items
        if self.dataset_ids is not None:
            selected_ids = set(self.dataset_ids)
            selected = [item for item in selected if str(item.id) in selected_ids]
        if self.data_type is not None:
            selected = [item for item in selected if item.data_type == self.data_type]
        if self.dataset_name is not None:
            selected = [item for item in selected if item.name == self.dataset_name]
        if self.name_contains is not None:
            selected = [item for item in selected if self.name_contains in item.name]
        if self.metadata is not None:
            selected = [
                item
                for item in selected
                if _metadata_contains(item.metadata, self.metadata)
            ]
        return selected

    def _apply_client_operations(self, items: list[Dataset]) -> list[Dataset]:
        selected = items
        selected = apply_name_filters(
            selected,
            lambda item: item.name,
            name_pattern=self.name_pattern,
            name_regex=self.name_regex,
        )
        selected = apply_exclude_filter(selected, self.exclude, lambda item: item.name)
        if self.sort_field is not None:
            selected = sort_items(
                selected,
                _sort_expression(self.sort_field, self.descending),
                {
                    DatasetSortField.NAME.value: lambda item: item.name,
                    DatasetSortField.CREATED_AT.value: lambda item: item.created_at,
                    DatasetSortField.EXAMPLE_COUNT.value: (
                        lambda item: (
                            item.example_count if item.example_count is not None else 0
                        )
                    ),
                },
            )
        return selected


class ExampleListQuery(ReplicaListQuery[Example]):
    example_ids: tuple[str, ...] | None = None
    metadata: dict[str, JsonValue] | None = None
    splits: tuple[str, ...] | None = None
    sort_field: ExampleSortField | None = None

    @classmethod
    def from_options(
        cls,
        *,
        example_ids: list[str] | None,
        limit: int,
        offset: int,
        metadata: dict[str, JsonValue] | None,
        splits: list[str] | None,
        sort_by: str | None,
        exclude: tuple[str, ...],
    ) -> ExampleListQuery:
        sort_field, descending = _parse_sort(sort_by, ExampleSortField)
        return cls(
            example_ids=tuple(example_ids) if example_ids is not None else None,
            limit=limit,
            offset=offset,
            metadata=metadata,
            splits=tuple(splits) if splits is not None else None,
            sort_field=sort_field,
            descending=descending,
            exclude=exclude,
        )

    @property
    def requires_unbounded_cloud_fetch(self) -> bool:
        return bool(self.exclude or self.sort_field is not None)

    @property
    def requires_global_materialization(self) -> bool:
        return self.sort_field is not None

    @property
    def cloud_offset(self) -> int:
        return 0 if self.requires_unbounded_cloud_fetch else self.offset

    def _filter_replica(self, items: list[Example]) -> list[Example]:
        selected = items
        if self.example_ids is not None:
            selected_ids = set(self.example_ids)
            selected = [item for item in selected if str(item.id) in selected_ids]
        if self.metadata is not None:
            selected = [
                item
                for item in selected
                if _metadata_contains(item.metadata, self.metadata)
            ]
        if self.splits is not None:
            selected_splits = set(self.splits)
            selected = [
                item
                for item in selected
                if bool(selected_splits.intersection(_example_splits(item)))
            ]
        return selected

    def _apply_client_operations(self, items: list[Example]) -> list[Example]:
        selected = items
        selected = apply_exclude_filter(
            selected, self.exclude, lambda item: str(item.id)
        )
        if self.sort_field is not None:
            selected = sort_items(
                selected,
                _sort_expression(self.sort_field, self.descending),
                {
                    ExampleSortField.CREATED_AT.value: lambda item: item.created_at,
                    ExampleSortField.MODIFIED_AT.value: lambda item: (
                        item.modified_at or item.created_at
                    ),
                },
            )
        return selected


SortField = TypeVar("SortField", bound=Enum)


def _parse_sort(
    value: str | None, field_type: type[SortField]
) -> tuple[SortField | None, bool]:
    if value is None:
        return None, False
    descending = value.startswith("-")
    return field_type(value.removeprefix("-")), descending


def _sort_expression(field: Enum, descending: bool) -> str:
    prefix = "-" if descending else ""
    return prefix + str(field.value)


def _metadata_contains(
    actual: dict[str, object] | None, expected: dict[str, JsonValue]
) -> bool:
    return actual is not None and all(
        key in actual and actual[key] == value for key, value in expected.items()
    )


def _example_splits(example: Example) -> tuple[str, ...]:
    if example.metadata is None or "dataset_split" not in example.metadata:
        return ()
    value = example.metadata["dataset_split"]
    if isinstance(value, str):
        return (value,)
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return tuple(value)
    return ()
