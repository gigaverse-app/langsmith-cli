"""Tests for utility functions in langsmith_cli.utils."""

import pytest
import json
from unittest.mock import MagicMock
from dataclasses import dataclass
import click

from langsmith_cli.utils import (
    output_formatted_data,
    sort_items,
    apply_regex_filter,
    apply_wildcard_filter,
    determine_output_format,
    print_empty_result_message,
    parse_json_string,
    parse_comma_separated_list,
)


@dataclass
class MockItem:
    """Simple mock item for testing."""

    name: str | None = None
    value: int = 0


class TestOutputFormattedData:
    """Tests for output_formatted_data function."""

    def test_output_json_format(self, capsys):
        """Test JSON output format."""
        data = [{"name": "test1", "id": "123"}, {"name": "test2", "id": "456"}]
        output_formatted_data(data, "json")

        captured = capsys.readouterr()
        result = json.loads(captured.out)
        assert len(result) == 2
        assert result[0]["name"] == "test1"
        assert result[1]["id"] == "456"

    def test_output_csv_format(self, capsys):
        """Test CSV output format."""
        data = [{"name": "test1", "id": "123"}, {"name": "test2", "id": "456"}]
        output_formatted_data(data, "csv")

        captured = capsys.readouterr()
        # Handle both \n and \r\n line endings
        lines = [line.strip() for line in captured.out.strip().split("\n")]
        assert lines[0] == "name,id"
        assert lines[1] == "test1,123"
        assert lines[2] == "test2,456"

    def test_output_yaml_format(self, capsys):
        """Test YAML output format."""
        data = [{"name": "test1", "id": "123"}]
        output_formatted_data(data, "yaml")

        captured = capsys.readouterr()
        assert "name: test1" in captured.out
        assert "id:" in captured.out

    def test_output_empty_data_json(self, capsys):
        """Test JSON output with empty data."""
        output_formatted_data([], "json")

        captured = capsys.readouterr()
        result = json.loads(captured.out)
        assert result == []

    def test_output_empty_data_yaml(self, capsys):
        """Test YAML output with empty data."""
        output_formatted_data([], "yaml")

        captured = capsys.readouterr()
        assert captured.out.strip() == "[]"

    def test_output_empty_data_csv(self, capsys):
        """Test CSV output with empty data."""
        output_formatted_data([], "csv")

        captured = capsys.readouterr()
        # CSV with no data should output nothing
        assert captured.out == ""

    def test_output_with_field_filtering(self, capsys):
        """Test field filtering in output."""
        data = [{"name": "test", "id": "123", "extra": "field"}]
        output_formatted_data(data, "json", fields=["name", "id"])

        captured = capsys.readouterr()
        result = json.loads(captured.out)
        assert "name" in result[0]
        assert "id" in result[0]
        assert "extra" not in result[0]

    def test_output_unsupported_format(self):
        """Test that unsupported format raises error."""
        with pytest.raises(ValueError, match="Unsupported format"):
            output_formatted_data([{"name": "test"}], "xml")

    def test_output_json_with_datetime(self, capsys):
        """Test JSON output handles datetime objects with default=str."""
        from datetime import datetime

        data = [{"name": "test", "timestamp": datetime(2024, 1, 14)}]
        output_formatted_data(data, "json")

        captured = capsys.readouterr()
        result = json.loads(captured.out)
        assert "2024" in result[0]["timestamp"]


class TestSortItems:
    """Tests for sort_items function."""

    def test_sort_ascending(self):
        """Test ascending sort by name."""
        items = [
            MockItem(name="zebra"),
            MockItem(name="alpha"),
            MockItem(name="beta"),
        ]
        sort_key_map = {"name": lambda x: x.name}
        console = MagicMock()

        result = sort_items(items, "name", sort_key_map, console)

        assert result[0].name == "alpha"
        assert result[1].name == "beta"
        assert result[2].name == "zebra"

    def test_sort_descending(self):
        """Test descending sort by value."""
        items = [
            MockItem(name="item1", value=10),
            MockItem(name="item2", value=50),
            MockItem(name="item3", value=30),
        ]
        sort_key_map = {"value": lambda x: x.value}
        console = MagicMock()

        result = sort_items(items, "-value", sort_key_map, console)

        assert result[0].value == 50
        assert result[1].value == 30
        assert result[2].value == 10

    def test_sort_unknown_field_warning(self):
        """Test warning for unknown sort field."""
        items = [MockItem(name="test")]
        sort_key_map = {"name": lambda x: x.name}
        console = MagicMock()

        result = sort_items(items, "unknown_field", sort_key_map, console)

        # Should return original list unchanged
        assert result == items
        # Should print warning
        console.print.assert_called_once()
        assert "Unknown sort field" in str(console.print.call_args)

    def test_sort_empty_list(self):
        """Test sorting empty list."""
        sort_key_map = {"name": lambda x: x.name}
        console = MagicMock()

        result = sort_items([], "name", sort_key_map, console)

        assert result == []

    def test_sort_error_handling(self):
        """Test error handling during sort."""
        items = [
            MockItem(name="test1"),
            MockItem(name=None),  # Will cause error in comparison
        ]
        # Key function that will raise error
        sort_key_map = {"name": lambda x: x.name.lower()}
        console = MagicMock()

        result = sort_items(items, "name", sort_key_map, console)

        # Should return original list on error
        assert result == items
        # Should print warning
        console.print.assert_called_once()
        assert "Could not sort" in str(console.print.call_args)

    def test_sort_no_sort_by(self):
        """Test that None or empty sort_by returns original list."""
        items = [MockItem(name="test")]
        sort_key_map = {}
        console = MagicMock()

        result = sort_items(items, None, sort_key_map, console)
        assert result == items

        result = sort_items(items, "", sort_key_map, console)
        assert result == items


