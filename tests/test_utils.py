"""Tests for utility functions in langsmith_cli.utils."""

import pytest
import json
from unittest.mock import MagicMock
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID
import click
from langsmith.schemas import Run

from langsmith_cli.utils import (
    output_formatted_data,
    sort_items,
    apply_regex_filter,
    apply_wildcard_filter,
    determine_output_format,
    print_empty_result_message,
    parse_json_string,
    parse_comma_separated_list,
    filter_fields,
    get_or_create_client,
    extract_wildcard_search_term,
    extract_regex_search_term,
    safe_model_dump,
    apply_client_side_limit,
    render_output,
    extract_model_name,
    format_token_count,
    parse_time_input,
    build_time_fql_filters,
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

    @pytest.mark.parametrize(
        "format_type,expected_check",
        [
            ("json", lambda out: json.loads(out) == []),
            ("yaml", lambda out: out.strip() == "[]"),
            ("csv", lambda out: out == ""),
        ],
    )
    def test_output_empty_data(self, capsys, format_type, expected_check):
        """Test output with empty data for different formats."""
        output_formatted_data([], format_type)

        captured = capsys.readouterr()
        assert expected_check(captured.out)

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

    @pytest.mark.parametrize(
        "explicit_format,json_flag,expected",
        [
            ("csv", True, "csv"),
            ("yaml", True, "yaml"),
            ("table", False, "table"),
            (None, True, "json"),
            (None, False, "table"),
            ("json", False, "json"),
        ],
    )
    def test_determine_output_format(self, explicit_format, json_flag, expected):
        """Test output format determination with various inputs."""
        assert determine_output_format(explicit_format, json_flag) == expected


class TestPrintEmptyResultMessage:
    """Tests for print_empty_result_message function."""

    @pytest.mark.parametrize(
        "item_type,expected_message",
        [
            ("runs", "No runs found"),
            ("projects", "No projects found"),
            ("datasets", "No datasets found"),
        ],
    )
    def test_print_empty_result_message(self, capsys, item_type, expected_message):
        """Test printing empty result messages for different item types."""
        console = MagicMock()
        print_empty_result_message(console, item_type)

        console.print.assert_called_once()
        call_args = str(console.print.call_args)
        assert expected_message in call_args
        assert "yellow" in call_args


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

    @pytest.mark.parametrize(
        "input_str,expected",
        [
            ("item1,item2,item3", ["item1", "item2", "item3"]),
            ("item1 , item2 ,  item3", ["item1", "item2", "item3"]),
            ("single", ["single"]),
            (None, None),
            ("", None),
            ("item1,,item2", ["item1", "", "item2"]),
            ("item-1,item_2,item.3", ["item-1", "item_2", "item.3"]),
        ],
    )
    def test_parse_comma_separated_list(self, input_str, expected):
        """Test parsing comma-separated lists with various inputs."""
        result = parse_comma_separated_list(input_str)
        assert result == expected


class TestGetOrCreateClient:
    """Tests for get_or_create_client function."""

    def test_creates_client_on_first_call(self):
        """Test that client is created on first call."""
        from unittest.mock import patch

        ctx = MagicMock()
        ctx.obj = {}

        with patch("langsmith.Client") as MockClient:
            mock_client = MagicMock()
            MockClient.return_value = mock_client

            result = get_or_create_client(ctx)

            assert result == mock_client
            MockClient.assert_called_once()
            assert ctx.obj["client"] == mock_client

    def test_reuses_existing_client(self):
        """Test that existing client is reused."""
        from unittest.mock import patch

        mock_client = MagicMock()
        ctx = MagicMock()
        ctx.obj = {"client": mock_client}

        with patch("langsmith.Client") as MockClient:
            result = get_or_create_client(ctx)

            assert result == mock_client
            # Should not create new client
            MockClient.assert_not_called()


class TestExtractWildcardSearchTerm:
    """Tests for extract_wildcard_search_term function."""

    @pytest.mark.parametrize(
        "pattern,expected_term,expected_unanchored",
        [
            ("*moments*", "moments", True),
            ("moments*", "moments", False),
            ("*moments", "moments", False),
            ("moments", "moments", False),
            (None, None, False),
            ("", None, False),
            ("*test?*", "test", True),
            ("***", None, True),
        ],
    )
    def test_extract_wildcard_search_term(
        self, pattern, expected_term, expected_unanchored
    ):
        """Test wildcard pattern extraction with various patterns."""
        search_term, is_unanchored = extract_wildcard_search_term(pattern)
        assert search_term == expected_term
        assert is_unanchored is expected_unanchored


class TestExtractRegexSearchTerm:
    """Tests for extract_regex_search_term function."""

    @pytest.mark.parametrize(
        "pattern,expected_term,min_length",
        [
            ("moments", "moments", 2),
            (".*moments.*", "moments", 2),
            ("^test-.*-v[0-9]+$", "test--v0-9", 2),
            ("^a+$", None, 2),
            (None, None, 2),
            ("", None, 2),
            (".*+?^$", None, 2),
            ("^a$", "a", 1),
            ("^a$", None, 3),
        ],
    )
    def test_extract_regex_search_term(self, pattern, expected_term, min_length):
        """Test regex pattern extraction with various patterns."""
        search_term = extract_regex_search_term(pattern, min_length=min_length)
        assert search_term == expected_term


class TestSafeModelDump:
    """Tests for safe_model_dump function."""

    def test_pydantic_v2_model(self):
        """Test with Pydantic v2 model (has model_dump)."""
        mock_model = MagicMock()
        mock_model.model_dump.return_value = {"name": "test", "id": "123"}

        result = safe_model_dump(mock_model)

        assert result == {"name": "test", "id": "123"}
        mock_model.model_dump.assert_called_once_with(include=None, mode="json")

    def test_pydantic_v2_model_with_include(self):
        """Test with Pydantic v2 model with field selection."""
        mock_model = MagicMock()
        mock_model.model_dump.return_value = {"name": "test"}

        safe_model_dump(mock_model, include={"name", "id"})

        mock_model.model_dump.assert_called_once_with(
            include={"name", "id"}, mode="json"
        )

    def test_pydantic_v1_model(self):
        """Test with Pydantic v1 model (has dict method)."""
        mock_model = MagicMock()
        mock_model.dict.return_value = {"name": "test", "id": "123"}
        del mock_model.model_dump  # Simulate v1 model

        result = safe_model_dump(mock_model)

        assert result == {"name": "test", "id": "123"}

    def test_pydantic_v1_model_with_include(self):
        """Test with Pydantic v1 model with field selection."""
        mock_model = MagicMock()
        mock_model.dict.return_value = {"name": "test", "id": "123", "extra": "field"}
        del mock_model.model_dump  # Simulate v1 model

        result = safe_model_dump(mock_model, include={"name", "id"})

        assert result == {"name": "test", "id": "123"}
        assert "extra" not in result

    def test_plain_dict(self):
        """Test with plain dictionary."""
        data = {"name": "test", "id": "123"}

        result = safe_model_dump(data)

        assert result == {"name": "test", "id": "123"}

    def test_plain_dict_with_include(self):
        """Test with plain dictionary with field selection."""
        data = {"name": "test", "id": "123", "extra": "field"}

        result = safe_model_dump(data, include={"name", "id"})

        assert result == {"name": "test", "id": "123"}
        assert "extra" not in result


class TestApplyClientSideLimit:
    """Tests for apply_client_side_limit function."""

    def test_no_limit_returns_all(self):
        """Test that None limit returns all items."""
        items = [MockItem(name=f"item{i}") for i in range(10)]

        result = apply_client_side_limit(items, None, has_client_filters=True)

        assert len(result) == 10

    def test_limit_without_client_filters(self):
        """Test that limit is not applied when no client-side filtering."""
        items = [MockItem(name=f"item{i}") for i in range(10)]

        result = apply_client_side_limit(items, 3, has_client_filters=False)

        # Should return all items since no client filtering
        assert len(result) == 10

    def test_limit_with_client_filters(self):
        """Test that limit is applied when client-side filtering is used."""
        items = [MockItem(name=f"item{i}") for i in range(10)]

        result = apply_client_side_limit(items, 3, has_client_filters=True)

        assert len(result) == 3
        assert result[0].name == "item0"
        assert result[1].name == "item1"
        assert result[2].name == "item2"

    def test_limit_greater_than_items(self):
        """Test limit greater than number of items."""
        items = [MockItem(name=f"item{i}") for i in range(3)]

        result = apply_client_side_limit(items, 10, has_client_filters=True)

        assert len(result) == 3

    def test_empty_items_list(self):
        """Test with empty items list."""
        result = apply_client_side_limit([], 5, has_client_filters=True)

        assert result == []


class TestRenderOutput:
    """Tests for render_output function."""

    def test_render_json_format(self, capsys):
        """Test rendering JSON format."""
        # Use dicts instead of dataclass items
        items = [{"name": "test1", "value": 10}, {"name": "test2", "value": 20}]
        ctx = MagicMock()
        ctx.obj = {"json": True}

        render_output(
            items,
            table_builder=None,
            ctx=ctx,
            include_fields={"name"},
        )

        captured = capsys.readouterr()
        result = json.loads(captured.out)
        assert len(result) == 2
        assert result[0]["name"] == "test1"

    def test_render_with_explicit_format(self, capsys):
        """Test rendering with explicit format override."""
        # Use dicts instead of dataclass items
        items = [{"name": "test1", "value": 10}]
        ctx = MagicMock()
        ctx.obj = {"json": False}

        render_output(
            items,
            table_builder=None,
            ctx=ctx,
            output_format="json",
            include_fields={"name"},
        )

        captured = capsys.readouterr()
        result = json.loads(captured.out)
        assert len(result) == 1
        assert result[0]["name"] == "test1"

    def test_render_empty_results_table_format(self, capsys):
        """Test rendering empty results in table format."""
        ctx = MagicMock()
        ctx.obj = {"json": False}

        render_output(
            [],
            table_builder=None,
            ctx=ctx,
            empty_message="No items found",
        )

        captured = capsys.readouterr()
        assert "No items found" in captured.out

    def test_render_empty_results_json_format(self, capsys):
        """Test rendering empty results in JSON format."""
        ctx = MagicMock()
        ctx.obj = {"json": True}

        render_output(
            [],
            table_builder=None,
            ctx=ctx,
        )

        captured = capsys.readouterr()
        result = json.loads(captured.out)
        assert result == []

    def test_render_single_item(self, capsys):
        """Test rendering single item (not a list)."""
        # Use dict instead of dataclass
        item = {"name": "single", "value": 1}
        ctx = MagicMock()
        ctx.obj = {"json": True}

        render_output(
            item,
            table_builder=None,
            ctx=ctx,
            include_fields={"name"},
        )

        captured = capsys.readouterr()
        result = json.loads(captured.out)
        assert len(result) == 1
        assert result[0]["name"] == "single"

    def test_render_with_table_builder(self):
        """Test rendering with table builder function."""
        from unittest.mock import patch

        # Use dicts instead of dataclass items
        items = [{"name": "test1"}, {"name": "test2"}]
        ctx = MagicMock()
        ctx.obj = {"json": False}

        mock_table = MagicMock()

        def table_builder(data):
            assert len(data) == 2
            return mock_table

        with patch("rich.console.Console") as MockConsole:
            mock_console = MockConsole.return_value

            render_output(
                items,
                table_builder=table_builder,
                ctx=ctx,
            )

            mock_console.print.assert_called_once_with(mock_table)


class TestFilterFields:
    """Tests for filter_fields function."""

    def test_filter_single_model_with_fields(self):
        """Test filtering a single Pydantic model with specific fields."""
        from pydantic import BaseModel

        class TestModel(BaseModel):
            id: str
            name: str
            description: str
            extra: str

        model = TestModel(
            id="123", name="test", description="A test model", extra="extra_value"
        )

        result = filter_fields(model, "id,name")

        assert isinstance(result, dict)
        assert result == {"id": "123", "name": "test"}
        assert "description" not in result
        assert "extra" not in result

    def test_filter_single_model_without_fields(self):
        """Test that None fields returns all fields for single model."""
        from pydantic import BaseModel

        class TestModel(BaseModel):
            id: str
            name: str
            value: int

        model = TestModel(id="123", name="test", value=42)

        result = filter_fields(model, None)

        assert isinstance(result, dict)
        assert result == {"id": "123", "name": "test", "value": 42}

    def test_filter_list_of_models_with_fields(self):
        """Test filtering a list of Pydantic models with specific fields."""
        from pydantic import BaseModel

        class TestModel(BaseModel):
            id: str
            name: str
            description: str

        models = [
            TestModel(id="1", name="test1", description="desc1"),
            TestModel(id="2", name="test2", description="desc2"),
        ]

        result = filter_fields(models, "id,name")

        assert isinstance(result, list)
        assert len(result) == 2
        assert result[0] == {"id": "1", "name": "test1"}
        assert result[1] == {"id": "2", "name": "test2"}
        assert "description" not in result[0]

    def test_filter_list_of_models_without_fields(self):
        """Test that None fields returns all fields for list of models."""
        from pydantic import BaseModel

        class TestModel(BaseModel):
            id: str
            name: str

        models = [
            TestModel(id="1", name="test1"),
            TestModel(id="2", name="test2"),
        ]

        result = filter_fields(models, None)

        assert isinstance(result, list)
        assert len(result) == 2
        assert result[0] == {"id": "1", "name": "test1"}
        assert result[1] == {"id": "2", "name": "test2"}

    def test_filter_with_whitespace_in_fields(self):
        """Test that fields with whitespace are properly trimmed."""
        from pydantic import BaseModel

        class TestModel(BaseModel):
            id: str
            name: str
            extra: str

        model = TestModel(id="123", name="test", extra="value")

        result = filter_fields(model, "id ,  name  ")

        assert result == {"id": "123", "name": "test"}
        assert "extra" not in result

    def test_filter_empty_list(self):
        """Test filtering an empty list."""
        result = filter_fields([], None)

        assert isinstance(result, list)
        assert result == []

        result = filter_fields([], "id,name")

        assert isinstance(result, list)
        assert result == []

    def test_filter_with_nonexistent_field(self):
        """Test that requesting nonexistent fields returns empty dict."""
        from pydantic import BaseModel

        class TestModel(BaseModel):
            id: str
            name: str

        model = TestModel(id="123", name="test")

        result = filter_fields(model, "nonexistent")

        # Pydantic's include parameter will just exclude fields not in the set
        # So requesting nonexistent fields returns an empty dict
        assert result == {}

    def test_filter_preserves_json_mode(self):
        """Test that model_dump uses mode='json' for JSON-compatible output."""
        from pydantic import BaseModel
        from datetime import datetime, timezone

        class TestModel(BaseModel):
            id: str
            created_at: datetime

        model = TestModel(
            id="123", created_at=datetime(2024, 1, 14, tzinfo=timezone.utc)
        )

        result = filter_fields(model, None)

        # In JSON mode, datetime should be serialized as string
        assert isinstance(result["created_at"], str)
        assert "2024" in result["created_at"]


class TestExtractModelName:
    """Tests for extract_model_name function using real Run models."""

    @pytest.mark.parametrize(
        "extra,expected_result",
        [
            # Standard cases
            ({"invocation_params": {"model_name": "gpt-4"}}, "gpt-4"),
            ({"metadata": {"ls_model_name": "claude-3"}}, "claude-3"),
            # Priority: invocation_params over metadata
            (
                {
                    "invocation_params": {"model_name": "gpt-4"},
                    "metadata": {"ls_model_name": "claude-3"},
                },
                "gpt-4",
            ),
            # Missing/empty cases
            (None, "-"),
            ({}, "-"),
            ({"invocation_params": {"other_param": "value"}}, "-"),
            # Malformed cases
            ({"invocation_params": "not-a-dict"}, "-"),
            ({"metadata": "not-a-dict"}, "-"),
        ],
    )
    def test_extract_model_name_scenarios(self, extra, expected_result):
        """Test model name extraction with various extra field configurations."""
        run = Run(
            id=UUID("12345678-1234-5678-1234-567812345678"),
            name="test-run",
            run_type="llm",
            start_time=datetime.now(timezone.utc),
            extra=extra,
        )

        result = extract_model_name(run)
        assert result == expected_result

    @pytest.mark.parametrize(
        "model_name,max_length,expected",
        [
            ("very-long-model-name-that-exceeds-limit", 20, "very-long-model-n..."),
            ("longmodelname", 10, "longmod..."),
            ("short", 20, "short"),
            ("exactly-twenty-char", 20, "exactly-twenty-char"),
        ],
    )
    def test_truncate_long_model_names(self, model_name, max_length, expected):
        """Test truncation of model names with different lengths."""
        run = Run(
            id=UUID("12345678-1234-5678-1234-567812345678"),
            name="test-run",
            run_type="llm",
            start_time=datetime.now(timezone.utc),
            extra={"invocation_params": {"model_name": model_name}},
        )

        result = extract_model_name(run, max_length=max_length)
        assert result == expected
        assert len(result) <= max_length


class TestFormatTokenCount:
    """Tests for format_token_count function."""

    @pytest.mark.parametrize(
        "tokens,expected",
        [
            (1234, "1,234"),
            (1234567, "1,234,567"),
            (42, "42"),
            (0, "-"),
            (None, "-"),
        ],
    )
    def test_format_token_count(self, tokens, expected):
        """Test token count formatting with various inputs."""
        result = format_token_count(tokens)
        assert result == expected


class TestParseTimeInput:
    """Tests for parse_time_input function."""

    def test_iso_format_with_z_suffix(self):
        """Test parsing ISO format with Z suffix."""
        result = parse_time_input("2024-01-14T10:00:00Z")
        assert result.year == 2024
        assert result.month == 1
        assert result.day == 14
        assert result.hour == 10

    def test_iso_format_with_timezone(self):
        """Test parsing ISO format with explicit timezone."""
        result = parse_time_input("2024-01-14T10:00:00+00:00")
        assert result.year == 2024
        assert result.month == 1

    def test_iso_date_only(self):
        """Test parsing ISO date without time."""
        result = parse_time_input("2024-01-14")
        assert result.year == 2024
        assert result.month == 1
        assert result.day == 14

    @pytest.mark.parametrize(
        "shorthand,expected_delta_type",
        [
            ("30m", "minutes"),
            ("24h", "hours"),
            ("7d", "days"),
            ("2w", "weeks"),
            ("30M", "minutes"),  # Case insensitive
            ("24H", "hours"),
            ("7D", "days"),
            ("2W", "weeks"),
        ],
    )
    def test_relative_shorthand_formats(self, shorthand, expected_delta_type):
        """Test parsing relative shorthand formats like '24h', '7d'."""
        result = parse_time_input(shorthand)
        # Result should be a datetime in the past
        now = datetime.now(timezone.utc)
        assert result < now

    @pytest.mark.parametrize(
        "natural,expected_delta_type",
        [
            ("3 days ago", "days"),
            ("1 hour ago", "hours"),
            ("30 minutes ago", "minutes"),
            ("2 weeks ago", "weeks"),
            ("5 min ago", "minutes"),
            ("2 hrs ago", "hours"),
            ("1 wk ago", "weeks"),
        ],
    )
    def test_natural_language_formats(self, natural, expected_delta_type):
        """Test parsing natural language time formats."""
        result = parse_time_input(natural)
        now = datetime.now(timezone.utc)
        assert result < now

    def test_invalid_format_raises_error(self):
        """Test that invalid format raises BadParameter."""
        with pytest.raises(click.BadParameter, match="Invalid time format"):
            parse_time_input("invalid")

    def test_invalid_format_with_partial_match(self):
        """Test that partial matches don't work."""
        with pytest.raises(click.BadParameter):
            parse_time_input("yesterday")  # Not supported

    def test_whitespace_handling(self):
        """Test that whitespace is properly trimmed."""
        result = parse_time_input("  3 days ago  ")
        now = datetime.now(timezone.utc)
        assert result < now


class TestBuildTimeFqlFilters:
    """Tests for build_time_fql_filters function."""

    def test_no_filters_returns_empty(self):
        """Test that no filters returns empty list."""
        result = build_time_fql_filters()
        assert result == []

    def test_none_values_returns_empty(self):
        """Test that None values return empty list."""
        result = build_time_fql_filters(since=None, last=None)
        assert result == []

    def test_since_filter_creates_fql(self):
        """Test that --since creates proper FQL filter."""
        result = build_time_fql_filters(since="3d")
        assert len(result) == 1
        assert result[0].startswith('gt(start_time, "')
        assert result[0].endswith('")')

    def test_last_filter_creates_fql(self):
        """Test that --last creates proper FQL filter."""
        result = build_time_fql_filters(last="24h")
        assert len(result) == 1
        assert result[0].startswith('gt(start_time, "')

    def test_both_filters_create_two_fql(self):
        """Test that both --since and --last create two FQL filters."""
        result = build_time_fql_filters(since="7d", last="24h")
        assert len(result) == 2
        for filter_str in result:
            assert filter_str.startswith('gt(start_time, "')

    def test_natural_language_since(self):
        """Test that natural language works in --since."""
        result = build_time_fql_filters(since="3 days ago")
        assert len(result) == 1
        assert "gt(start_time" in result[0]

    def test_invalid_since_raises_error(self):
        """Test that invalid --since raises BadParameter."""
        with pytest.raises(click.BadParameter):
            build_time_fql_filters(since="invalid")

    def test_invalid_last_raises_error(self):
        """Test that invalid --last raises BadParameter."""
        with pytest.raises(click.BadParameter):
            build_time_fql_filters(last="invalid")
