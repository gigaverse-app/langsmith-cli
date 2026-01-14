import click
from rich.console import Console
from rich.table import Table
import langsmith
import json
from langsmith_cli.utils import (
    print_empty_result_message,
    parse_comma_separated_list,
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
@click.pass_context
def list_prompts(ctx, limit, is_public):
    """List available prompt repositories."""
    client = langsmith.Client()
    # list_prompts returns ListPromptsResponse with .repos attribute
    result = client.list_prompts(limit=limit, is_public=is_public)
    prompts_list = result.repos

    if ctx.obj.get("json"):
        # Use SDK's Pydantic models with focused field selection for context efficiency
        data = [
            p.model_dump(
                include={
                    "repo_handle",
                    "description",
                    "id",
                    "is_public",
                    "tags",
                    "owner",
                    "full_name",
                    "num_likes",
                    "num_downloads",
                    "num_views",
                    "created_at",
                    "updated_at",
                },
                mode="json",
            )
            for p in prompts_list
        ]
        click.echo(json.dumps(data, default=str))
        return

    table = Table(title="Prompts")
    table.add_column("Repo", style="cyan")
    table.add_column("Description")
    table.add_column("Owner", style="dim")

    for p in prompts_list:
        table.add_row(p.full_name, p.description or "", p.owner)

    if not prompts_list:
        print_empty_result_message(console, "prompts")
    else:
        console.print(table)


@prompts.command("get")
@click.argument("name")
@click.option("--commit", help="Commit hash or tag.")
@click.pass_context
def get_prompt(ctx, name, commit):
    """Fetch a prompt template."""
    client = langsmith.Client()
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
    client = langsmith.Client()

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
