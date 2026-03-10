"""Runs command package.

This package splits the runs commands into focused sub-modules.
The `runs` Click group is defined in `_group.py` and commands are
registered by importing each sub-module below.

All test-facing symbols are re-exported here for backward compatibility
with `from langsmith_cli.commands.runs import X`.
"""

# The Click group (must be imported first)
from langsmith_cli.commands.runs._group import runs  # noqa: F401

# Import sub-modules to register their commands on the `runs` group.
# Order doesn't matter for Click, but keep alphabetical for readability.
import langsmith_cli.commands.runs.analyze_cmd  # noqa: F401
import langsmith_cli.commands.runs.cache_cmd  # noqa: F401
import langsmith_cli.commands.runs.discovery_cmd  # noqa: F401
import langsmith_cli.commands.runs.export_cmd  # noqa: F401
import langsmith_cli.commands.runs.get_cmd  # noqa: F401
import langsmith_cli.commands.runs.list_cmd  # noqa: F401
import langsmith_cli.commands.runs.pricing_cmd  # noqa: F401
import langsmith_cli.commands.runs.search_cmd  # noqa: F401
import langsmith_cli.commands.runs.stats_cmd  # noqa: F401
import langsmith_cli.commands.runs.usage_cmd  # noqa: F401
import langsmith_cli.commands.runs.watch_cmd  # noqa: F401

# Re-export symbols that tests and other sub-modules import from
# `langsmith_cli.commands.runs`.

# From analyze_cmd (used by tests and usage_cmd/search_cmd)
from langsmith_cli.commands.runs.analyze_cmd import (  # noqa: F401
    build_grouping_fql_filter,
    build_multi_dimensional_fql_filter,
    compute_metrics,
    extract_group_value,
    parse_grouping_field,
)

# From list_cmd (used by search_cmd via ctx.invoke)
from langsmith_cli.commands.runs.list_cmd import list_runs  # noqa: F401

# From usage_cmd (used by tests)
from langsmith_cli.commands.runs.usage_cmd import (  # noqa: F401
    _get_model_name,
    _metadata_value_matches,
    _truncate_hour,
)

# From pricing_cmd (used by tests)
from langsmith_cli.commands.runs.pricing_cmd import _fetch_openrouter_pricing  # noqa: F401
