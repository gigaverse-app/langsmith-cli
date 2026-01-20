"""Field analysis utilities for runs fields and runs describe commands."""

from collections import Counter
from dataclasses import dataclass, field
from collections.abc import Sequence
from typing import Any

# Language detection constants
LANG_DETECT_SAMPLE_SIZE = 500  # chars per field value
LANG_DETECT_MIN_LENGTH = 30  # minimum chars to attempt detection
LANG_DETECT_MAX_SAMPLES = 100  # max values to sample per field
LANG_TOP_N = 3  # show top N languages


@dataclass
class FieldStats:
    """Statistics for a single field across runs."""

    path: str
    field_type: str
    present_count: int
    total_count: int
    # Length stats (for strings)
    length_min: int | None = None
    length_max: int | None = None
    length_avg: float | None = None
    length_p50: float | None = None
    # Numeric stats
    num_min: float | None = None
    num_max: float | None = None
    num_avg: float | None = None
    num_p50: float | None = None
    num_sum: float | None = None
    # Language detection results: {"en": 80.0, "he": 15.0, "others": 5.0}
    languages: dict[str, float] = field(default_factory=dict)
    # Sample value
    sample: str | None = None

    @property
    def present_pct(self) -> float:
        """Percentage of runs with this field present."""
        if self.total_count == 0:
            return 0.0
        return round(100.0 * self.present_count / self.total_count, 1)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON output."""
        result: dict[str, Any] = {
            "path": self.path,
            "type": self.field_type,
            "present_count": self.present_count,
            "present_pct": self.present_pct,
        }

        if self.length_min is not None:
            result["length"] = {
                "min": self.length_min,
                "max": self.length_max,
                "avg": round(self.length_avg, 1) if self.length_avg else None,
                "p50": round(self.length_p50, 1) if self.length_p50 else None,
            }

        if self.num_min is not None:
            result["numeric"] = {
                "min": self.num_min,
                "max": self.num_max,
                "avg": round(self.num_avg, 3) if self.num_avg else None,
                "p50": round(self.num_p50, 3) if self.num_p50 else None,
                "sum": self.num_sum,
            }

        if self.languages:
            result["languages"] = self.languages

        if self.sample is not None:
            result["sample"] = self.sample

        return result


def extract_nested_fields(
    data: dict[str, Any],
    prefix: str = "",
    max_depth: int = 5,
) -> dict[str, Any]:
    """Extract all fields from nested dictionary with dot-notation paths.

    Args:
        data: Dictionary to extract fields from
        prefix: Current path prefix (for recursion)
        max_depth: Maximum recursion depth to prevent infinite loops

    Returns:
        Dictionary mapping field paths to values, e.g.:
        {"inputs.query": "hello", "outputs.answer": "world", "metadata.model": "gpt-4"}
    """
    if max_depth <= 0:
        return {}

    result: dict[str, Any] = {}

    for key, value in data.items():
        path = f"{prefix}.{key}" if prefix else key

        if isinstance(value, dict) and value:
            # Recurse into nested dict
            nested = extract_nested_fields(value, path, max_depth - 1)
            result.update(nested)
        else:
            # Store leaf value
            result[path] = value

    return result


def get_value_type(value: Any) -> str:
    """Determine the type string for a value.

    Returns:
        Type string: "string", "int", "float", "bool", "list", "dict", "null"
    """
    if value is None:
        return "null"
    elif isinstance(value, bool):
        return "bool"
    elif isinstance(value, int):
        return "int"
    elif isinstance(value, float):
        return "float"
    elif isinstance(value, str):
        return "string"
    elif isinstance(value, list):
        return "list"
    elif isinstance(value, dict):
        return "dict"
    else:
        return type(value).__name__


def detect_language_safe(text: str) -> str | None:
    """Safely detect language of text.

    Args:
        text: Text to analyze (will be truncated to LANG_DETECT_SAMPLE_SIZE)

    Returns:
        ISO 639-1 language code (e.g., "en", "he", "es") or None if detection fails
    """
    if len(text) < LANG_DETECT_MIN_LENGTH:
        return None

    # Truncate to sample size
    sample = text[:LANG_DETECT_SAMPLE_SIZE]

    try:
        # Lazy import for performance
        from langdetect import detect

        return detect(sample)
    except Exception:
        # langdetect can throw LangDetectException and various other errors on edge cases
        return None


def compute_language_distribution(
    values: list[str],
    max_samples: int = LANG_DETECT_MAX_SAMPLES,
) -> dict[str, float]:
    """Compute language distribution for a list of string values.

    Args:
        values: List of string values to analyze
        max_samples: Maximum number of values to sample

    Returns:
        Dictionary mapping language codes to percentages, e.g.:
        {"en": 80.0, "he": 15.0, "others": 5.0}
        Returns empty dict if no languages detected.
    """
    if not values:
        return {}

    # Sample if too many values
    sample_values = values[:max_samples]

    # Detect languages
    lang_counts: Counter[str] = Counter()
    total_detected = 0

    for text in sample_values:
        if not isinstance(text, str) or len(text) < LANG_DETECT_MIN_LENGTH:
            continue

        lang = detect_language_safe(text)
        if lang:
            lang_counts[lang] += 1
            total_detected += 1

    if total_detected == 0:
        return {}

    # Convert to percentages
    lang_pcts: dict[str, float] = {}
    for lang, count in lang_counts.most_common():
        pct = round(100.0 * count / total_detected, 1)
        lang_pcts[lang] = pct

    # Collapse to top N + others
    return collapse_languages(lang_pcts, top_n=LANG_TOP_N)


def collapse_languages(
    lang_pcts: dict[str, float],
    top_n: int = LANG_TOP_N,
) -> dict[str, float]:
    """Collapse language percentages to top N + others.

    Args:
        lang_pcts: Full language distribution
        top_n: Number of top languages to keep

    Returns:
        Collapsed distribution with top N languages and "others" for the rest
    """
    if len(lang_pcts) <= top_n:
        return lang_pcts

    # Sort by percentage descending
    sorted_langs = sorted(lang_pcts.items(), key=lambda x: x[1], reverse=True)

    result: dict[str, float] = {}
    others_pct = 0.0

    for i, (lang, pct) in enumerate(sorted_langs):
        if i < top_n:
            result[lang] = pct
        else:
            others_pct += pct

    if others_pct > 0:
        result["others"] = round(others_pct, 1)

    return result


def format_languages_display(languages: dict[str, float]) -> str:
    """Format language distribution for table display.

    Args:
        languages: Dictionary of language codes to percentages

    Returns:
        Formatted string like "en: 80%, he: 15%, others: 5%"
        or "-" if no languages detected
    """
    if not languages:
        return "-"

    parts = []
    for lang, pct in languages.items():
        # Format percentage without decimal if whole number
        if pct == int(pct):
            parts.append(f"{lang}: {int(pct)}%")
        else:
            parts.append(f"{lang}: {pct}%")

    return ", ".join(parts)


def format_length_stats(stats: FieldStats) -> str:
    """Format length statistics for table display.

    Returns:
        String like "min=5, max=500, avg=89, p50=72" or "-" if no stats
    """
    if stats.length_min is None:
        return "-"

    parts = [
        f"min={stats.length_min}",
        f"max={stats.length_max}",
    ]

    if stats.length_avg is not None:
        parts.append(f"avg={int(stats.length_avg)}")

    if stats.length_p50 is not None:
        parts.append(f"p50={int(stats.length_p50)}")

    return ", ".join(parts)


def format_numeric_stats(stats: FieldStats) -> str:
    """Format numeric statistics for table display.

    Returns:
        String like "min=0.1, max=45, p50=1.2" or "-" if no stats
    """
    if stats.num_min is None:
        return "-"

    def fmt(val: float | None) -> str:
        if val is None:
            return "?"
        if val == int(val):
            return str(int(val))
        return f"{val:.2f}"

    parts = [
        f"min={fmt(stats.num_min)}",
        f"max={fmt(stats.num_max)}",
        f"p50={fmt(stats.num_p50)}",
    ]

    return ", ".join(parts)


def percentile(values: Sequence[float], p: float) -> float:
    """Compute percentile of sorted values.

    Args:
        values: Sequence of numeric values (will be sorted)
        p: Percentile (0-100)

    Returns:
        Percentile value
    """
    if not values:
        return 0.0

    sorted_values = sorted(values)
    n = len(sorted_values)
    k = (n - 1) * p / 100.0
    f = int(k)
    c = f + 1 if f + 1 < n else f

    if f == c:
        return sorted_values[f]

    return sorted_values[f] * (c - k) + sorted_values[c] * (k - f)


def analyze_field_values(
    path: str,
    values: list[Any],
    total_count: int,
    detect_languages: bool = True,
) -> FieldStats:
    """Analyze values for a single field.

    Args:
        path: Field path (e.g., "inputs.query")
        values: Non-null values for this field
        total_count: Total number of runs analyzed
        detect_languages: Whether to perform language detection

    Returns:
        FieldStats with computed statistics
    """
    present_count = len(values)

    if present_count == 0:
        return FieldStats(
            path=path,
            field_type="unknown",
            present_count=0,
            total_count=total_count,
        )

    # Determine type from first non-null value
    field_type = get_value_type(values[0])

    # Initialize stats
    stats = FieldStats(
        path=path,
        field_type=field_type,
        present_count=present_count,
        total_count=total_count,
    )

    # Compute type-specific stats
    if field_type == "string":
        str_values: list[str] = [v for v in values if isinstance(v, str)]
        if str_values:
            lengths = [len(s) for s in str_values]
            stats.length_min = min(lengths)
            stats.length_max = max(lengths)
            stats.length_avg = sum(lengths) / len(lengths)
            stats.length_p50 = percentile(lengths, 50)

            # Language detection
            if detect_languages:
                stats.languages = compute_language_distribution(str_values)

            # Sample value (truncate if too long)
            sample = str_values[0]
            if len(sample) > 50:
                stats.sample = sample[:47] + "..."
            else:
                stats.sample = sample

    elif field_type in ("int", "float"):
        num_values: list[float] = [
            float(v) for v in values if isinstance(v, (int, float))
        ]
        if num_values:
            stats.num_min = min(num_values)
            stats.num_max = max(num_values)
            stats.num_avg = sum(num_values) / len(num_values)
            stats.num_p50 = percentile(num_values, 50)
            stats.num_sum = sum(num_values)

    elif field_type == "list":
        # Show sample and list length stats
        list_values: list[list[Any]] = [v for v in values if isinstance(v, list)]
        if list_values:
            lengths = [len(lst) for lst in list_values]
            stats.length_min = min(lengths)
            stats.length_max = max(lengths)
            stats.length_avg = sum(lengths) / len(lengths)
            stats.length_p50 = percentile(lengths, 50)

            # Sample
            sample = list_values[0]
            sample_str = str(sample)
            if len(sample_str) > 50:
                stats.sample = sample_str[:47] + "..."
            else:
                stats.sample = sample_str

    else:
        # For other types, just show sample
        sample_str = str(values[0])
        if len(sample_str) > 50:
            stats.sample = sample_str[:47] + "..."
        else:
            stats.sample = sample_str

    return stats


def analyze_runs_fields(
    runs_data: list[dict[str, Any]],
    detect_languages: bool = True,
) -> list[FieldStats]:
    """Analyze all fields across multiple runs.

    Args:
        runs_data: List of run dictionaries (from model_dump)
        detect_languages: Whether to perform language detection

    Returns:
        List of FieldStats for each discovered field, sorted by path
    """
    if not runs_data:
        return []

    total_count = len(runs_data)

    # Collect all values by field path
    field_values: dict[str, list[Any]] = {}

    for run_data in runs_data:
        fields = extract_nested_fields(run_data)
        for path, value in fields.items():
            if path not in field_values:
                field_values[path] = []
            if value is not None:
                field_values[path].append(value)

    # Analyze each field
    stats_list: list[FieldStats] = []
    for path in sorted(field_values.keys()):
        values = field_values[path]
        stats = analyze_field_values(path, values, total_count, detect_languages)
        stats_list.append(stats)

    return stats_list


def filter_fields_by_path(
    stats_list: list[FieldStats],
    include_paths: list[str] | None = None,
    exclude_paths: list[str] | None = None,
) -> list[FieldStats]:
    """Filter field stats by path patterns.

    Args:
        stats_list: List of FieldStats to filter
        include_paths: If provided, only include fields starting with these paths
        exclude_paths: If provided, exclude fields starting with these paths

    Returns:
        Filtered list of FieldStats
    """
    result = stats_list

    if include_paths:
        result = [
            s
            for s in result
            if any(s.path.startswith(p) or s.path == p for p in include_paths)
        ]

    if exclude_paths:
        result = [
            s
            for s in result
            if not any(s.path.startswith(p) or s.path == p for p in exclude_paths)
        ]

    return result
