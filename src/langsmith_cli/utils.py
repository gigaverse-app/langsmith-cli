"""Utility functions shared across commands.

This module re-exports all utilities from focused sub-modules
for backward compatibility. New code should import from the
specific module directly:

- langsmith_cli.output: JSON/output formatting, rendering
- langsmith_cli.filtering: Field filtering, grep, sort, exclude
- langsmith_cli.project_resolution: Project/entity resolution, fetch helpers
- langsmith_cli.time_parsing: Time/duration parsing, FQL time filters
- langsmith_cli.run_helpers: Run-specific table building and filter construction
"""

# Re-export everything for backward compatibility.
# All existing `from langsmith_cli.utils import X` imports continue to work.

from langsmith_cli.output import (
    ConsoleProtocol,
    configure_logger_streams,
    determine_output_format,
    json_dumps,
    output_formatted_data,
    output_option,
    output_single_item,
    print_empty_result_message,
    render_output,
    safe_model_dump,
    write_output_to_file,
)

from langsmith_cli.filtering import (
    add_grep_options,
    add_metadata_filter_options,
    add_name_filter_options,
    apply_client_side_limit,
    apply_exclude_filter,
    apply_grep_filter,
    apply_name_filters,
    apply_regex_filter,
    apply_wildcard_filter,
    build_metadata_fql_filters,
    build_tag_fql_filters,
    count_option,
    exclude_option,
    extract_regex_search_term,
    extract_wildcard_search_term,
    fields_option,
    filter_fields,
    filter_runs_by_tags,
    parse_comma_separated_list,
    parse_fields_option,
    parse_json_string,
    should_use_client_side_limit,
    sort_by_option,
    sort_items,
)

from langsmith_cli.project_resolution import (
    CLIFetchError,
    FetchResult,
    ProjectQuery,
    _looks_like_uuid,
    add_project_filter_options,
    fetch_from_projects,
    get_matching_items,
    get_matching_projects,
    get_or_create_client,
    get_project_suggestions,
    raise_if_all_failed_with_suggestions,
    resolve_by_name_or_id,
    resolve_project_filters,
)

from langsmith_cli.time_parsing import (
    add_time_filter_options,
    build_time_fql_filters,
    combine_fql_filters,
    parse_duration_to_seconds,
    parse_relative_time,
    parse_time_duration,
    parse_time_input,
    parse_time_range,
)

from langsmith_cli.run_helpers import (
    build_runs_list_filter,
    build_runs_table,
    extract_model_name,
    format_token_count,
    render_run_details,
)

__all__ = [
    # output
    "ConsoleProtocol",
    "configure_logger_streams",
    "determine_output_format",
    "json_dumps",
    "output_formatted_data",
    "output_option",
    "output_single_item",
    "print_empty_result_message",
    "render_output",
    "safe_model_dump",
    "write_output_to_file",
    # filtering
    "add_grep_options",
    "add_metadata_filter_options",
    "add_name_filter_options",
    "apply_client_side_limit",
    "apply_exclude_filter",
    "apply_grep_filter",
    "apply_name_filters",
    "apply_regex_filter",
    "apply_wildcard_filter",
    "build_metadata_fql_filters",
    "build_tag_fql_filters",
    "count_option",
    "exclude_option",
    "extract_regex_search_term",
    "extract_wildcard_search_term",
    "fields_option",
    "filter_fields",
    "filter_runs_by_tags",
    "parse_comma_separated_list",
    "parse_fields_option",
    "parse_json_string",
    "should_use_client_side_limit",
    "sort_by_option",
    "sort_items",
    # project_resolution
    "CLIFetchError",
    "FetchResult",
    "ProjectQuery",
    "_looks_like_uuid",
    "add_project_filter_options",
    "fetch_from_projects",
    "get_matching_items",
    "get_matching_projects",
    "get_or_create_client",
    "get_project_suggestions",
    "raise_if_all_failed_with_suggestions",
    "resolve_by_name_or_id",
    "resolve_project_filters",
    # time_parsing
    "add_time_filter_options",
    "build_time_fql_filters",
    "combine_fql_filters",
    "parse_duration_to_seconds",
    "parse_relative_time",
    "parse_time_duration",
    "parse_time_input",
    "parse_time_range",
    # run_helpers
    "build_runs_list_filter",
    "build_runs_table",
    "extract_model_name",
    "format_token_count",
    "render_run_details",
]
