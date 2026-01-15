"""Tests for the CLI logging infrastructure."""

import sys
from unittest.mock import patch

from rich.console import Console

from langsmith_cli.logging import CLILogger, Verbosity


class TestVerbosity:
    """Test the Verbosity enum."""

    def test_verbosity_levels_ordered_correctly(self):
        """Verify verbosity levels are ordered from most to least verbose."""
        assert (
            Verbosity.TRACE
            < Verbosity.DEBUG
            < Verbosity.INFO
            < Verbosity.WARNING
            < Verbosity.ERROR
        )

    def test_verbosity_numeric_values(self):
        """Verify verbosity levels match Python logging standard."""
        assert Verbosity.TRACE == 5
        assert Verbosity.DEBUG == 10
        assert Verbosity.INFO == 20
        assert Verbosity.WARNING == 30
        assert Verbosity.ERROR == 40


class TestCLILogger:
    """Test the CLILogger class."""

    def test_init_creates_consoles(self):
        """Test logger initializes both stdout and stderr consoles."""
        logger = CLILogger(Verbosity.INFO, use_stderr=True)
        assert isinstance(logger.stdout_console, Console)
        assert isinstance(logger.stderr_console, Console)
        assert logger.verbosity == Verbosity.INFO
        assert logger.use_stderr is True

    def test_diagnostic_console_uses_stderr_when_machine_readable(self):
        """Test diagnostic console uses stderr in machine-readable mode."""
        logger = CLILogger(Verbosity.INFO, use_stderr=True)
        assert logger.diagnostic_console == logger.stderr_console
        assert logger.diagnostic_console.file == sys.stderr

    def test_diagnostic_console_uses_stdout_when_human_readable(self):
        """Test diagnostic console uses stdout in human-readable mode."""
        logger = CLILogger(Verbosity.INFO, use_stderr=False)
        assert logger.diagnostic_console == logger.stdout_console
        assert logger.diagnostic_console.file == sys.stdout

    def test_error_level_only_shows_errors(self):
        """Test ERROR level (-qq) only shows error messages."""
        logger = CLILogger(Verbosity.ERROR, use_stderr=True)

        with patch.object(logger.diagnostic_console, "print") as mock_print:
            logger.trace("trace message")
            logger.debug("debug message")
            logger.info("info message")
            logger.warning("warning message")
            logger.error("error message")

            # Only error should be printed
            assert mock_print.call_count == 1
            mock_print.assert_called_once()
            assert "error message" in str(mock_print.call_args)

    def test_warning_level_shows_warnings_and_errors(self):
        """Test WARNING level (-q) shows warnings and errors but not info/debug/trace."""
        logger = CLILogger(Verbosity.WARNING, use_stderr=True)

        with patch.object(logger.diagnostic_console, "print") as mock_print:
            logger.trace("trace message")
            logger.debug("debug message")
            logger.info("info message")
            logger.warning("warning message")
            logger.error("error message")

            # Warning and error should be printed (2 calls)
            assert mock_print.call_count == 2
            calls = [str(call) for call in mock_print.call_args_list]
            assert any("warning message" in call for call in calls)
            assert any("error message" in call for call in calls)

    def test_info_level_shows_info_warnings_errors(self):
        """Test INFO level (default) shows info, warnings, and errors."""
        logger = CLILogger(Verbosity.INFO, use_stderr=True)

        with patch.object(logger.diagnostic_console, "print") as mock_print:
            logger.trace("trace message")
            logger.debug("debug message")
            logger.info("info message")
            logger.warning("warning message")
            logger.error("error message")
            logger.success("success message")

            # Info, warning, error, success should be printed (4 calls)
            assert mock_print.call_count == 4
            calls = [str(call) for call in mock_print.call_args_list]
            assert any("info message" in call for call in calls)
            assert any("warning message" in call for call in calls)
            assert any("error message" in call for call in calls)
            assert any("success message" in call for call in calls)

    def test_debug_level_shows_debug_and_above(self):
        """Test DEBUG level (-v) shows debug, info, warnings, and errors."""
        logger = CLILogger(Verbosity.DEBUG, use_stderr=True)

        with patch.object(logger.diagnostic_console, "print") as mock_print:
            logger.trace("trace message")
            logger.debug("debug message")
            logger.info("info message")
            logger.warning("warning message")
            logger.error("error message")

            # Debug, info, warning, error should be printed (4 calls)
            assert mock_print.call_count == 4
            calls = [str(call) for call in mock_print.call_args_list]
            assert any("debug message" in call for call in calls)
            assert any("info message" in call for call in calls)

    def test_trace_level_shows_everything(self):
        """Test TRACE level (-vv) shows all messages."""
        logger = CLILogger(Verbosity.TRACE, use_stderr=True)

        with patch.object(logger.diagnostic_console, "print") as mock_print:
            logger.trace("trace message")
            logger.debug("debug message")
            logger.info("info message")
            logger.warning("warning message")
            logger.error("error message")

            # All 5 messages should be printed
            assert mock_print.call_count == 5
            calls = [str(call) for call in mock_print.call_args_list]
            assert any("trace message" in call for call in calls)
            assert any("debug message" in call for call in calls)

    def test_data_outputs_to_stdout(self):
        """Test data() method outputs to stdout console."""
        logger = CLILogger(Verbosity.INFO, use_stderr=True)

        with patch.object(logger.stdout_console, "print") as mock_print:
            logger.data("test table", mode="table")
            mock_print.assert_called_once_with("test table")

    def test_data_does_nothing_for_non_table_mode(self):
        """Test data() method does nothing for non-table modes (JSON/CSV handled elsewhere)."""
        logger = CLILogger(Verbosity.INFO, use_stderr=True)

        with patch.object(logger.stdout_console, "print") as mock_print:
            logger.data("test data", mode="json")
            logger.data("test data", mode="csv")
            mock_print.assert_not_called()

    def test_use_stderr_can_be_updated_dynamically(self):
        """Test use_stderr can be changed after initialization."""
        logger = CLILogger(Verbosity.INFO, use_stderr=False)
        assert logger.diagnostic_console == logger.stdout_console

        # Update to use stderr
        logger.use_stderr = True
        assert logger.diagnostic_console == logger.stderr_console

    def test_trace_includes_trace_prefix(self):
        """Test trace messages include [TRACE] prefix."""
        logger = CLILogger(Verbosity.TRACE, use_stderr=True)

        with patch.object(logger.diagnostic_console, "print") as mock_print:
            logger.trace("test message")
            call_args = str(mock_print.call_args)
            assert "[TRACE]" in call_args

    def test_debug_includes_debug_prefix(self):
        """Test debug messages include [DEBUG] prefix."""
        logger = CLILogger(Verbosity.DEBUG, use_stderr=True)

        with patch.object(logger.diagnostic_console, "print") as mock_print:
            logger.debug("test message")
            call_args = str(mock_print.call_args)
            assert "[DEBUG]" in call_args

    def test_success_shown_at_info_level(self):
        """Test success messages are shown at INFO level and above."""
        logger_info = CLILogger(Verbosity.INFO, use_stderr=True)
        logger_warning = CLILogger(Verbosity.WARNING, use_stderr=True)

        with patch.object(logger_info.diagnostic_console, "print") as mock_print_info:
            logger_info.success("success message")
            assert mock_print_info.call_count == 1

        with patch.object(
            logger_warning.diagnostic_console, "print"
        ) as mock_print_warning:
            logger_warning.success("success message")
            assert (
                mock_print_warning.call_count == 0
            )  # Should not print at WARNING level
