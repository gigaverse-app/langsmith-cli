import click
from rich.console import Console
from rich.table import Table
from langsmith_cli.utils import (
    apply_exclude_filter,
    count_option,
    exclude_option,
    fields_option,
    filter_fields,
    get_or_create_client,
    output_option,
    parse_comma_separated_list,
    render_output,
    write_output_to_file,
    json_dumps,
)

console = Console()


@click.group()
def prompts():
    """Manage LangSmith prompts."""
    pass


@prompts.command("list")
@click.option("--limit", default=20, help="Limit number of prompts (default 20).")
@click.option(
    "--is-public", type=bool, default=None, help="Filter by public/private status."
)
@exclude_option()
@fields_option()
@count_option()
@output_option()
@click.pass_context
def list_prompts(ctx, limit, is_public, exclude, fields, count, output):
    """List available prompt repositories."""
    logger = ctx.obj["logger"]
    is_machine_readable = ctx.obj.get("json") or bool(output) or bool(fields)
    logger.use_stderr = is_machine_readable

    logger.debug(f"Listing prompts: limit={limit}, is_public={is_public}")

    client = get_or_create_client(ctx)
    # list_prompts returns ListPromptsResponse with .repos attribute
    result = client.list_prompts(limit=limit, is_public=is_public)
    prompts_list = result.repos

    # Client-side exclude filtering
    prompts_list = apply_exclude_filter(prompts_list, exclude, lambda p: p.full_name)

    # Handle file output - short circuit if writing to file
    if output:
        data = filter_fields(prompts_list, fields)
        write_output_to_file(data, output, console, format_type="jsonl")
        return

    # Define table builder function
    def build_prompts_table(prompts):
        table = Table(title="Prompts")
        table.add_column("Repo", style="cyan")
        table.add_column("Description")
        table.add_column("Owner", style="dim")
        for p in prompts:
            table.add_row(p.full_name, p.description or "", p.owner)
        return table

    # Determine which fields to include
    if fields:
        include_fields = {f.strip() for f in fields.split(",") if f.strip()}
    else:
        # Default fields for output
        include_fields = None

    # Unified output rendering
    render_output(
        prompts_list,
        build_prompts_table,
        ctx,
        include_fields=include_fields,
        empty_message="No prompts found",
        count_flag=count,
    )


@prompts.command("get")
@click.argument("name")
@click.option("--commit", help="Commit hash or tag.")
@click.pass_context
def get_prompt(ctx, name, commit):
    """Fetch a prompt template."""
    logger = ctx.obj["logger"]
    is_machine_readable = ctx.obj.get("json")
    logger.use_stderr = is_machine_readable

    logger.debug(f"Fetching prompt: name={name}, commit={commit}")

    client = get_or_create_client(ctx)
    # pull_prompt returns the prompt object (might be LangChain PromptTemplate)
    prompt_obj = client.pull_prompt(name + (f":{commit}" if commit else ""))

    # We want a context-efficient representation, usually the template string
    # Try to convert to dict or extract template
    if hasattr(prompt_obj, "to_json"):
        data = prompt_obj.to_json()
    else:
        # Fallback to string representation if it's not JSON serializable trivially
        data = {"prompt": str(prompt_obj)}

    if ctx.obj.get("json"):
        click.echo(json_dumps(data))
        return

    console.print(f"[bold]Prompt:[/bold] {name}")
    console.print("-" * 20)
    console.print(str(prompt_obj))


@prompts.command("push")
@click.argument("name")
@click.argument("file_path", type=click.Path(exists=True))
@click.option("--description", help="Prompt description.")
@click.option("--tags", help="Comma-separated tags.")
@click.option("--is-public", type=bool, default=False, help="Make prompt public.")
@click.pass_context
def push_prompt(ctx, name, file_path, description, tags, is_public):
    """Push a local prompt file to LangSmith."""
    logger = ctx.obj["logger"]
    is_machine_readable = ctx.obj.get("json")
    logger.use_stderr = is_machine_readable

    logger.debug(f"Pushing prompt: name={name}, file={file_path}")

    client = get_or_create_client(ctx)

    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Parse tags if provided
    tags_list = parse_comma_separated_list(tags)

    # Push prompt with metadata
    client.push_prompt(
        prompt_identifier=name,
        object=content,
        description=description,
        tags=tags_list,
        is_public=is_public,
    )

    logger.success(f"Successfully pushed prompt to {name}")
