"""Tests for runs tags and metadata-keys discovery commands."""

import json

from conftest import create_run
from langsmith_cli.main import cli


class TestRunsTagsDiscovery:
    """Tests for runs tags command."""

    def test_tags_discovery(self, runner, mock_client):
        """Tags command discovers tag patterns."""
        mock_client.list_runs.return_value = [
            create_run("run1", id_str="auto", tags=["length:short", "env:prod"]),
            create_run("run2", id_str="auto", tags=["length:medium", "env:dev"]),
            create_run(
                "run3", id_str="auto", tags=["length:long", "env:prod", "schema:v2"]
            ),
        ]

        result = runner.invoke(cli, ["--json", "runs", "tags"])

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "tag_patterns" in data
        patterns = data["tag_patterns"]

        assert "length" in patterns
        assert set(patterns["length"]) == {"short", "medium", "long"}
        assert "env" in patterns
        assert set(patterns["env"]) == {"prod", "dev"}
        assert "schema" in patterns
        assert patterns["schema"] == ["v2"]

    def test_tags_discovery_table_output(self, runner, mock_client):
        """Tags command table output."""
        mock_client.list_runs.return_value = [
            create_run("run1", tags=["length:short", "env:prod"])
        ]

        result = runner.invoke(cli, ["runs", "tags"])

        assert result.exit_code == 0
        assert "Tag Patterns" in result.output
        assert "length" in result.output
        assert "short" in result.output

    def test_tags_discovery_no_tags(self, runner, mock_client):
        """Tags command with no structured tags."""
        mock_client.list_runs.return_value = [
            create_run("run1", tags=["unstructured-tag"])
        ]

        result = runner.invoke(cli, ["runs", "tags"])

        assert result.exit_code == 0
        assert "No structured tags found" in result.output

    def test_tags_discovery_sample_size(self, runner, mock_client):
        """Tags command respects --sample-size option."""
        mock_client.list_runs.return_value = []

        runner.invoke(cli, ["runs", "tags", "--sample-size", "5000"])

        _, kwargs = mock_client.list_runs.call_args
        assert kwargs["limit"] == 5000


class TestRunsMetadataKeysDiscovery:
    """Tests for runs metadata-keys command."""

    def test_metadata_keys_discovery(self, runner, mock_client):
        """Metadata-keys command discovers keys."""
        mock_client.list_runs.return_value = [
            create_run(
                "run1",
                id_str="auto",
                metadata={"user_tier": "premium", "region": "us-east"},
            ),
            create_run(
                "run2",
                id_str="auto",
                metadata={"user_tier": "free", "channel_id": "abc123"},
            ),
            create_run(
                "run3", id_str="auto", extra={"metadata": {"session_id": "xyz789"}}
            ),
        ]

        result = runner.invoke(cli, ["--json", "runs", "metadata-keys"])

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "metadata_keys" in data
        keys = set(data["metadata_keys"])
        assert "user_tier" in keys
        assert "region" in keys
        assert "channel_id" in keys
        assert "session_id" in keys

    def test_metadata_keys_discovery_table_output(self, runner, mock_client):
        """Metadata-keys command table output."""
        mock_client.list_runs.return_value = [
            create_run("run1", metadata={"user_tier": "premium"})
        ]

        result = runner.invoke(cli, ["runs", "metadata-keys"])

        assert result.exit_code == 0
        assert "Metadata Keys" in result.output
        assert "user_tier" in result.output

    def test_metadata_keys_discovery_no_metadata(self, runner, mock_client):
        """Metadata-keys command with no metadata."""
        mock_client.list_runs.return_value = [create_run("run1")]

        result = runner.invoke(cli, ["runs", "metadata-keys"])

        assert result.exit_code == 0
        assert "No metadata keys found" in result.output
