"""Project resolution, fetch helpers, and multi-project query utilities."""

import re
from dataclasses import dataclass
from typing import Any, Callable, Generic, TypeVar

import click
import langsmith
from langsmith.utils import LangSmithError, LangSmithNotFoundError
from pydantic import BaseModel, Field

from langsmith_cli.utils import apply_regex_filter, apply_wildcard_filter

T = TypeVar("T")


class CLIFetchError(click.ClickException):
    """ClickException with structured failure data for JSON mode.

    Carries failed_sources and suggestions so the global error handler
    in main.py can produce structured JSON output instead of a flat message.
    """

    def __init__(
        self,
        message: str,
        failed_sources: list[tuple[str, str]] | None = None,
        suggestions: list[str] | None = None,
    ) -> None:
        super().__init__(message)
        self.failed_sources = failed_sources or []
        self.suggestions = suggestions or []


class FetchResult(BaseModel, Generic[T]):
    """Result of fetching items from multiple projects/sources.

    Tracks both successful items and failed sources for proper error reporting.
    """

    model_config = {"arbitrary_types_allowed": True}

    items: list[T]
    successful_sources: list[str]
    failed_sources: list[tuple[str, str]] = Field(
        default_factory=list, description="(source_name, error_message)"
    )
    item_source_map: dict[str, str] = Field(
        default_factory=dict,
        description="Maps item ID (str) to source name for project attribution",
    )

    @property
    def has_failures(self) -> bool:
        """Check if any sources failed."""
        return len(self.failed_sources) > 0

    @property
    def all_failed(self) -> bool:
        """Check if ALL sources failed (no successful sources).

        This indicates a complete failure - the user should be notified
        and the CLI should return a non-zero exit code.
        """
        return len(self.successful_sources) == 0 and len(self.failed_sources) > 0

    @property
    def total_sources(self) -> int:
        """Total number of sources attempted."""
        return len(self.successful_sources) + len(self.failed_sources)

    def report_failures(self, console: Any, max_show: int = 3) -> None:
        """Report failures to console.

        Args:
            console: Console object (Rich Console or ConsoleProtocol)
            max_show: Maximum number of failures to show (default 3)
        """
        if not self.has_failures:
            return

        console.print("[yellow]Warning: Some sources failed to fetch:[/yellow]")
        for source, error_msg in self.failed_sources[:max_show]:
            # Truncate long error messages
            short_error = error_msg[:100] + "..." if len(error_msg) > 100 else error_msg
            console.print(f"  • {source}: {short_error}")

        if len(self.failed_sources) > max_show:
            remaining = len(self.failed_sources) - max_show
            console.print(f"  ... and {remaining} more")

    def report_failures_to_logger(self, logger: Any, max_show: int = 3) -> None:
        """Report failures using the CLI logger.

        Use this instead of report_failures() when you need proper
        stdout/stderr separation (e.g., in JSON mode).

        Args:
            logger: CLILogger instance
            max_show: Maximum number of failures to show (default 3)
        """
        if not self.has_failures:
            return

        if self.all_failed:
            logger.error("All sources failed to fetch:")
        else:
            logger.warning("Some sources failed to fetch:")

        for source, error_msg in self.failed_sources[:max_show]:
            # Truncate long error messages
            short_error = error_msg[:100] + "..." if len(error_msg) > 100 else error_msg
            logger.warning(f"  • {source}: {short_error}")

        if len(self.failed_sources) > max_show:
            remaining = len(self.failed_sources) - max_show
            logger.warning(f"  ... and {remaining} more")

    def raise_if_all_failed(
        self,
        logger: Any | None = None,
        entity_name: str = "runs",
        suggestions: list[str] | None = None,
    ) -> None:
        """Raise CLIFetchError if all sources failed.

        Use this for consistent error handling across commands. This method:
        1. Reports failures to logger (if provided)
        2. Logs suggestions if available
        3. Raises CLIFetchError with failure details and suggestions

        The CLIFetchError carries structured data (failed_sources, suggestions)
        so the global error handler in main.py can produce structured JSON output.

        Args:
            logger: Optional CLILogger for reporting (uses proper stderr in JSON mode)
            entity_name: What we were trying to fetch (e.g., "runs", "datasets")
            suggestions: Optional list of similar project names to suggest

        Raises:
            CLIFetchError: If all sources failed to fetch

        Example:
            result = fetch_from_projects(client, projects, fetch_func)
            result.raise_if_all_failed(logger, "runs")  # Raises if all failed
            # ... continue processing result.items ...
        """
        if not self.all_failed:
            return

        # Report failures if logger provided
        if logger:
            self.report_failures_to_logger(logger)
            if suggestions:
                suggestion_list = ", ".join(f"'{s}'" for s in suggestions[:5])
                logger.info(f"Did you mean: {suggestion_list}?")

        # Build human-readable message (also used as fallback in non-JSON mode)
        parts: list[str] = [
            f"Failed to fetch {entity_name} from all {self.total_sources} source(s)."
        ]
        for source, error_msg in self.failed_sources[:3]:
            short = error_msg[:100] + "..." if len(error_msg) > 100 else error_msg
            parts.append(f"  {source}: {short}")
        if suggestions:
            parts.append(
                f"Did you mean: {', '.join(repr(s) for s in suggestions[:5])}?"
            )

        raise CLIFetchError(
            "\n".join(parts),
            failed_sources=self.failed_sources,
            suggestions=suggestions or [],
        )


