import click
from rich.console import Console
from rich.table import Table
import langsmith
import json

console = Console()


@click.group()
def prompts():
    """Manage LangSmith prompts."""
    pass


@prompts.command("list")
@click.pass_context
def list_prompts(ctx):
    """List available prompt repositories."""
    client = langsmith.Client()
    # The SDK usually allows listing prominent prompts or repos
    # Note: Hub listing might be different, but MCP has list_prompts
    prompts = client.list_prompts()

    if ctx.obj.get("json"):
        data = [p.dict() if hasattr(p, "dict") else dict(p) for p in prompts]
        click.echo(json.dumps(data, default=str))
        return

    table = Table(title="Prompts")
    table.add_column("Repo", style="cyan")
    table.add_column("Description")

    for p in prompts:
        table.add_row(
            getattr(p, "repo_full_name", "Unknown"), getattr(p, "description", "")
        )
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
@click.pass_context
def push_prompt(ctx, name, file_path):
    """Push a local prompt file to LangSmith."""
    client = langsmith.Client()

    with open(file_path, "r") as f:
        content = f.read()

    # Simple push of a string as a prompt version.
    # LangSmith hub often expects LangChain objects, but can handle strings.
    # We'll try to push it.
    client.push_prompt(name, object=content)

    console.print(f"[green]Successfully pushed prompt to {name}[/green]")
