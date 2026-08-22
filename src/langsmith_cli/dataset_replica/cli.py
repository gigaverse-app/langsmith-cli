"""Small Click adapters shared by dataset and example replica commands."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, TypeVar

import click

from langsmith_cli.dataset_replica.models import ReplicaSource

if TYPE_CHECKING:
    from langsmith_cli.dataset_replica.repository import DatasetReplicaRepository


CommandFunction = TypeVar("CommandFunction", bound=Callable[..., Any])


def replica_source_options(
    *,
    include_cloud: bool = True,
    default: ReplicaSource = ReplicaSource.CLOUD,
) -> Callable[[CommandFunction], CommandFunction]:
    """Apply the uniform source location options in one drift-free order."""
    choices = (
        list(ReplicaSource)
        if include_cloud
        else [ReplicaSource.ARCHIVE, ReplicaSource.LOCAL]
    )

    def decorate(function: CommandFunction) -> CommandFunction:
        function = click.option("--local-dir", type=click.Path(file_okay=False))(
            function
        )
        function = click.option("--archive-uri", envvar="LANGSMITH_ARCHIVE_URI")(
            function
        )
        function = click.option(
            "--source",
            type=click.Choice([item.value for item in choices]),
            default=default.value,
            show_default=True,
        )(function)
        return function

    return decorate


def replica_list_pagination_options(
    *, include_offset: bool = False, item_name: str
) -> Callable[[CommandFunction], CommandFunction]:
    """Apply validated list bounds consistently to every readable source."""

    def decorate(function: CommandFunction) -> CommandFunction:
        if include_offset:
            function = click.option(
                "--offset",
                type=click.IntRange(min=0),
                default=0,
                show_default=True,
                help=f"Number of {item_name} to skip after filtering and sorting.",
            )(function)
        function = click.option(
            "--limit",
            type=click.IntRange(min=1),
            default=20,
            show_default=True,
            help=f"Maximum number of {item_name} to return.",
        )(function)
        return function

    return decorate


def replica_repository(
    source: ReplicaSource,
    archive_uri: str | None,
    local_directory: str | None,
) -> DatasetReplicaRepository:
    """Open a repository lazily so normal cloud CLI startup stays lightweight."""
    from langsmith_cli.dataset_replica.service import repository_for

    return repository_for(
        source,
        archive_uri=archive_uri,
        local_directory=local_directory,
    )