class TestApplyRegexFilter:
    """Tests for apply_regex_filter function."""

    def test_regex_filter_matches(self):
        """Test regex filtering with matches."""
        items = [
            MockItem(name="test-auth-v1"),
            MockItem(name="test-auth-v2"),
            MockItem(name="prod-checkout"),
        ]

        def field_getter(x):
            return x.name

        result = apply_regex_filter(items, r"test-auth-v\d+", field_getter)

        assert len(result) == 2
        assert result[0].name == "test-auth-v1"
        assert result[1].name == "test-auth-v2"

    def test_regex_filter_no_matches(self):
        """Test regex filtering with no matches."""
        items = [MockItem(name="test1"), MockItem(name="test2")]

        def field_getter(x):
            return x.name

        result = apply_regex_filter(items, "nomatch", field_getter)

        assert result == []

    def test_regex_filter_none_pattern(self):
        """Test that None pattern returns all items."""
        items = [MockItem(name="test1"), MockItem(name="test2")]

        def field_getter(x):
            return x.name

        result = apply_regex_filter(items, None, field_getter)

        assert result == items

    def test_regex_filter_empty_pattern(self):
        """Test that empty string pattern returns all items."""
        items = [MockItem(name="test1")]

        def field_getter(x):
            return x.name

        result = apply_regex_filter(items, "", field_getter)

        assert result == items

    def test_regex_filter_invalid_pattern(self):
        """Test that invalid regex raises BadParameter."""
        items = [MockItem(name="test")]

        def field_getter(x):
            return x.name

        with pytest.raises(click.BadParameter, match="Invalid regex pattern"):
            apply_regex_filter(items, "[invalid(", field_getter)

    def test_regex_filter_with_anchors(self):
        """Test regex with anchors."""
        items = [
            MockItem(name="auth-service"),
            MockItem(name="test-auth"),
        ]

        def field_getter(x):
            return x.name

        result = apply_regex_filter(items, "^auth", field_getter)

        assert len(result) == 1
        assert result[0].name == "auth-service"

    def test_regex_filter_none_field_value(self):
        """Test filtering items with None field values."""
        items = [
            MockItem(name="test"),
            MockItem(name=None),
        ]

        def field_getter(x):
            return x.name

        result = apply_regex_filter(items, "test", field_getter)

        # Should only match items with non-None names
        assert len(result) == 1
        assert result[0].name == "test"


class TestApplyWildcardFilter:
    """Tests for apply_wildcard_filter function."""

    def test_wildcard_star_filter(self):
        """Test wildcard filtering with * pattern."""
        items = [
            MockItem(name="prod-api-v1"),
            MockItem(name="prod-web-v1"),
            MockItem(name="staging-api"),
        ]

        def field_getter(x):
            return x.name

        result = apply_wildcard_filter(items, "*prod*", field_getter)

        assert len(result) == 2
        assert result[0].name == "prod-api-v1"
        assert result[1].name == "prod-web-v1"

    def test_wildcard_question_filter(self):
        """Test wildcard filtering with ? pattern."""
        items = [
            MockItem(name="test1"),
            MockItem(name="test2"),
            MockItem(name="test10"),
        ]

        def field_getter(x):
            return x.name

        result = apply_wildcard_filter(items, "test?", field_getter)

        assert len(result) == 2
        assert result[0].name == "test1"
        assert result[1].name == "test2"

    def test_wildcard_none_pattern(self):
        """Test that None pattern returns all items."""
        items = [MockItem(name="test")]

        def field_getter(x):
            return x.name

        result = apply_wildcard_filter(items, None, field_getter)

        assert result == items

    def test_wildcard_empty_pattern(self):
        """Test that empty pattern returns all items."""
        items = [MockItem(name="test")]

        def field_getter(x):
            return x.name

        result = apply_wildcard_filter(items, "", field_getter)

        assert result == items

    def test_wildcard_no_matches(self):
        """Test wildcard with no matches."""
        items = [MockItem(name="test1"), MockItem(name="test2")]

        def field_getter(x):
            return x.name

        result = apply_wildcard_filter(items, "*nomatch*", field_getter)

        assert result == []

    def test_wildcard_none_field_value(self):
        """Test filtering items with None field values."""
        items = [
            MockItem(name="test"),
            MockItem(name=None),
        ]

        def field_getter(x):
            return x.name

        result = apply_wildcard_filter(items, "*test*", field_getter)

        # Should only match items with non-None names
        assert len(result) == 1
        assert result[0].name == "test"


