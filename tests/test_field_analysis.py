"""Tests for field_analysis module."""

from langsmith_cli.field_analysis import (
    FieldStats,
    analyze_field_values,
    analyze_runs_fields,
    collapse_languages,
    compute_language_distribution,
    detect_language_safe,
    extract_nested_fields,
    filter_fields_by_path,
    format_languages_display,
    format_length_stats,
    format_numeric_stats,
    get_value_type,
    percentile,
)


class TestExtractNestedFields:
    """Tests for extract_nested_fields function."""

    def test_flat_dict(self):
        """Extracts fields from flat dictionary."""
        data = {"name": "test", "value": 123}
        result = extract_nested_fields(data)
        assert result == {"name": "test", "value": 123}

    def test_nested_dict(self):
        """Extracts fields from nested dictionary with dot notation."""
        data = {
            "inputs": {"query": "hello", "context": "world"},
            "outputs": {"answer": "hi"},
        }
        result = extract_nested_fields(data)
        assert result == {
            "inputs.query": "hello",
            "inputs.context": "world",
            "outputs.answer": "hi",
        }

    def test_deeply_nested(self):
        """Handles deeply nested structures."""
        data = {"a": {"b": {"c": {"d": "deep"}}}}
        result = extract_nested_fields(data)
        assert result == {"a.b.c.d": "deep"}

    def test_mixed_types(self):
        """Handles mixed types at leaf nodes."""
        data = {
            "string": "text",
            "number": 42,
            "float": 3.14,
            "bool": True,
            "null": None,
            "list": [1, 2, 3],
        }
        result = extract_nested_fields(data)
        assert result["string"] == "text"
        assert result["number"] == 42
        assert result["list"] == [1, 2, 3]

    def test_max_depth_limit(self):
        """Respects max depth to prevent infinite recursion."""
        data = {"a": {"b": {"c": {"d": {"e": {"f": "too deep"}}}}}}
        result = extract_nested_fields(data, max_depth=3)
        # Should stop before reaching 'f'
        assert "a.b.c.d.e.f" not in result

    def test_empty_dict(self):
        """Handles empty dictionary."""
        result = extract_nested_fields({})
        assert result == {}


class TestGetValueType:
    """Tests for get_value_type function."""

    def test_string(self):
        assert get_value_type("hello") == "string"

    def test_int(self):
        assert get_value_type(42) == "int"

    def test_float(self):
        assert get_value_type(3.14) == "float"

    def test_bool(self):
        assert get_value_type(True) == "bool"

    def test_none(self):
        assert get_value_type(None) == "null"

    def test_list(self):
        assert get_value_type([1, 2, 3]) == "list"

    def test_dict(self):
        assert get_value_type({"a": 1}) == "dict"


class TestPercentile:
    """Tests for percentile function."""

    def test_empty_list(self):
        """Returns 0 for empty list."""
        assert percentile([], 50) == 0.0

    def test_single_value(self):
        """Returns the value for single-element list."""
        assert percentile([5.0], 50) == 5.0

    def test_median_odd(self):
        """Computes median for odd-length list."""
        assert percentile([1.0, 2.0, 3.0, 4.0, 5.0], 50) == 3.0

    def test_median_even(self):
        """Computes median for even-length list."""
        result = percentile([1.0, 2.0, 3.0, 4.0], 50)
        assert 2.0 <= result <= 3.0

    def test_accepts_int_list(self):
        """Accepts list of integers (Sequence[float] is covariant)."""
        # This should work due to Sequence being covariant
        result = percentile([1, 2, 3, 4, 5], 50)
        assert result == 3.0


class TestDetectLanguageSafe:
    """Tests for detect_language_safe function."""

    def test_short_text_returns_none(self):
        """Returns None for text shorter than minimum length."""
        result = detect_language_safe("hi")
        assert result is None

    def test_english_text(self):
        """Detects English text."""
        text = "This is a sample text in English that should be detected correctly."
        result = detect_language_safe(text)
        assert result == "en"

    def test_truncates_long_text(self):
        """Truncates text to sample size before detection."""
        # Create very long text
        long_text = "Hello world. " * 1000
        result = detect_language_safe(long_text)
        # Should still work and detect English
        assert result == "en"


class TestComputeLanguageDistribution:
    """Tests for compute_language_distribution function."""

    def test_empty_list(self):
        """Returns empty dict for empty list."""
        result = compute_language_distribution([])
        assert result == {}

    def test_single_language(self):
        """Computes distribution for single language."""
        texts = [
            "This is English text number one.",
            "This is English text number two.",
            "This is English text number three.",
        ]
        result = compute_language_distribution(texts)
        assert "en" in result
        assert result["en"] == 100.0

    def test_short_texts_skipped(self):
        """Skips texts that are too short."""
        texts = ["hi", "yo", "ok"]
        result = compute_language_distribution(texts)
        assert result == {}