@dataclass
class ProjectQuery:
    """Resolved project query - either a list of names or a direct project ID.

    When project_id is set, commands should pass it directly to the SDK
    (e.g., client.list_runs(project_id=...)) instead of using project names.
    """

    names: list[str]
    project_id: str | None = None

    @property
    def use_id(self) -> bool:
        """Whether to use project_id instead of project names."""
        return self.project_id is not None


def fetch_from_projects(
    client: langsmith.Client,
    project_names: list[str],
    fetch_func: Callable[..., Any],
    *,
    project_query: ProjectQuery | None = None,
    limit: int | None = None,
    console: Any | None = None,
    show_warnings: bool = True,
    **fetch_kwargs: Any,
) -> FetchResult[Any]:
    """Universal helper to fetch items from multiple projects with error tracking.

    Args:
        client: LangSmith client instance
        project_names: List of project names to fetch from
        fetch_func: Function that takes (client, project_name, **kwargs) and returns items.
                    When project_query.use_id is True, the second arg is None and
                    project_id is included in fetch_kwargs.
        project_query: Optional ProjectQuery. When its use_id is True, project_id is
                       passed directly to fetch_func via fetch_kwargs instead of project names.
        limit: Optional limit on number of items to fetch per project
        console: Optional console for warnings
        show_warnings: Whether to automatically show warnings (default True)
        **fetch_kwargs: Additional kwargs passed to fetch_func

    Returns:
        FetchResult containing items, successful projects, and failed projects

    Example:
        >>> result = fetch_from_projects(
        ...     client,
        ...     ["proj1", "proj2"],
        ...     lambda c, proj, **kw: c.list_runs(project_name=proj, **kw),
        ...     limit=10,
        ...     console=console
        ... )
        >>> if result.has_failures:
        ...     result.report_failures(console)
    """
    # When project_id is available, bypass name-based iteration
    if project_query and project_query.use_id:
        try:
            items = fetch_func(
                client,
                None,
                limit=limit,
                project_id=project_query.project_id,
                **fetch_kwargs,
            )
            if not isinstance(items, list):
                items = list(items)
            result = FetchResult(
                items=items,
                successful_sources=[f"id:{project_query.project_id}"],
                failed_sources=[],
            )
            if show_warnings and result.has_failures and console:
                result.report_failures(console)
            return result
        except Exception as e:
            return FetchResult(
                items=[],
                successful_sources=[],
                failed_sources=[(f"id:{project_query.project_id}", str(e))],
            )

    all_items: list[Any] = []
    successful: list[str] = []
    failed: list[tuple[str, str]] = []

    for proj_name in project_names:
        try:
            # Call fetch function with project name and all kwargs
            items = fetch_func(client, proj_name, limit=limit, **fetch_kwargs)

            # Handle iterators (like client.list_runs returns)
            if hasattr(items, "__iter__") and not isinstance(items, (list, tuple)):
                items = list(items)

            all_items.extend(items)
            successful.append(proj_name)
        except Exception as e:
            failed.append((proj_name, str(e)))

    result = FetchResult(
        items=all_items, successful_sources=successful, failed_sources=failed
    )

    # Automatically show warnings if requested
    if show_warnings and result.has_failures and console:
        result.report_failures(console)

    return result