class TestDetermineOutputFormat:
    """Tests for determine_output_format function."""

    def test_explicit_format_takes_precedence(self):
        """Test that explicit format flag takes precedence."""
        assert determine_output_format("csv", True) == "csv"
        assert determine_output_format("yaml", True) == "yaml"
        assert determine_output_format("table", False) == "table"

    def test_json_flag_when_no_explicit_format(self):
        """Test json flag determines format when no explicit format."""
        assert determine_output_format(None, True) == "json"

    def test_default_to_table(self):
        """Test default is table when no flags."""
        assert determine_output_format(None, False) == "table"

    def test_explicit_json_format(self):
        """Test explicit json format."""
        assert determine_output_format("json", False) == "json"


class TestPrintEmptyResultMessage:
    """Tests for print_empty_result_message function."""

    def test_print_empty_result_message(self, capsys):
        """Test printing empty result message."""
        console = MagicMock()
        print_empty_result_message(console, "runs")

        console.print.assert_called_once()
        call_args = str(console.print.call_args)
        assert "No runs found" in call_args
        assert "yellow" in call_args

    def test_print_empty_result_message_different_types(self, capsys):
        """Test printing empty result messages for different item types."""
        console = MagicMock()

        print_empty_result_message(console, "projects")
        assert "No projects found" in str(console.print.call_args)

        console.reset_mock()
        print_empty_result_message(console, "datasets")
        assert "No datasets found" in str(console.print.call_args)


class TestParseJsonString:
    """Tests for parse_json_string function."""

    def test_parse_valid_json(self):
        """Test parsing valid JSON string."""
        result = parse_json_string('{"key": "value", "num": 42}')
        assert result == {"key": "value", "num": 42}

    def test_parse_none_input(self):
        """Test parsing None returns None."""
        result = parse_json_string(None)
        assert result is None

    def test_parse_empty_string(self):
        """Test parsing empty string returns None."""
        result = parse_json_string("")
        assert result is None

    def test_parse_invalid_json(self):
        """Test parsing invalid JSON raises BadParameter."""
        with pytest.raises(click.BadParameter, match="Invalid JSON"):
            parse_json_string('{"invalid": }', "metadata")

    def test_parse_json_with_nested_objects(self):
        """Test parsing JSON with nested objects."""
        json_str = '{"outer": {"inner": "value"}, "list": [1, 2, 3]}'
        result = parse_json_string(json_str)
        assert result is not None
        assert result["outer"]["inner"] == "value"
        assert result["list"] == [1, 2, 3]

    def test_parse_json_error_includes_field_name(self):
        """Test that error message includes field name."""
        with pytest.raises(click.BadParameter) as exc_info:
            parse_json_string("invalid", "custom_field")
        assert "custom_field" in str(exc_info.value)


class TestParseCommaSeparatedList:
    """Tests for parse_comma_separated_list function."""

    def test_parse_simple_list(self):
        """Test parsing simple comma-separated list."""
        result = parse_comma_separated_list("item1,item2,item3")
        assert result == ["item1", "item2", "item3"]

    def test_parse_list_with_spaces(self):
        """Test parsing list with spaces around items."""
        result = parse_comma_separated_list("item1 , item2 ,  item3")
        assert result == ["item1", "item2", "item3"]

    def test_parse_single_item(self):
        """Test parsing single item (no commas)."""
        result = parse_comma_separated_list("single")
        assert result == ["single"]

    def test_parse_none_input(self):
        """Test parsing None returns None."""
        result = parse_comma_separated_list(None)
        assert result is None

    def test_parse_empty_string(self):
        """Test parsing empty string returns None."""
        result = parse_comma_separated_list("")
        assert result is None

    def test_parse_list_with_empty_items(self):
        """Test parsing list with empty items."""
        result = parse_comma_separated_list("item1,,item2")
        assert result == ["item1", "", "item2"]

    def test_parse_list_with_special_characters(self):
        """Test parsing list with special characters."""
        result = parse_comma_separated_list("item-1,item_2,item.3")
        assert result == ["item-1", "item_2", "item.3"]