class TestCollapseLanguages:
    """Tests for collapse_languages function."""

    def test_few_languages_unchanged(self):
        """Returns unchanged if 3 or fewer languages."""
        langs = {"en": 70.0, "es": 20.0, "fr": 10.0}
        result = collapse_languages(langs, top_n=3)
        assert result == langs

    def test_collapses_to_others(self):
        """Collapses extra languages to 'others'."""
        langs = {"en": 50.0, "es": 25.0, "fr": 15.0, "de": 7.0, "it": 3.0}
        result = collapse_languages(langs, top_n=3)
        assert result["en"] == 50.0
        assert result["es"] == 25.0
        assert result["fr"] == 15.0
        assert result["others"] == 10.0
        assert "de" not in result
        assert "it" not in result


class TestFormatLanguagesDisplay:
    """Tests for format_languages_display function."""

    def test_empty_returns_dash(self):
        """Returns dash for empty languages."""
        assert format_languages_display({}) == "-"

    def test_single_language(self):
        """Formats single language correctly."""
        result = format_languages_display({"en": 100.0})
        assert result == "en: 100%"

    def test_multiple_languages(self):
        """Formats multiple languages correctly."""
        result = format_languages_display({"en": 80.0, "he": 15.0, "es": 5.0})
        assert "en: 80%" in result
        assert "he: 15%" in result
        assert "es: 5%" in result

    def test_decimal_percentage(self):
        """Includes decimal for non-whole percentages."""
        result = format_languages_display({"en": 75.5})
        assert "75.5%" in result


class TestFormatLengthStats:
    """Tests for format_length_stats function."""

    def test_no_length_stats(self):
        """Returns dash when no length stats."""
        stats = FieldStats(
            path="test", field_type="int", present_count=10, total_count=10
        )
        assert format_length_stats(stats) == "-"

    def test_with_length_stats(self):
        """Formats length stats correctly."""
        stats = FieldStats(
            path="test",
            field_type="string",
            present_count=10,
            total_count=10,
            length_min=5,
            length_max=100,
            length_avg=50.5,
            length_p50=45.0,
        )
        result = format_length_stats(stats)
        assert "min=5" in result
        assert "max=100" in result
        assert "avg=50" in result
        assert "p50=45" in result


class TestFormatNumericStats:
    """Tests for format_numeric_stats function."""

    def test_no_numeric_stats(self):
        """Returns dash when no numeric stats."""
        stats = FieldStats(
            path="test", field_type="string", present_count=10, total_count=10
        )
        assert format_numeric_stats(stats) == "-"

    def test_with_numeric_stats(self):
        """Formats numeric stats correctly."""
        stats = FieldStats(
            path="test",
            field_type="float",
            present_count=10,
            total_count=10,
            num_min=0.1,
            num_max=10.5,
            num_avg=5.25,
            num_p50=5.0,
        )
        result = format_numeric_stats(stats)
        assert "min=0.10" in result
        assert "max=10.50" in result
        assert "p50=5" in result


class TestFieldStats:
    """Tests for FieldStats dataclass."""

    def test_present_pct_calculation(self):
        """Calculates present percentage correctly."""
        stats = FieldStats(
            path="test", field_type="string", present_count=75, total_count=100
        )
        assert stats.present_pct == 75.0

    def test_present_pct_zero_total(self):
        """Returns 0 when total count is 0."""
        stats = FieldStats(
            path="test", field_type="string", present_count=0, total_count=0
        )
        assert stats.present_pct == 0.0

    def test_to_dict_basic(self):
        """Converts to dict with basic fields."""
        stats = FieldStats(
            path="test", field_type="string", present_count=80, total_count=100
        )
        result = stats.to_dict()
        assert result["path"] == "test"
        assert result["type"] == "string"
        assert result["present_count"] == 80
        assert result["present_pct"] == 80.0

    def test_to_dict_with_length(self):
        """Includes length stats in dict when present."""
        stats = FieldStats(
            path="test",
            field_type="string",
            present_count=100,
            total_count=100,
            length_min=10,
            length_max=100,
            length_avg=50.0,
            length_p50=45.0,
        )
        result = stats.to_dict()
        assert "length" in result
        assert result["length"]["min"] == 10
        assert result["length"]["max"] == 100

    def test_to_dict_with_languages(self):
        """Includes languages in dict when present."""
        stats = FieldStats(
            path="test",
            field_type="string",
            present_count=100,
            total_count=100,
            languages={"en": 80.0, "he": 20.0},
        )
        result = stats.to_dict()
        assert "languages" in result
        assert result["languages"]["en"] == 80.0