def get_or_create_client(ctx: Any) -> Any:
    """Get LangSmith client from context, or create if not exists.

    Note: langsmith module is imported at module level for testability,
    but Client instantiation is still lazy (only created when first needed).

    Args:
        ctx: Click context object

    Returns:
        LangSmith Client instance
    """
    if "client" not in ctx.obj:
        ctx.obj["client"] = langsmith.Client()
    return ctx.obj["client"]


def get_matching_items(
    items: list[Any],
    *,
    default_item: str | None = None,
    name: str | None = None,
    name_exact: str | None = None,
    name_pattern: str | None = None,
    name_regex: str | None = None,
    name_getter: Callable[[Any], str],
) -> list[Any]:
    """Get list of items matching the given filters.

    Universal helper for pattern matching across any item type.

    Filter precedence (most specific to least specific):
    1. name_exact - Exact match (highest priority)
    2. name_regex - Regular expression
    3. name_pattern - Wildcard pattern (*, ?)
    4. name - Substring/contains match
    5. default_item - Single item (default/fallback)

    Args:
        items: List of items to filter
        default_item: Single item (default fallback)
        name: Substring/contains match (convenience filter)
        name_exact: Exact name match
        name_pattern: Wildcard pattern (e.g., "dev/*", "*production*")
        name_regex: Regular expression pattern
        name_getter: Function to extract name from an item

    Returns:
        List of matching items

    Examples:
        # Single item (default)
        get_matching_items(projects, default_item="my-project", name_getter=lambda p: p.name)
        # -> [project_with_name_my_project]

        # Exact match
        get_matching_items(projects, name_exact="production-api", name_getter=lambda p: p.name)
        # -> [project_with_name_production_api] or []

        # Substring contains
        get_matching_items(projects, name="prod", name_getter=lambda p: p.name)
        # -> [production-api, production-web, dev-prod-test]

        # Wildcard pattern
        get_matching_items(projects, name_pattern="dev/*", name_getter=lambda p: p.name)
        # -> [dev/api, dev/web, dev/worker]

        # Regex pattern
        get_matching_items(projects, name_regex="^prod-.*-v[0-9]+$", name_getter=lambda p: p.name)
        # -> [prod-api-v1, prod-web-v2]
    """
    # Exact match has highest priority - return immediately if found
    if name_exact:
        matching = [item for item in items if name_getter(item) == name_exact]
        return matching

    # If a default item is given and no other filters, find and return just that item
    if default_item and not name and not name_pattern and not name_regex:
        # Try to find item with matching name
        matching = [item for item in items if name_getter(item) == default_item]
        if matching:
            return matching
        # If not found, assume default_item might be used elsewhere (e.g., for API calls)
        # Return empty list - caller will handle
        return []

    # Apply filters in order
    filtered_items = items

    # Apply regex filter (higher priority than wildcard)
    if name_regex:
        filtered_items = apply_regex_filter(filtered_items, name_regex, name_getter)

    # Apply wildcard pattern filter
    if name_pattern:
        filtered_items = apply_wildcard_filter(
            filtered_items, name_pattern, name_getter
        )

    # Apply substring/contains filter (lowest priority)
    if name:
        filtered_items = [item for item in filtered_items if name in name_getter(item)]

    return filtered_items


