"""Tests for the runs pricing command."""

import json
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import patch, MagicMock
from uuid import UUID

import pytest
from langsmith.schemas import Run

from conftest import create_run, make_run_id, strip_ansi
from langsmith_cli.commands.runs import _fetch_openrouter_pricing
from langsmith_cli.main import cli


def _make_llm_run(
    n: int,
    model: str = "gpt-4o",
    total_tokens: int = 1000,
    total_cost: Decimal | None = None,
) -> Run:
    """Create an LLM run for pricing tests."""
    return Run(
        id=UUID(make_run_id(n)),
        name="ChatOpenAI",
        run_type="llm",
        start_time=datetime(2026, 3, 9, 16, 0, 0, tzinfo=timezone.utc),
        total_tokens=total_tokens,
        prompt_tokens=int(total_tokens * 0.7),
        completion_tokens=int(total_tokens * 0.3),
        total_cost=total_cost,
        extra={"metadata": {"ls_model_name": model}},
    )


def _extract_json(output: str) -> dict:
    """Extract JSON from potentially mixed stdout/stderr output."""
    for line in output.strip().splitlines():
        line = line.strip()
        if line.startswith("{") or line.startswith("["):
            return json.loads(line)
    raise ValueError(f"No JSON found in output: {output[:200]}")


class TestPricingCommand:
    """Tests for runs pricing command."""

    def test_pricing_from_cache_shows_missing(self, runner, tmp_path, monkeypatch):
        """Pricing command identifies models without cost data."""
        monkeypatch.setattr("langsmith_cli.cache.get_cache_dir", lambda: tmp_path)
        from langsmith_cli.cache import append_runs_to_cache

        runs = [
            _make_llm_run(1, model="gpt-4o", total_cost=Decimal("0.005")),
            _make_llm_run(2, model="llama-70b", total_tokens=5000, total_cost=None),
            _make_llm_run(3, model="llama-70b", total_tokens=3000, total_cost=None),
        ]
        append_runs_to_cache("test-proj", runs)

        with patch("langsmith.Client"):
            result = runner.invoke(
                cli,
                [
                    "runs",
                    "pricing",
                    "--project",
                    "test-proj",
                    "--from-cache",
                    "--no-lookup",
                ],
            )

        assert result.exit_code == 0
        output = strip_ansi(result.output)
        assert "gpt-4o" in output
        assert "llama-70b" in output
        assert "MISSING" in output
        assert "OK" in output

    def test_pricing_json_output(self, runner, tmp_path, monkeypatch):
        """JSON output includes has_pricing flag for each model."""
        monkeypatch.setattr("langsmith_cli.cache.get_cache_dir", lambda: tmp_path)
        from langsmith_cli.cache import append_runs_to_cache

        runs = [
            _make_llm_run(1, model="gpt-4o", total_cost=Decimal("0.005")),
            _make_llm_run(2, model="custom-model", total_tokens=2000, total_cost=None),
        ]
        append_runs_to_cache("test-proj", runs)

        with patch("langsmith.Client"):
            result = runner.invoke(
                cli,
                [
                    "--json",
                    "runs",
                    "pricing",
                    "--project",
                    "test-proj",
                    "--from-cache",
                    "--no-lookup",
                ],
            )

        assert result.exit_code == 0
        data = _extract_json(result.output)
        models = {m["model"]: m for m in data["models"]}
        assert models["gpt-4o"]["has_pricing"] is True
        assert models["custom-model"]["has_pricing"] is False

    def test_pricing_zero_tokens_treated_as_ok(self, runner, tmp_path, monkeypatch):
        """Models with 0 tokens and $0 cost should be marked as OK."""
        monkeypatch.setattr("langsmith_cli.cache.get_cache_dir", lambda: tmp_path)
        from langsmith_cli.cache import append_runs_to_cache

        runs = [
            _make_llm_run(1, model="zero-token-model", total_tokens=0, total_cost=None),
        ]
        append_runs_to_cache("test-proj", runs)

        with patch("langsmith.Client"):
            result = runner.invoke(
                cli,
                [
                    "--json",
                    "runs",
                    "pricing",
                    "--project",
                    "test-proj",
                    "--from-cache",
                    "--no-lookup",
                ],
            )

        assert result.exit_code == 0
        data = _extract_json(result.output)
        models = {m["model"]: m for m in data["models"]}
        assert models["zero-token-model"]["has_pricing"] is True

    def test_pricing_no_runs(self, runner, tmp_path, monkeypatch):
        """Pricing command handles empty cache gracefully."""
        monkeypatch.setattr("langsmith_cli.cache.get_cache_dir", lambda: tmp_path)
        from langsmith_cli.cache import append_runs_to_cache

        # Only chain runs, no LLM runs
        chain_run = create_run(name="chain", run_type="chain")
        append_runs_to_cache("test-proj", [chain_run])

        with patch("langsmith.Client"):
            result = runner.invoke(
                cli,
                [
                    "runs",
                    "pricing",
                    "--project",
                    "test-proj",
                    "--from-cache",
                    "--no-lookup",
                ],
            )

        assert result.exit_code == 0
        assert "No LLM runs" in result.output

    def test_pricing_all_models_have_pricing(self, runner, tmp_path, monkeypatch):
        """When all models have pricing, no 'MISSING' should appear."""
        monkeypatch.setattr("langsmith_cli.cache.get_cache_dir", lambda: tmp_path)
        from langsmith_cli.cache import append_runs_to_cache

        runs = [
            _make_llm_run(1, model="gpt-4o", total_cost=Decimal("0.005")),
            _make_llm_run(2, model="gpt-5", total_cost=Decimal("0.01")),
        ]
        append_runs_to_cache("test-proj", runs)

        with patch("langsmith.Client"):
            result = runner.invoke(
                cli,
                [
                    "runs",
                    "pricing",
                    "--project",
                    "test-proj",
                    "--from-cache",
                    "--no-lookup",
                ],
            )

        assert result.exit_code == 0
        output = strip_ansi(result.output)
        assert "MISSING" not in output
        assert "To add missing pricing" not in output


