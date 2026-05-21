import click
from rich.console import Console
from rich.table import Table
from langsmith_cli.utils import (
    apply_exclude_filter,
    configure_logger_streams,
    confirm_option,
    count_option,
    emit_action_result,
    exclude_option,
    fields_option,
    filter_fields,
    get_or_create_client,
    json_dumps,
    output_option,
    output_single_item,
    parse_comma_separated_list,
    parse_fields_option,
    render_output,
    require_confirmation,
    sort_by_option,
    sort_items,
)

console = Console()


def _resolve_visibility_flags(
    ctx: click.Context,
    *,
    is_public: bool | None,
    public: bool,
    private: bool,
) -> bool | None:
    """Resolve prompt visibility flags and reject contradictory inputs."""
    if public and private:
        raise click.UsageError("Use only one of --public or --private.")

    legacy_source = ctx.get_parameter_source("is_public")
    legacy_was_set = legacy_source is click.core.ParameterSource.COMMANDLINE

    if legacy_was_set and public and is_public is False:
        raise click.UsageError("Use only one of --public or --is-public false.")
    if legacy_was_set and private and is_public is True:
        raise click.UsageError("Use only one of --private or --is-public true.")

    if public:
        return True
    if private:
        return False
    return is_public


@click.group()
def prompts():
    """Manage LangSmith prompts."""
    pass


@prompts.command("list")
@click.option("--limit", default=20, help="Limit number of prompts (default 20).")
@click.option(
    "--is-public", type=bool, default=None, help="Filter by public/private status."
)
@click.option(
    "--public",
    "public",
    is_flag=True,
    help="Show only public prompts.",
)
@click.option(
    "--private",
    "private",
    is_flag=True,
    help="Show only private prompts.",
)
@sort_by_option(fields="full_name, created_at, updated_at")
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["table", "json", "csv", "yaml"]),
    help="Output format (default: table, or json if --json flag used).",
)
@exclude_option()
@fields_option()
@count_option()
@output_option()
@click.pass_context
def list_prompts(
    ctx,
    limit,
    is_public,
    public,
    private,
    sort_by,
    output_format,
    exclude,
    fields,
    count,
    output,
):
    """List available prompt repositories."""
    logger = ctx.obj["logger"]
    configure_logger_streams(
        ctx, logger, output=output, output_format=output_format, fields=fields
    )

    is_public = _resolve_visibility_flags(
        ctx, is_public=is_public, public=public, private=private
    )

    logger.debug(f"Listing prompts: limit={limit}, is_public={is_public}")

    client = get_or_create_client(ctx)
    # list_prompts returns ListPromptsResponse with .repos attribute
    result = client.list_prompts(limit=limit, is_public=is_public)
    prompts_list = result.repos

    # Client-side exclude filtering
    prompts_list = apply_exclude_filter(prompts_list, exclude, lambda p: p.full_name)

    # Client-side sorting
    if sort_by:
        prompts_list = sort_items(prompts_list, sort_by)

    # Define table builder function
    def build_prompts_table(prompts):
        table = Table(title="Prompts")
        table.add_column("Repo", style="cyan")
        table.add_column("Description")
        table.add_column("Owner", style="dim")
        for p in prompts:
            table.add_row(p.full_name, p.description or "", p.owner)
        return table

    include_fields = parse_fields_option(fields)

    # Unified output rendering (handles --json, --format, --output, --count uniformly)
    render_output(
        prompts_list,
        build_prompts_table,
        ctx,
        include_fields=include_fields,
        empty_message="No prompts found",
        output_format=output_format,
        count_flag=count,
        output_path=output,
    )


