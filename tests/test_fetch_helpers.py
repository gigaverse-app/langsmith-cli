"""Tests for fetch helpers in utils.py."""

from unittest.mock import MagicMock


from langsmith_cli.utils import FetchResult, fetch_from_projects


class TestFetchResult:
    """Tests for FetchResult Pydantic model."""

    def test_creation_with_no_failures(self):
        """Test creating FetchResult with successful fetches only."""
        result = FetchResult(
            items=[1, 2, 3],
            successful_sources=["proj1", "proj2"],
            failed_sources=[],
        )
        assert len(result.items) == 3
        assert len(result.successful_sources) == 2
        assert len(result.failed_sources) == 0
        assert result.has_failures is False

    def test_creation_with_failures(self):
        """Test creating FetchResult with failed fetches."""
        result = FetchResult(
            items=[1, 2],
            successful_sources=["proj1"],
            failed_sources=[("proj2", "Connection timeout")],
        )
        assert len(result.items) == 2
        assert len(result.successful_sources) == 1
        assert len(result.failed_sources) == 1
        assert result.has_failures is True

    def test_has_failures_property(self):
        """Test has_failures property."""
        # No failures
        result1 = FetchResult(
            items=[],
            successful_sources=[],
            failed_sources=[],
        )
        assert result1.has_failures is False

        # With failures
        result2 = FetchResult(
            items=[],
            successful_sources=[],
            failed_sources=[("proj1", "Error")],
        )
        assert result2.has_failures is True

    def test_report_failures_shows_nothing_when_no_failures(self):
        """Test report_failures doesn't print when there are no failures."""
        result = FetchResult(
            items=[1],
            successful_sources=["proj1"],
            failed_sources=[],
        )
        console = MagicMock()
        result.report_failures(console)
        console.print.assert_not_called()

    def test_report_failures_shows_failures(self):
        """Test report_failures prints failures to console."""
        result = FetchResult(
            items=[],
            successful_sources=[],
            failed_sources=[
                ("proj1", "Connection timeout"),
                ("proj2", "Authentication failed"),
            ],
        )
        console = MagicMock()
        result.report_failures(console)

        # Should have printed warning header + 2 failures
        assert console.print.call_count == 3
        calls = [str(call) for call in console.print.call_args_list]
        assert any("Warning" in call for call in calls)
        assert any("proj1" in call for call in calls)
        assert any("proj2" in call for call in calls)

    def test_report_failures_truncates_long_errors(self):
        """Test report_failures truncates long error messages."""
        long_error = "x" * 200
        result = FetchResult(
            items=[],
            successful_sources=[],
            failed_sources=[("proj1", long_error)],
        )
        console = MagicMock()
        result.report_failures(console)

        # Check that truncation occurred (100 chars + "...")
        calls = [str(call) for call in console.print.call_args_list]
        proj1_call = [call for call in calls if "proj1" in call][0]
        assert "..." in proj1_call

    def test_report_failures_limits_shown_count(self):
        """Test report_failures limits number of failures shown."""
        failures = [(f"proj{i}", "Error") for i in range(10)]
        result = FetchResult(
            items=[],
            successful_sources=[],
            failed_sources=failures,
        )
        console = MagicMock()
        result.report_failures(console, max_show=3)

        # Should show: warning header + 3 failures + "... and N more"
        assert console.print.call_count == 5
        calls = [str(call) for call in console.print.call_args_list]
        assert any("7 more" in call for call in calls)

    def test_default_empty_failed_sources(self):
        """Test that failed_sources defaults to empty list."""
        result = FetchResult(
            items=[1, 2],
            successful_sources=["proj1"],
        )
        assert result.failed_sources == []
        assert result.has_failures is False