class TestAnalyzeFieldValues:
    """Tests for analyze_field_values function."""

    def test_empty_values(self):
        """Handles empty values list."""
        stats = analyze_field_values("test", [], 10, detect_languages=False)
        assert stats.present_count == 0
        assert stats.field_type == "unknown"

    def test_string_values(self):
        """Analyzes string values correctly."""
        values = ["hello world", "foo bar baz", "test"]
        stats = analyze_field_values("test", values, 10, detect_languages=False)
        assert stats.field_type == "string"
        assert stats.present_count == 3
        assert stats.length_min == 4  # "test"
        assert stats.length_max == 11  # "hello world"

    def test_numeric_values(self):
        """Analyzes numeric values correctly."""
        values = [1.0, 2.0, 3.0, 4.0, 5.0]
        stats = analyze_field_values("test", values, 10, detect_languages=False)
        assert stats.field_type == "float"
        assert stats.num_min == 1.0
        assert stats.num_max == 5.0
        assert stats.num_avg == 3.0
        assert stats.num_sum == 15.0

    def test_list_values(self):
        """Analyzes list values correctly."""
        values = [[1, 2], [1, 2, 3], [1]]
        stats = analyze_field_values("test", values, 10, detect_languages=False)
        assert stats.field_type == "list"
        assert stats.length_min == 1
        assert stats.length_max == 3


class TestAnalyzeRunsFields:
    """Tests for analyze_runs_fields function."""

    def test_empty_runs(self):
        """Returns empty list for no runs."""
        result = analyze_runs_fields([], detect_languages=False)
        assert result == []

    def test_single_run(self):
        """Analyzes single run correctly."""
        runs_data = [
            {
                "name": "test-run",
                "inputs": {"query": "hello"},
                "outputs": {"answer": "world"},
            }
        ]
        result = analyze_runs_fields(runs_data, detect_languages=False)

        # Should find fields: name, inputs.query, outputs.answer
        paths = [s.path for s in result]
        assert "name" in paths
        assert "inputs.query" in paths
        assert "outputs.answer" in paths

    def test_multiple_runs_aggregation(self):
        """Aggregates statistics across multiple runs."""
        runs_data = [
            {"value": 10},
            {"value": 20},
            {"value": 30},
        ]
        result = analyze_runs_fields(runs_data, detect_languages=False)

        value_stats = next(s for s in result if s.path == "value")
        assert value_stats.present_count == 3
        assert value_stats.num_min == 10
        assert value_stats.num_max == 30
        assert value_stats.num_avg == 20.0

    def test_partial_field_presence(self):
        """Tracks partial field presence correctly."""
        runs_data = [
            {"always": "yes", "sometimes": "here"},
            {"always": "yes"},
            {"always": "yes", "sometimes": "there"},
        ]
        result = analyze_runs_fields(runs_data, detect_languages=False)

        always_stats = next(s for s in result if s.path == "always")
        assert always_stats.present_pct == 100.0

        sometimes_stats = next(s for s in result if s.path == "sometimes")
        assert sometimes_stats.present_count == 2
        assert sometimes_stats.total_count == 3
        # 2/3 = 66.7%
        assert 66.0 <= sometimes_stats.present_pct <= 67.0


class TestFilterFieldsByPath:
    """Tests for filter_fields_by_path function."""

    def test_no_filters(self):
        """Returns all stats when no filters."""
        stats_list = [
            FieldStats(
                path="inputs.query",
                field_type="string",
                present_count=10,
                total_count=10,
            ),
            FieldStats(
                path="outputs.answer",
                field_type="string",
                present_count=10,
                total_count=10,
            ),
            FieldStats(
                path="metadata.model",
                field_type="string",
                present_count=10,
                total_count=10,
            ),
        ]
        result = filter_fields_by_path(stats_list)
        assert len(result) == 3

    def test_include_filter(self):
        """Includes only matching paths."""
        stats_list = [
            FieldStats(
                path="inputs.query",
                field_type="string",
                present_count=10,
                total_count=10,
            ),
            FieldStats(
                path="outputs.answer",
                field_type="string",
                present_count=10,
                total_count=10,
            ),
            FieldStats(
                path="metadata.model",
                field_type="string",
                present_count=10,
                total_count=10,
            ),
        ]
        result = filter_fields_by_path(stats_list, include_paths=["inputs"])
        assert len(result) == 1
        assert result[0].path == "inputs.query"

    def test_exclude_filter(self):
        """Excludes matching paths."""
        stats_list = [
            FieldStats(
                path="inputs.query",
                field_type="string",
                present_count=10,
                total_count=10,
            ),
            FieldStats(
                path="outputs.answer",
                field_type="string",
                present_count=10,
                total_count=10,
            ),
            FieldStats(
                path="metadata.model",
                field_type="string",
                present_count=10,
                total_count=10,
            ),
        ]
        result = filter_fields_by_path(stats_list, exclude_paths=["metadata"])
        assert len(result) == 2
        paths = [s.path for s in result]
        assert "metadata.model" not in paths

    def test_include_and_exclude(self):
        """Applies both include and exclude filters."""
        stats_list = [
            FieldStats(
                path="inputs.query",
                field_type="string",
                present_count=10,
                total_count=10,
            ),
            FieldStats(
                path="inputs.context",
                field_type="string",
                present_count=10,
                total_count=10,
            ),
            FieldStats(
                path="outputs.answer",
                field_type="string",
                present_count=10,
                total_count=10,
            ),
        ]
        result = filter_fields_by_path(
            stats_list, include_paths=["inputs"], exclude_paths=["inputs.context"]
        )
        assert len(result) == 1
        assert result[0].path == "inputs.query"
