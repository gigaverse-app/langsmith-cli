import click
import webbrowser
from pathlib import Path
from rich.console import Console
from langsmith_cli.config import save_api_key

console = Console()


@click.command()
@click.option(
    "--local",
    is_flag=True,
    help="Save to .env in current directory instead of global config directory",
)
def login(local):
    """
    Configure LangSmith API Key.

    By default, saves to user config directory:
    - Linux: ~/.config/langsmith-cli/credentials
    - macOS: ~/Library/Application Support/langsmith-cli/credentials
    - Windows: %APPDATA%\\Local\\langsmith-cli\\credentials

    Use --local to save to .env in current directory (not recommended).
    """
    url = "https://smith.langchain.com/settings"
    click.echo(f"Opening LangSmith settings to retrieve your API Key: {url}")
    webbrowser.open(url)
    api_key = click.prompt("Enter your LangSmith API Key", hide_input=True)

    if local:
        # Save to ./.env (current directory) - backward compatibility
        env_file = Path(".env")
        if env_file.exists():
            if not click.confirm(
                f"{env_file} already exists. Overwrite?", default=False
            ):
                console.print("[yellow]Aborted.[/yellow]")
                return

        env_file.write_text(f"LANGSMITH_API_KEY={api_key}\n", encoding="utf-8")
        console.print(f"[green]API key saved to {env_file}[/green]")
        console.print(
            "[yellow]Warning: Remember to add .env to .gitignore to avoid committing secrets![/yellow]"
        )
    else:
        # Save to user config directory (default, recommended)
        creds_file = save_api_key(api_key)
        console.print("[green]Successfully logged in![/green]")
        console.print(f"API key saved to [cyan]{creds_file}[/cyan]")
