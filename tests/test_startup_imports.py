"""Startup must stay lazy: heavy SDKs load only inside the command that needs them.

Every invocation, including ``--help`` and ``--version``, pays for whatever
``langsmith_cli.main`` imports at module level. The LangSmith client alone costs
most of a second, so a single top-level ``from langsmith import Client`` in any
command module silently makes every command slow.
"""

import subprocess
import sys

import pytest

# Modules that each cost tens to hundreds of milliseconds and are only needed
# once a command actually talks to LangSmith, S3, or Parquet. The archive
# command package builds many Pydantic models, so the root group loads it only
# when `archive` is invoked.
HEAVY_MODULES = [
    "langsmith.client",
    "langsmith_cli.commands.archive",
    "httpx",
    "requests",
    "duckdb",
    "pyarrow",
    "boto3",
]

# Reports on stderr behind a marker, so command output on stdout can't mix in.
REPORT_LOADED = f"""
for module in {HEAVY_MODULES!r}:
    if module in sys.modules:
        print("LOADED:" + module, file=sys.stderr)
"""


def _modules_loaded_after(code: str) -> set[str]:
    result = subprocess.run(
        [sys.executable, "-c", f"import sys\n{code}\n{REPORT_LOADED}"],
        capture_output=True,
        text=True,
        check=True,
    )
    return {
        line.removeprefix("LOADED:")
        for line in result.stderr.splitlines()
        if line.startswith("LOADED:")
    }


RUN_HELP = """
from langsmith_cli.main import cli_main
try:
    cli_main.main(["--help"], standalone_mode=False)
except SystemExit:
    pass
"""


@pytest.mark.parametrize(
    "code",
    ["import langsmith_cli.main", RUN_HELP],
    ids=["import", "help"],
)
def test_startup_does_not_import_heavy_modules(code: str):
    assert _modules_loaded_after(code) == set()


def test_probe_detects_heavy_imports():
    """Control: the probe must see a heavy module when one is imported."""
    assert "langsmith.client" in _modules_loaded_after("from langsmith import Client")


def test_lazy_subcommand_help_matches_real_command():
    """`--help` shows the declared short help, so it must not drift from the command."""
    from langsmith_cli.main import LAZY_SUBCOMMANDS

    for name, lazy in LAZY_SUBCOMMANDS.items():
        assert lazy.load().get_short_help_str(limit=1000) == lazy.short_help, name


def test_lazy_subcommand_is_listed_and_runs():
    """Control: deferring the import must not hide or break the command."""
    from click.testing import CliRunner

    from langsmith_cli.main import LAZY_SUBCOMMANDS, cli_main

    runner = CliRunner()
    root_help = runner.invoke(cli_main, ["--help"])
    assert root_help.exit_code == 0
    archive_rows = [
        line.split() for line in root_help.output.splitlines() if "archive" in line
    ]
    assert archive_rows == [
        ["archive", *LAZY_SUBCOMMANDS["archive"].short_help.split()]
    ]

    archive_help = runner.invoke(cli_main, ["archive", "--help"])
    assert archive_help.exit_code == 0
    assert "backfill" in archive_help.output