class TestFetchOpenRouterPricing:
    """Tests for the OpenRouter pricing lookup function."""

    def test_successful_lookup(self):
        """OpenRouter lookup maps model names to pricing."""
        mock_response = json.dumps(
            {
                "data": [
                    {
                        "id": "openai/gpt-oss-120b",
                        "pricing": {
                            "prompt": "0.000000039",
                            "completion": "0.00000019",
                        },
                    },
                    {
                        "id": "meta-llama/llama-3.3-70b-instruct",
                        "pricing": {"prompt": "0.0000001", "completion": "0.00000032"},
                    },
                ]
            }
        ).encode()

        mock_resp = MagicMock()
        mock_resp.read.return_value = mock_response
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        logger = MagicMock()

        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = _fetch_openrouter_pricing(
                ["gpt-oss-120b", "llama-3.3-70b-versatile"], logger
            )

        assert "gpt-oss-120b" in result
        assert result["gpt-oss-120b"]["openrouter_id"] == "openai/gpt-oss-120b"
        assert result["gpt-oss-120b"]["input_per_million"] == pytest.approx(
            0.039, rel=0.01
        )

        # llama-3.3-70b-versatile should match meta-llama/llama-3.3-70b-instruct
        assert "llama-3.3-70b-versatile" in result

    def test_api_failure_returns_empty(self):
        """OpenRouter lookup returns empty dict on API failure."""
        logger = MagicMock()

        with patch("urllib.request.urlopen", side_effect=TimeoutError("timeout")):
            result = _fetch_openrouter_pricing(["some-model"], logger)

        assert result == {}
        logger.warning.assert_called_once()

    def test_unmatched_model_not_in_result(self):
        """Models not found on OpenRouter are excluded from results."""
        mock_response = json.dumps({"data": []}).encode()
        mock_resp = MagicMock()
        mock_resp.read.return_value = mock_response
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        logger = MagicMock()

        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = _fetch_openrouter_pricing(["totally-unknown-model"], logger)

        assert "totally-unknown-model" not in result


