"""Logging infrastructure for CLI output with verbosity levels.

This module provides a unified logging system that:
1. Separates stdout (data) from stderr (diagnostics) in machine-readable modes
2. Provides industry-standard verbosity levels (TRACE, DEBUG, INFO, WARNING, ERROR)
3. Uses Rich Console for formatted terminal output
"""

from enum import IntEnum
from typing import Any

from rich.console import Console


class Verbosity(IntEnum):
    """Verbosity levels matching Python logging standard.

    Lower numeric values mean more verbose output.
    """

    TRACE = 5  # Ultra-verbose (all HTTP, raw responses, timing)
    DEBUG = 10  # Debug details (API calls, data processing)
    INFO = 20  # Progress messages (fetching, found X matches) - DEFAULT
    WARNING = 30  # Warnings + errors (quieter, no progress)
    ERROR = 40  # Only errors (quietest, for clean scripts)


class CLILogger:
    """Centralized logger for CLI output with verbosity control.

    Handles stream separation (stdout vs stderr) and verbosity filtering
    based on the current output mode and verbosity level.
    """

    def __init__(self, verbosity: Verbosity, use_stderr: bool):
        """Initialize the logger.

        Args:
            verbosity: The verbosity level (TRACE, DEBUG, INFO, WARNING, ERROR)
            use_stderr: If True, diagnostics go to stderr (machine-readable modes)
        """
        self.verbosity = verbosity
        self.stdout_console = Console()  # Always available for data output
        self.stderr_console = Console(stderr=True)  # For diagnostics
        self.use_stderr = use_stderr

    @property
    def diagnostic_console(self) -> Console:
        """Returns the appropriate console for diagnostic messages.

        Returns stderr_console if in machine-readable mode, else stdout_console.
        """
        return self.stderr_console if self.use_stderr else self.stdout_console

    def trace(self, message: str) -> None:
        """Ultra-verbose logging (HTTP requests, raw responses, timing).

        Only shown with -vv (TRACE level).
        """
        if self.verbosity <= Verbosity.TRACE:
            self.diagnostic_console.print(
                f"[dim magenta][TRACE][/dim magenta] {message}"
            )

    def debug(self, message: str) -> None:
        """Debug logging (API calls, data processing details).

        Shown with -v or -vv (DEBUG and TRACE levels).
        """
        if self.verbosity <= Verbosity.DEBUG:
            self.diagnostic_console.print(f"[dim cyan][DEBUG][/dim cyan] {message}")

    def info(self, message: str) -> None:
        """Info logging (progress messages, match counts).

        Shown by default (INFO level) unless -q or -qq is used.
        """
        if self.verbosity <= Verbosity.INFO:
            self.diagnostic_console.print(f"[dim]{message}[/dim]")

    def warning(self, message: str) -> None:
        """Warning messages.

        Shown by default and with -q (WARNING level), hidden with -qq.
        """
        if self.verbosity <= Verbosity.WARNING:
            self.diagnostic_console.print(f"[yellow]{message}[/yellow]")

    def error(self, message: str) -> None:
        """Error messages.

        Always shown unless explicitly suppressed.
        """
        if self.verbosity <= Verbosity.ERROR:
            self.diagnostic_console.print(f"[red]{message}[/red]")

    def success(self, message: str) -> None:
        """Success messages (shown at INFO level and above).

        Shown by default (INFO level) unless -q or -qq is used.
        """
        if self.verbosity <= Verbosity.INFO:
            self.diagnostic_console.print(f"[green]{message}[/green]")

    def data(self, content: Any, mode: str = "table") -> None:
        """Output data to stdout (never filtered by verbosity).

        Args:
            content: The content to output (typically a Rich table)
            mode: The output mode ("table", "json", etc.)
        """
        if mode == "table":
            self.stdout_console.print(content)
        # For JSON/CSV/YAML, data is handled by click.echo() directly