@prompts.command("get")
@click.argument("name")
@click.option("--commit", help="Commit hash or tag.")
@fields_option(
    "Comma-separated field names to include (e.g., 'template,input_variables'). Reduces context usage."
)
@output_option()
@click.pass_context
def get_prompt(ctx, name, commit, fields, output):
    """Fetch a prompt template."""
    from langsmith.utils import LangSmithNotFoundError

    logger = ctx.obj["logger"]
    configure_logger_streams(ctx, logger, output=output, fields=fields)

    logger.debug(f"Fetching prompt: name={name}, commit={commit}")

    client = get_or_create_client(ctx)
    # pull_prompt returns the prompt object (might be LangChain PromptTemplate)
    try:
        prompt_obj = client.pull_prompt(name + (f":{commit}" if commit else ""))
    except LangSmithNotFoundError:
        raise click.ClickException(
            f"Prompt '{name}' not found or has no commits. "
            "Push content first with `langsmith-cli prompts push`."
        )

    # Convert prompt object to dict
    try:
        data: dict = prompt_obj.to_json()  # type: ignore[union-attr]
    except AttributeError:
        # Fallback to string representation if no to_json method
        data = {"prompt": str(prompt_obj)}

    # Apply field filtering if requested
    field_set = parse_fields_option(fields)
    if field_set:
        data = {k: v for k, v in data.items() if k in field_set}

    # Capture prompt_obj for rich rendering closure
    prompt_str = str(prompt_obj)

    def render_prompt_details(data: dict, console: object) -> None:
        from rich.console import Console as RichConsole

        assert isinstance(console, RichConsole)
        console.print(f"[bold]Prompt:[/bold] {name}")
        console.print("-" * 20)
        console.print(prompt_str)

    output_single_item(
        ctx, data, console, output=output, render_fn=render_prompt_details
    )


@prompts.command("push")
@click.argument("name")
@click.argument("file_path", type=click.Path(exists=True))
@click.option("--description", help="Prompt description.")
@click.option("--tags", help="Comma-separated tags.")
@click.option("--is-public", type=bool, default=False, help="Make prompt public.")
@click.option("--public", "public", is_flag=True, help="Make prompt public.")
@click.option("--private", "private", is_flag=True, help="Make prompt private.")
@click.pass_context
def push_prompt(ctx, name, file_path, description, tags, is_public, public, private):
    """Push a local prompt file to LangSmith."""
    logger = ctx.obj["logger"]
    configure_logger_streams(ctx, logger)

    logger.debug(f"Pushing prompt: name={name}, file={file_path}")

    prompt_is_public = _resolve_visibility_flags(
        ctx, is_public=is_public, public=public, private=private
    )

    client = get_or_create_client(ctx)

    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Parse tags if provided
    tags_list = parse_comma_separated_list(tags)

    # Push prompt with metadata
    try:
        client.push_prompt(
            prompt_identifier=name,
            object=content,
            description=description,
            tags=tags_list,
            is_public=prompt_is_public,
        )
    except ImportError:
        raise click.ClickException(
            "Prompt push requires the langchain-core package. "
            "Install with: pip install langchain-core"
        )

    emit_action_result(
        ctx,
        logger,
        payload={"status": "success", "name": name},
        success_message=f"Successfully pushed prompt to {name}",
    )


@prompts.command("pull")
@click.argument("name")
@click.option("--commit", help="Commit hash or tag to pull a specific version.")
@click.option(
    "--include-model",
    is_flag=True,
    default=False,
    help="Include model configuration in output.",
)
@fields_option()
@output_option()
@click.pass_context
def pull_prompt(ctx, name, commit, include_model, fields, output):
    """Pull a prompt's raw commit data (manifest, metadata) from LangSmith."""
    from langsmith.utils import LangSmithNotFoundError

    logger = ctx.obj["logger"]
    configure_logger_streams(ctx, logger, output=output, fields=fields)

    identifier = name + (f":{commit}" if commit else "")
    logger.debug(f"Pulling prompt commit: {identifier}")

    client = get_or_create_client(ctx)
    try:
        prompt_commit = client.pull_prompt_commit(
            identifier, include_model=include_model
        )
    except LangSmithNotFoundError:
        raise click.ClickException(
            f"Prompt '{name}' not found or has no commits. "
            "Push content first with `langsmith-cli prompts push`."
        )

    data = filter_fields(prompt_commit, fields)

    def render_commit_details(data: dict, console: object) -> None:
        from rich.console import Console as RichConsole
        from rich.syntax import Syntax

        assert isinstance(console, RichConsole)
        console.print(f"[bold]Prompt:[/bold] {data.get('owner')}/{data.get('repo')}")
        console.print(f"[bold]Commit:[/bold] {data.get('commit_hash')}")
        if data.get("manifest"):
            console.print("\n[bold]Manifest:[/bold]")
            console.print(Syntax(json_dumps(data["manifest"], indent=2), "json"))

    output_single_item(
        ctx, data, console, output=output, render_fn=render_commit_details
    )