class TestPricingTagFiltering:
    """Tests for --tag filtering on runs pricing command."""

    def test_tag_filter_adds_fql_for_api(self, runner, mock_client):
        """INVARIANT: --tag adds has(tags, ...) FQL filter for API calls."""
        mock_client.list_runs.return_value = [_make_llm_run(1)]

        runner.invoke(
            cli,
            [
                "--json",
                "runs",
                "pricing",
                "--project",
                "test-proj",
                "--last",
                "7d",
                "--tag",
                "env:prod",
                "--no-lookup",
            ],
        )

        call_kwargs = mock_client.list_runs.call_args[1]
        assert 'has(tags, "env:prod")' in call_kwargs["filter"]

    def test_tag_filter_client_side_from_cache(
        self, runner, mock_client, tmp_path, monkeypatch
    ):
        """INVARIANT: --tag filters cached runs client-side."""
        monkeypatch.setattr("langsmith_cli.cache.get_cache_dir", lambda: tmp_path)
        from langsmith_cli.cache import append_runs_to_cache

        prod_run = Run(
            id=UUID(make_run_id(1)),
            name="ChatOpenAI",
            run_type="llm",
            start_time=datetime(2026, 3, 9, 16, 0, 0, tzinfo=timezone.utc),
            total_tokens=1000,
            total_cost=Decimal("0.005"),
            extra={"metadata": {"ls_model_name": "gpt-4o"}},
            tags=["env:prod"],
        )
        staging_run = Run(
            id=UUID(make_run_id(2)),
            name="ChatOpenAI",
            run_type="llm",
            start_time=datetime(2026, 3, 9, 16, 0, 0, tzinfo=timezone.utc),
            total_tokens=2000,
            total_cost=Decimal("0.010"),
            extra={"metadata": {"ls_model_name": "gpt-4o"}},
            tags=["env:staging"],
        )
        append_runs_to_cache("test-proj", [prod_run, staging_run])

        result = runner.invoke(
            cli,
            [
                "--json",
                "runs",
                "pricing",
                "--project",
                "test-proj",
                "--from-cache",
                "--tag",
                "env:prod",
                "--no-lookup",
            ],
        )
        assert result.exit_code == 0
        data = _extract_json(result.output)
        # Only the prod run should be counted (1 run, not 2)
        models = data["models"]
        assert len(models) == 1
        assert models[0]["runs"] == 1
        assert models[0]["total_tokens"] == 1000


class TestPricingEmptyJsonOutput:
    """Tests for pricing command JSON output when no data matches."""

    def test_json_output_has_models_when_no_llm_runs(
        self, runner, tmp_path, monkeypatch
    ):
        """INVARIANT: --json always outputs parseable JSON with 'models' key, even when empty."""
        monkeypatch.setattr("langsmith_cli.cache.get_cache_dir", lambda: tmp_path)
        from langsmith_cli.cache import append_runs_to_cache

        chain_run = create_run(name="chain", run_type="chain")
        append_runs_to_cache("test-proj", [chain_run])

        with patch("langsmith.Client"):
            result = runner.invoke(
                cli,
                [
                    "--json",
                    "runs",
                    "pricing",
                    "--project",
                    "test-proj",
                    "--from-cache",
                    "--no-lookup",
                ],
            )

        assert result.exit_code == 0
        data = _extract_json(result.output)
        assert "models" in data
        assert data["models"] == []