def get_matching_projects(
    client: langsmith.Client,
    *,
    project: str | None = None,
    name: str | None = None,
    name_exact: str | None = None,
    name_pattern: str | None = None,
    name_regex: str | None = None,
) -> list[str]:
    """Get list of project names matching the given filters.

    Universal helper for project pattern matching across all commands.

    Filter precedence (most specific to least specific):
    1. name_exact - Exact match (highest priority)
    2. name_regex - Regular expression
    3. name_pattern - Wildcard pattern (*, ?)
    4. name - Substring/contains match
    5. project - Single project (default/fallback)

    Args:
        client: LangSmith Client instance
        project: Single project name (default fallback)
        name: Substring/contains match (convenience filter)
        name_exact: Exact project name match
        name_pattern: Wildcard pattern (e.g., "dev/*", "*production*")
        name_regex: Regular expression pattern

    Returns:
        List of matching project names

    Examples:
        # Single project (default)
        get_matching_projects(client, project="my-project")
        # -> ["my-project"]

        # Exact match
        get_matching_projects(client, name_exact="production-api")
        # -> ["production-api"] or []

        # Substring contains
        get_matching_projects(client, name="prod")
        # -> ["production-api", "production-web", "dev-prod-test"]

        # Wildcard pattern
        get_matching_projects(client, name_pattern="dev/*")
        # -> ["dev/api", "dev/web", "dev/worker"]

        # Regex pattern
        get_matching_projects(client, name_regex="^prod-.*-v[0-9]+$")
        # -> ["prod-api-v1", "prod-web-v2"]
    """
    # If a specific project is given and no other filters, return just that project
    # (don't need to call API)
    if project and not name and not name_exact and not name_pattern and not name_regex:
        return [project]

    # Otherwise, list all projects and use universal filter
    all_projects = list(client.list_projects())

    matching = get_matching_items(
        all_projects,
        default_item=project,
        name=name,
        name_exact=name_exact,
        name_pattern=name_pattern,
        name_regex=name_regex,
        name_getter=lambda p: p.name,
    )

    # If we found matching projects, return their names
    if matching:
        return [p.name for p in matching]

    # If no matches and we have a default project, return it
    # (it might be a valid project that just isn't in the list yet)
    if project:
        return [project]

    return []


def _looks_like_uuid(value: str) -> bool:
    """Check if a string looks like a UUID (8-4-4-4-12 hex format).

    Used to auto-detect when a user passes a UUID to --project instead of --project-id.

    Args:
        value: String to check

    Returns:
        True if the string matches UUID format
    """
    return bool(
        re.match(
            r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$",
            value,
        )
    )


def resolve_by_name_or_id(
    name_or_id: str,
    *,
    read_by_name: Callable[[str], T],
    read_by_id: Callable[[str], T],
    entity_name: str,
) -> T:
    """Generic resolver for entities that can be looked up by name or UUID.

    Tries name first (unless input looks like a UUID), then falls back.
    Raises click.ClickException if neither resolves.

    Args:
        name_or_id: User-provided name or UUID string
        read_by_name: Callable that reads entity by name
        read_by_id: Callable that reads entity by ID
        entity_name: Human-readable entity name for error messages (e.g., "Project")
    """
    if _looks_like_uuid(name_or_id):
        try:
            return read_by_id(name_or_id)
        except (LangSmithNotFoundError, LangSmithError, ValueError):
            raise click.ClickException(f"{entity_name} '{name_or_id}' not found.")

    try:
        return read_by_name(name_or_id)
    except LangSmithNotFoundError:
        try:
            return read_by_id(name_or_id)
        except (LangSmithNotFoundError, LangSmithError, ValueError):
            raise click.ClickException(f"{entity_name} '{name_or_id}' not found.")


def get_project_suggestions(
    client: langsmith.Client,
    failed_name: str,
    max_suggestions: int = 5,
) -> list[str]:
    """Find similar project names to suggest when a project is not found.

    Uses two strategies:
    1. Substring matching: query appears in name or name appears in query
    2. Token matching: split by '/', '-', '_', '.' and find shared tokens

    Only called on the failure path (not the happy path), so the extra
    API call to list projects is acceptable.

    Args:
        client: LangSmith Client instance
        failed_name: The project name that was not found
        max_suggestions: Maximum number of suggestions to return

    Returns:
        List of similar project names, sorted by relevance (may be empty)
    """
    try:
        all_projects = list(client.list_projects())
    except Exception:
        return []

    query_lower = failed_name.lower()
    query_tokens = set(re.split(r"[/\-_.]", query_lower))
    query_tokens.discard("")

    scored: list[tuple[float, str]] = []
    for proj in all_projects:
        name = proj.name
        if name is None:
            continue
        name_lower = name.lower()

        # Exact match — shouldn't happen but skip
        if name_lower == query_lower:
            continue

        # Substring match (highest priority)
        if query_lower in name_lower or name_lower in query_lower:
            scored.append((1.0, name))
            continue

        # Token overlap (Jaccard-style score)
        name_tokens = set(re.split(r"[/\-_.]", name_lower))
        name_tokens.discard("")
        if not query_tokens or not name_tokens:
            continue

        shared = query_tokens & name_tokens
        if shared:
            score = len(shared) / len(query_tokens | name_tokens)
            scored.append((score, name))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [name for _, name in scored[:max_suggestions]]


