import click
import os
import webbrowser
from rich.console import Console

console = Console()


@click.command()
def login():
    """
    Configure LangSmith API Key.
    """
    url = "https://smith.langchain.com/settings"
    click.echo(f"Opening LangSmith settings to retrieve your API Key: {url}")
    webbrowser.open(url)
    api_key = click.prompt("Enter your LangSmith API Key", hide_input=True)

    env_file = ".env"

    if os.path.exists(env_file):
        if not click.confirm(f"{env_file} already exists. Overwrite?", default=False):
            console.print("[yellow]Aborted.[/yellow]")
            return

    with open(env_file, "w") as f:
        f.write(f"LANGSMITH_API_KEY={api_key}\n")

    console.print(f"[green]Successfully logged in![/green] API key saved to {env_file}")