class TestGetModelName:
    """Tests for _get_model_name helper."""

    def test_extracts_from_ls_model_name(self):
        """INVARIANT: ls_model_name in metadata is the primary source."""
        from langsmith_cli.commands.runs.pricing_cmd import _get_model_name

        run = Run(
            id=UUID(make_run_id(1)),
            name="test",
            run_type="llm",
            start_time=datetime(2026, 3, 9, tzinfo=timezone.utc),
            extra={"metadata": {"ls_model_name": "gpt-4o"}},
        )
        assert _get_model_name(run) == "gpt-4o"

    def test_extracts_from_invocation_params_model(self):
        """INVARIANT: invocation_params.model is used when ls_model_name absent."""
        from langsmith_cli.commands.runs.pricing_cmd import _get_model_name

        run = Run(
            id=UUID(make_run_id(2)),
            name="test",
            run_type="llm",
            start_time=datetime(2026, 3, 9, tzinfo=timezone.utc),
            extra={"invocation_params": {"model": "claude-3-opus"}},
        )
        assert _get_model_name(run) == "claude-3-opus"

    def test_extracts_from_invocation_params_model_name(self):
        """INVARIANT: invocation_params.model_name is used as fallback."""
        from langsmith_cli.commands.runs.pricing_cmd import _get_model_name

        run = Run(
            id=UUID(make_run_id(3)),
            name="test",
            run_type="llm",
            start_time=datetime(2026, 3, 9, tzinfo=timezone.utc),
            extra={"invocation_params": {"model_name": "llama-3-70b"}},
        )
        assert _get_model_name(run) == "llama-3-70b"

    def test_returns_unknown_when_no_model_info(self):
        """INVARIANT: returns 'unknown' when no model info available."""
        from langsmith_cli.commands.runs.pricing_cmd import _get_model_name

        run = Run(
            id=UUID(make_run_id(4)),
            name="test",
            run_type="llm",
            start_time=datetime(2026, 3, 9, tzinfo=timezone.utc),
            extra={},
        )
        assert _get_model_name(run) == "unknown"


class TestPricingYamlOutput:
    """Tests for pricing --format yaml output."""

    def test_yaml_output_format(self, tmp_path, monkeypatch):
        """INVARIANT: --format yaml outputs valid YAML pricing data."""
        from click.testing import CliRunner as _Runner

        monkeypatch.setattr("langsmith_cli.cache.get_cache_dir", lambda: tmp_path)
        from langsmith_cli.cache import append_runs_to_cache

        runs = [
            _make_llm_run(1, model="gpt-4o", total_cost=Decimal("0.005")),
            _make_llm_run(
                2, model="gpt-4o", total_tokens=1000, total_cost=Decimal("0.003")
            ),
        ]
        append_runs_to_cache("test-proj", runs)

        isolated_runner = _Runner(env={"COLUMNS": "160"})
        with patch("langsmith.Client"):
            result = isolated_runner.invoke(
                cli,
                [
                    "runs",
                    "pricing",
                    "--project",
                    "test-proj",
                    "--from-cache",
                    "--no-lookup",
                    "--format",
                    "yaml",
                ],
            )

        assert result.exit_code == 0
        import yaml

        data = yaml.safe_load(result.stdout)
        assert "gpt-4o" in data
        assert "input_per_million" in data["gpt-4o"]
        assert "output_per_million" in data["gpt-4o"]

    def test_yaml_output_missing_model_shows_zeros(self, tmp_path, monkeypatch):
        """INVARIANT: Models without pricing show 0.0 in YAML output."""
        from click.testing import CliRunner as _Runner

        monkeypatch.setattr("langsmith_cli.cache.get_cache_dir", lambda: tmp_path)
        from langsmith_cli.cache import append_runs_to_cache

        runs = [
            _make_llm_run(1, model="unknown-model", total_tokens=1000, total_cost=None)
        ]
        append_runs_to_cache("test-proj", runs)

        isolated_runner = _Runner(env={"COLUMNS": "160"})
        with patch("langsmith.Client"):
            result = isolated_runner.invoke(
                cli,
                [
                    "runs",
                    "pricing",
                    "--project",
                    "test-proj",
                    "--from-cache",
                    "--no-lookup",
                    "--format",
                    "yaml",
                ],
            )

        assert result.exit_code == 0
        import yaml

        data = yaml.safe_load(result.stdout)
        assert "unknown-model" in data
        assert data["unknown-model"]["input_per_million"] == 0.0
        assert data["unknown-model"]["output_per_million"] == 0.0


