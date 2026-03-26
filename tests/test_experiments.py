"""
Tests for experiments command group.

INVARIANT: experiments results command must be reachable and display
run stats and feedback stats for a named experiment.

Note: ExperimentResults is a TypedDict (not a Pydantic model), so test data
is constructed as a dict matching the TypedDict shape.
"""

from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from langsmith.utils import LangSmithNotFoundError

from langsmith_cli.main import cli
from conftest import strip_ansi, parse_json_output


def _make_experiment_results(
    run_count: int = 5,
    feedback_stats: dict | None = None,
) -> dict:
    """Create a mock ExperimentResults TypedDict for testing."""
    if feedback_stats is None:
        feedback_stats = {"correctness": 0.8, "helpfulness": 0.9}
    return {
        "run_stats": {
            "run_count": run_count,
            "latency_p50": timedelta(seconds=1.5),
            "latency_p99": timedelta(seconds=3.2),
            "total_tokens": 1000,
            "prompt_tokens": 700,
            "completion_tokens": 300,
            "error_rate": 0.0,
            "total_cost": Decimal("0.05"),
            "prompt_cost": None,
            "completion_cost": None,
            "last_run_start_time": None,
            "run_facets": None,
            "first_token_p50": None,
            "first_token_p99": None,
        },
        "feedback_stats": feedback_stats,
        "examples_with_runs": iter([]),
    }


def test_experiments_results_exits_zero(runner):
    """INVARIANT: experiments results exits 0 for a valid experiment name."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        mock_client.get_experiment_results.return_value = _make_experiment_results()
        result = runner.invoke(cli, ["experiments", "results", "my-experiment"])
        assert result.exit_code == 0


def test_experiments_results_shows_stats(runner):
    """INVARIANT: experiments results displays run_count and feedback stats."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        mock_client.get_experiment_results.return_value = _make_experiment_results(
            run_count=42,
            feedback_stats={"correctness": 0.85},
        )
        result = runner.invoke(cli, ["experiments", "results", "my-experiment"])
        assert result.exit_code == 0
        output = strip_ansi(result.output)
        assert "42" in output
        assert "correctness" in output


def test_experiments_results_json(runner):
    """INVARIANT: experiments results --json produces valid JSON with stats."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        mock_client.get_experiment_results.return_value = _make_experiment_results(
            run_count=10,
            feedback_stats={"correctness": 0.9},
        )
        result = runner.invoke(
            cli, ["--json", "experiments", "results", "my-experiment"]
        )
        assert result.exit_code == 0
        data = parse_json_output(result.output)
        assert "run_stats" in data
        assert "feedback_stats" in data


def test_experiments_results_passes_name_to_sdk(runner):
    """INVARIANT: experiment name is passed to get_experiment_results SDK call."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        mock_client.get_experiment_results.return_value = _make_experiment_results()
        result = runner.invoke(
            cli, ["experiments", "results", "my-specific-experiment"]
        )
        assert result.exit_code == 0
        mock_client.get_experiment_results.assert_called_once()
        call_kwargs = mock_client.get_experiment_results.call_args[1]
        assert call_kwargs.get("name") == "my-specific-experiment"


def test_experiments_results_not_found(runner):
    """INVARIANT: experiments results exits non-zero when experiment name not found."""
    with patch("langsmith.Client") as MockClient:
        mock_client = MockClient.return_value
        mock_client.get_experiment_results.side_effect = LangSmithNotFoundError(
            "not found"
        )
        result = runner.invoke(
            cli, ["experiments", "results", "nonexistent-experiment"]
        )
        assert result.exit_code != 0
        assert "not found" in result.output.lower()
