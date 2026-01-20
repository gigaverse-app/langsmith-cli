"""Tests for runs sample command and stratification."""

import json


from conftest import create_run
from langsmith_cli.main import cli


class TestRunsSample:
    """Tests for runs sample command."""

    def test_sample_basic(self, runner, mock_client):
        """Sample command stratifies runs by tag."""
        short_run = create_run(
            name="short-run", id_str="auto", tags=["length_category:short"]
        )
        medium_run = create_run(
            name="medium-run", id_str="auto", tags=["length_category:medium"]
        )

        def list_runs_side_effect(*args, **kwargs):
            filter_str = kwargs.get("filter", "")
            if "length_category:short" in filter_str:
                return [short_run]
            elif "length_category:medium" in filter_str:
                return [medium_run]
            return []

        mock_client.list_runs.side_effect = list_runs_side_effect

        result = runner.invoke(
            cli,
            [
                "runs",
                "sample",
                "--stratify-by",
                "tag:length_category",
                "--values",
                "short,medium",
                "--samples-per-stratum",
                "1",
            ],
        )

        assert result.exit_code == 0
        lines = result.output.strip().split("\n")
        assert len(lines) == 2
        samples = [json.loads(line) for line in lines]
        assert all("stratum" in s for s in samples)

    def test_sample_with_output_file(self, runner, mock_client, tmp_path):
        """Sample writes to output file."""
        mock_client.list_runs.return_value = [
            create_run(name="test-run", tags=["category:test"])
        ]
        output_file = tmp_path / "sample.jsonl"

        result = runner.invoke(
            cli,
            [
                "runs",
                "sample",
                "--stratify-by",
                "tag:category",
                "--values",
                "test",
                "--samples-per-stratum",
                "1",
                "--output",
                str(output_file),
            ],
        )

        assert result.exit_code == 0
        assert output_file.exists()
        with open(output_file) as f:
            data = json.loads(f.readline())
            assert data["stratum"] == "category:test"

    def test_sample_with_fields_pruning(self, runner, mock_client):
        """Sample with --fields prunes output."""
        mock_client.list_runs.return_value = [
            create_run(
                name="test-run",
                tags=["category:test"],
                inputs={"query": "test"},
                outputs={"result": "ok"},
                extra={"large_field": "huge_data"},
            )
        ]

        result = runner.invoke(
            cli,
            [
                "runs",
                "sample",
                "--stratify-by",
                "tag:category",
                "--values",
                "test",
                "--samples-per-stratum",
                "1",
                "--fields",
                "id,name,stratum",
            ],
        )

        assert result.exit_code == 0
        data = json.loads(result.output.strip())
        assert "id" in data
        assert "name" in data
        assert "stratum" in data
        assert "inputs" not in data
        assert "huge_data" not in result.output

    def test_sample_invalid_stratify_by(self, runner):
        """Invalid stratify-by format produces error."""
        result = runner.invoke(
            cli, ["runs", "sample", "--stratify-by", "invalid", "--values", "a,b"]
        )

        assert result.exit_code != 0
        assert "Invalid grouping format" in result.output


class TestRunsSampleMultiDimensional:
    """Tests for multi-dimensional stratification."""

    def test_multi_dimensional_cartesian_product(self, runner, mock_client):
        """Multi-dimensional sampling with Cartesian product."""

        def mock_list_runs(project_name, limit, filter, order_by):
            if "length:short" in filter and "content_type:news" in filter:
                return [
                    create_run("short-news", tags=["length:short", "content_type:news"])
                ]
            elif "length:long" in filter and "content_type:gaming" in filter:
                return [
                    create_run(
                        "long-gaming", tags=["length:long", "content_type:gaming"]
                    )
                ]
            return []

        mock_client.list_runs.side_effect = mock_list_runs

        result = runner.invoke(
            cli,
            [
                "--json",
                "runs",
                "sample",
                "--stratify-by",
                "tag:length,tag:content_type",
                "--dimension-values",
                "short|long,news|gaming",
                "--samples-per-combination",
                "1",
            ],
        )

        assert result.exit_code == 0
        lines = result.output.strip().split("\n")
        samples = [json.loads(line) for line in lines]
        strata = {s["stratum"] for s in samples}
        assert any("length:short" in s and "content_type:news" in s for s in strata)

    def test_multi_dimensional_manual_combinations(self, runner, mock_client):
        """Multi-dimensional sampling with manual combinations."""
        mock_client.list_runs.return_value = [
            create_run("run1", tags=["length:short", "content_type:news"])
        ]

        result = runner.invoke(
            cli,
            [
                "--json",
                "runs",
                "sample",
                "--stratify-by",
                "tag:length,tag:content_type",
                "--values",
                "short:news,medium:gaming",
                "--samples-per-stratum",
                "1",
            ],
        )

        assert result.exit_code == 0
        assert mock_client.list_runs.call_count >= 2

    def test_multi_dimensional_validation(self, runner):
        """Missing values parameter produces error."""
        result = runner.invoke(
            cli,
            [
                "runs",
                "sample",
                "--stratify-by",
                "tag:length,tag:content_type",
                "--samples-per-stratum",
                "1",
            ],
        )

        assert result.exit_code != 0
        assert "requires --values or --dimension-values" in result.output