class TestPricingWithOpenRouterLookup:
    """Tests for pricing command with OpenRouter lookup enabled."""

    def test_json_output_includes_openrouter_pricing(
        self, runner, tmp_path, monkeypatch
    ):
        """INVARIANT: JSON output includes openrouter_pricing for matched models."""
        monkeypatch.setattr("langsmith_cli.cache.get_cache_dir", lambda: tmp_path)
        from langsmith_cli.cache import append_runs_to_cache

        runs = [_make_llm_run(1, model="gpt-4o", total_tokens=1000, total_cost=None)]
        append_runs_to_cache("test-proj", runs)

        mock_or_response = json.dumps(
            {
                "data": [
                    {
                        "id": "openai/gpt-4o",
                        "pricing": {"prompt": "0.0000025", "completion": "0.00001"},
                    }
                ]
            }
        ).encode()
        mock_resp = MagicMock()
        mock_resp.read.return_value = mock_or_response
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("langsmith.Client"):
            with patch("urllib.request.urlopen", return_value=mock_resp):
                result = runner.invoke(
                    cli,
                    [
                        "--json",
                        "runs",
                        "pricing",
                        "--project",
                        "test-proj",
                        "--from-cache",
                    ],
                )

        assert result.exit_code == 0
        data = _extract_json(result.output)
        models = {m["model"]: m for m in data["models"]}
        assert "gpt-4o" in models
        assert "openrouter_pricing" in models["gpt-4o"]
        assert models["gpt-4o"]["openrouter_pricing"][
            "input_per_million"
        ] == pytest.approx(2.5, rel=0.01)

    def test_table_output_shows_openrouter_price_table(
        self, runner, tmp_path, monkeypatch
    ):
        """INVARIANT: Table output shows OpenRouter pricing sub-table for missing models."""
        monkeypatch.setattr("langsmith_cli.cache.get_cache_dir", lambda: tmp_path)
        from langsmith_cli.cache import append_runs_to_cache

        runs = [
            _make_llm_run(1, model="llama-3-70b", total_tokens=2000, total_cost=None)
        ]
        append_runs_to_cache("test-proj", runs)

        mock_or_response = json.dumps(
            {
                "data": [
                    {
                        "id": "meta-llama/llama-3-70b-instruct",
                        "pricing": {"prompt": "0.0000001", "completion": "0.00000032"},
                    }
                ]
            }
        ).encode()
        mock_resp = MagicMock()
        mock_resp.read.return_value = mock_or_response
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("langsmith.Client"):
            with patch("urllib.request.urlopen", return_value=mock_resp):
                result = runner.invoke(
                    cli,
                    [
                        "runs",
                        "pricing",
                        "--project",
                        "test-proj",
                        "--from-cache",
                    ],
                )

        assert result.exit_code == 0
        output = strip_ansi(result.output)
        assert "OpenRouter" in output

    def test_api_fetch_exception_swallowed_silently(self, runner, mock_client):
        """INVARIANT: API errors for individual projects are swallowed (no crash)."""
        mock_client.list_runs.side_effect = Exception("network error")

        result = runner.invoke(
            cli,
            [
                "--json",
                "runs",
                "pricing",
                "--project",
                "test-proj",
                "--last",
                "7d",
                "--no-lookup",
            ],
        )

        assert result.exit_code == 0
        data = _extract_json(result.output)
        assert data["models"] == []
