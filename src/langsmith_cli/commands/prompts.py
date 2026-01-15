import click
from rich.console import Console
from rich.table import Table
from langsmith_cli.utils import (
    parse_comma_separated_list,
    fields_option,
    get_or_create_client,
    render_output,
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
@fields_option()
@click.pass_context
def list_prompts(ctx, limit, is_public, fields):
    """List available prompt repositories."""
    client = get_or_create_client(ctx)
    # list_prompts returns ListPromptsResponse with .repos attribute
    result = client.list_prompts(limit=limit, is_public=is_public)
    prompts_list = result.repos

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
    )


@prompts.command("get")
@click.argument("name")
@click.option("--commit", help="Commit hash or tag.")
@click.pass_context
def get_prompt(ctx, name, commit):
    """Fetch a prompt template."""
    import json

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
        click.echo(json.dumps(data, default=str))
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
    client = get_or_create_client(ctx)

    with open(file_path, "r") as f:
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

    console.print(f"[green]Successfully pushed prompt to {name}[/green]")