@prompts.command("delete")
@click.argument("name")
@confirm_option()
@click.pass_context
def delete_prompt(ctx, name, confirm):
    """Delete a prompt from LangSmith."""
    from langsmith.utils import LangSmithNotFoundError

    logger = ctx.obj["logger"]
    configure_logger_streams(ctx, logger)

    require_confirmation(confirm, f"Are you sure you want to delete prompt '{name}'?")

    logger.debug(f"Deleting prompt: {name}")

    client = get_or_create_client(ctx)
    try:
        client.delete_prompt(name)
    except LangSmithNotFoundError:
        if ctx.obj.get("json"):
            click.echo(
                json_dumps({"status": "error", "message": f"Prompt '{name}' not found"})
            )
        else:
            logger.warning(f"Prompt '{name}' not found.")
        return

    emit_action_result(
        ctx,
        logger,
        payload={"status": "success", "name": name},
        success_message=f"Deleted prompt '{name}'",
    )


@prompts.command("create")
@click.argument("name")
@click.option("--description", help="Prompt description.")
@click.option("--tags", help="Comma-separated tags.")
@click.option("--is-public", type=bool, default=False, help="Make prompt public.")
@click.option("--public", "public", is_flag=True, help="Make prompt public.")
@click.option("--private", "private", is_flag=True, help="Make prompt private.")
@click.pass_context
def create_prompt_cmd(ctx, name, description, tags, is_public, public, private):
    """Create a new empty prompt repository."""
    from langsmith.utils import LangSmithConflictError

    logger = ctx.obj["logger"]
    configure_logger_streams(ctx, logger)

    logger.debug(f"Creating prompt: {name}")

    tags_list = parse_comma_separated_list(tags)
    prompt_is_public = _resolve_visibility_flags(
        ctx, is_public=is_public, public=public, private=private
    )
    client = get_or_create_client(ctx)

    try:
        prompt = client.create_prompt(
            name,
            description=description,
            tags=tags_list if tags_list else None,
            is_public=prompt_is_public,
        )
        emit_action_result(
            ctx,
            logger,
            model=prompt,
            success_message=f"Created prompt '{prompt.full_name}'",
        )
    except LangSmithConflictError:
        if ctx.obj.get("json"):
            click.echo(
                json_dumps(
                    {"status": "error", "message": f"Prompt '{name}' already exists"}
                )
            )
        else:
            logger.warning(f"Prompt '{name}' already exists.")


@prompts.command("commits")
@click.argument("name")
@click.option("--limit", default=20, help="Limit number of commits (default 20).")
@click.option("--offset", default=0, help="Number of commits to skip.")
@click.option(
    "--include-model",
    is_flag=True,
    default=False,
    help="Include model configuration.",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["table", "json", "csv", "yaml"]),
    help="Output format (default: table, or json if --json flag used).",
)
@fields_option()
@count_option()
@output_option()
@click.pass_context
def list_commits(
    ctx, name, limit, offset, include_model, output_format, fields, count, output
):
    """List version history (commits) for a prompt."""
    logger = ctx.obj["logger"]
    configure_logger_streams(
        ctx, logger, output=output, output_format=output_format, fields=fields
    )

    logger.debug(f"Listing commits for prompt: {name}, limit={limit}")

    client = get_or_create_client(ctx)
    commits_gen = client.list_prompt_commits(
        name, limit=limit, offset=offset, include_model=include_model
    )
    commits_list = list(commits_gen)

    # Define table builder
    def build_commits_table(commits):
        table = Table(title=f"Commits: {name}")
        table.add_column("Hash", style="cyan")
        table.add_column("Created At", style="dim")
        table.add_column("Parent Hash")
        for c in commits:
            created = str(c.created_at) if c.created_at else "-"
            table.add_row(
                c.commit_hash or "-",
                created,
                c.parent_commit_hash or "-",
            )
        return table

    include_fields = parse_fields_option(fields)

    render_output(
        commits_list,
        build_commits_table,
        ctx,
        include_fields=include_fields,
        empty_message=f"No commits found for '{name}'",
        output_format=output_format,
        count_flag=count,
        output_path=output,
    )