def raise_if_all_failed_with_suggestions(
    result: FetchResult[Any],
    client: langsmith.Client,
    project_query: ProjectQuery,
    logger: Any | None = None,
    entity_name: str = "runs",
) -> None:
    """Raise if all sources failed, with project name suggestions.

    Wraps FetchResult.raise_if_all_failed() with automatic suggestion
    fetching when a single project name lookup fails.

    Args:
        result: The FetchResult to check
        client: LangSmith client (for fetching project list on failure)
        project_query: The ProjectQuery used for the fetch
        logger: Optional CLILogger
        entity_name: What we were trying to fetch
    """
    if not result.all_failed:
        return

    # Only fetch suggestions for single-project-name failures
    suggestions: list[str] | None = None
    if project_query.names and not project_query.use_id:
        failed_names = [
            name for name, _ in result.failed_sources if not name.startswith("id:")
        ]
        if len(failed_names) == 1:
            suggestions = get_project_suggestions(client, failed_names[0])

    result.raise_if_all_failed(logger, entity_name, suggestions=suggestions)


def resolve_project_filters(
    client: langsmith.Client,
    *,
    project: str | None = None,
    project_id: str | None = None,
    name: str | None = None,
    name_exact: str | None = None,
    name_pattern: str | None = None,
    name_regex: str | None = None,
) -> ProjectQuery:
    """Resolve project filter options into a ProjectQuery.

    When --project-id is provided, returns a ProjectQuery with project_id set,
    bypassing all name-based resolution. When --project contains a UUID, it is
    auto-detected and treated as --project-id. Otherwise delegates to
    get_matching_projects.

    Args:
        client: LangSmith Client instance
        project: Single project name (default fallback). UUIDs are auto-detected.
        project_id: Direct project UUID (highest priority)
        name: Substring/contains match
        name_exact: Exact project name match
        name_pattern: Wildcard pattern
        name_regex: Regular expression pattern

    Returns:
        ProjectQuery with either project_id or names populated
    """
    if project_id:
        return ProjectQuery(names=[], project_id=project_id)

    # Auto-detect: if --project looks like a UUID, treat as --project-id
    if project and _looks_like_uuid(project):
        return ProjectQuery(names=[], project_id=project)

    names = get_matching_projects(
        client,
        project=project,
        name=name,
        name_exact=name_exact,
        name_pattern=name_pattern,
        name_regex=name_regex,
    )
    return ProjectQuery(names=names)


def add_project_filter_options(func: Callable[..., Any]) -> Callable[..., Any]:
    """Decorator to add universal project filtering options to a command.

    Adds the following Click options in consistent order:
    - --project: Single project name (default/fallback)
    - --project-id: Direct project UUID (bypasses name resolution)
    - --project-name: Substring/contains match
    - --project-name-exact: Exact match
    - --project-name-pattern: Wildcard pattern (*, ?)
    - --project-name-regex: Regular expression

    Usage:
        @runs.command("list")
        @add_project_filter_options
        @click.pass_context
        def list_runs(ctx, project, project_id, project_name, project_name_exact, project_name_pattern, project_name_regex, ...):
            client = get_or_create_client(ctx)
            projects, pid = resolve_project_filters(
                client,
                project=project,
                project_id=project_id,
                name=project_name,
                name_exact=project_name_exact,
                name_pattern=project_name_pattern,
                name_regex=project_name_regex,
            )
            # Use projects list or pid...
    """
    func = click.option(
        "--project-name-regex",
        help="Regular expression pattern for project names (e.g., '^prod-.*-v[0-9]+$').",
    )(func)
    func = click.option(
        "--project-name-pattern",
        help="Wildcard pattern for project names (e.g., 'dev/*', '*production*').",
    )(func)
    func = click.option(
        "--project-name-exact",
        help="Exact project name match.",
    )(func)
    func = click.option(
        "--project-name",
        help="Substring/contains match for project names (convenience filter).",
    )(func)
    func = click.option(
        "--project-id",
        default=None,
        help="Project UUID (bypasses name resolution, fastest lookup).",
    )(func)
    func = click.option(
        "--project",
        default="default",
        help="Project name (default fallback if no other filters specified).",
    )(func)
    return func