class TestFetchFromProjects:
    """Tests for fetch_from_projects helper function."""

    def test_successful_fetch_from_single_project(self):
        """Test fetching from a single project successfully."""
        client = MagicMock()
        fetch_func = MagicMock(return_value=[1, 2, 3])

        result = fetch_from_projects(
            client,
            ["proj1"],
            fetch_func,
            limit=10,
        )

        assert len(result.items) == 3
        assert result.successful_sources == ["proj1"]
        assert result.failed_sources == []
        assert result.has_failures is False

        # Verify fetch_func was called correctly
        fetch_func.assert_called_once_with(client, "proj1", limit=10)

    def test_successful_fetch_from_multiple_projects(self):
        """Test fetching from multiple projects successfully."""
        client = MagicMock()

        def mock_fetch(c, proj, **kwargs):
            if proj == "proj1":
                return [1, 2]
            elif proj == "proj2":
                return [3, 4, 5]
            return []

        result = fetch_from_projects(
            client,
            ["proj1", "proj2"],
            mock_fetch,
            limit=10,
        )

        assert len(result.items) == 5
        assert result.items == [1, 2, 3, 4, 5]
        assert result.successful_sources == ["proj1", "proj2"]
        assert result.failed_sources == []

    def test_handles_one_project_failure(self):
        """Test that one project failure doesn't stop other fetches."""
        client = MagicMock()

        def mock_fetch(c, proj, **kwargs):
            if proj == "proj1":
                return [1, 2]
            elif proj == "proj2":
                raise ConnectionError("Network timeout")
            elif proj == "proj3":
                return [3, 4]
            return []

        console = MagicMock()
        result = fetch_from_projects(
            client,
            ["proj1", "proj2", "proj3"],
            mock_fetch,
            console=console,
            show_warnings=True,
        )

        # Should have items from proj1 and proj3
        assert len(result.items) == 4
        assert result.items == [1, 2, 3, 4]
        assert result.successful_sources == ["proj1", "proj3"]
        assert len(result.failed_sources) == 1
        assert result.failed_sources[0][0] == "proj2"
        assert "Network timeout" in result.failed_sources[0][1]

        # Should have printed warning
        console.print.assert_called()

    def test_handles_all_projects_failing(self):
        """Test handling when all projects fail."""
        client = MagicMock()

        def mock_fetch(c, proj, **kwargs):
            raise ValueError(f"Project {proj} not found")

        console = MagicMock()
        result = fetch_from_projects(
            client,
            ["proj1", "proj2"],
            mock_fetch,
            console=console,
        )

        assert len(result.items) == 0
        assert len(result.successful_sources) == 0
        assert len(result.failed_sources) == 2

    def test_passes_kwargs_to_fetch_func(self):
        """Test that additional kwargs are passed to fetch_func."""
        client = MagicMock()
        fetch_func = MagicMock(return_value=[])

        _ = fetch_from_projects(
            client,
            ["proj1"],
            fetch_func,
            limit=10,
            status="error",
            run_type="llm",
        )

        fetch_func.assert_called_once_with(
            client, "proj1", limit=10, status="error", run_type="llm"
        )

    def test_handles_iterator_return_from_fetch_func(self):
        """Test that iterators from fetch_func are converted to lists."""
        client = MagicMock()

        def mock_fetch(c, proj, **kwargs):
            # Return an iterator
            return iter([1, 2, 3])

        result = fetch_from_projects(
            client,
            ["proj1"],
            mock_fetch,
        )

        assert len(result.items) == 3
        assert isinstance(result.items, list)

    def test_show_warnings_false_doesnt_print(self):
        """Test that show_warnings=False suppresses console output."""
        client = MagicMock()

        def mock_fetch(c, proj, **kwargs):
            if proj == "proj1":
                return [1]
            raise ValueError("Error")

        console = MagicMock()
        result = fetch_from_projects(
            client,
            ["proj1", "proj2"],
            mock_fetch,
            console=console,
            show_warnings=False,
        )

        # Should have failures but no console output
        assert result.has_failures is True
        console.print.assert_not_called()

    def test_no_console_provided_doesnt_crash(self):
        """Test that missing console doesn't cause errors."""
        client = MagicMock()

        def mock_fetch(c, proj, **kwargs):
            if proj == "proj1":
                return [1]
            raise ValueError("Error")

        result = fetch_from_projects(
            client,
            ["proj1", "proj2"],
            mock_fetch,
            console=None,
            show_warnings=True,
        )

        # Should complete without error even though console is None
        assert result.has_failures is True

    def test_empty_project_list(self):
        """Test handling empty project list."""
        client = MagicMock()
        fetch_func = MagicMock()

        result = fetch_from_projects(
            client,
            [],
            fetch_func,
        )

        assert len(result.items) == 0
        assert len(result.successful_sources) == 0
        assert len(result.failed_sources) == 0
        fetch_func.assert_not_called()

    def test_typical_langsmith_usage_pattern(self):
        """Test typical usage pattern with LangSmith client.list_runs."""
        client = MagicMock()

        # Simulate client.list_runs returning an iterator of runs
        def mock_list_runs(project_name, **kwargs):
            # Return mock Run objects
            run1 = MagicMock()
            run1.id = "run1"
            run1.name = "ChatOpenAI"
            run2 = MagicMock()
            run2.id = "run2"
            run2.name = "LLMChain"
            return iter([run1, run2])

        # Usage pattern from runs.py
        _ = fetch_from_projects(
            client,
            ["default", "prod"],
            lambda c, proj, **kw: c.list_runs(project_name=proj, **kw),
            limit=50,
            status="error",
        )

        # Verify client.list_runs was called correctly
        assert client.list_runs.call_count == 2
        calls = client.list_runs.call_args_list
        assert calls[0][1]["project_name"] == "default"
        assert calls[0][1]["limit"] == 50
        assert calls[0][1]["status"] == "error"
