#!/bin/sh
# Standalone installer wrapper for langsmith-cli (Unix systems)
#
# This script downloads and runs the Python installer.
#
# Usage:
# curl -sSL https://raw.githubusercontent.com/langchain-ai/langsmith-cli/main/scripts/install.sh | sh
# wget -qO- https://raw.githubusercontent.com/langchain-ai/langsmith-cli/main/scripts/install.sh | sh

set -e

# Colors
BOLD="\033[1m"
GREEN="\033[32m"
YELLOW="\033[33m"
RED="\033[31m"
RESET="\033[0m"

# Configuration
INSTALLER_URL="https://raw.githubusercontent.com/langchain-ai/langsmith-cli/main/scripts/install.py"
TEMP_INSTALLER="/tmp/langsmith-cli-install.py"

log_info() {
    printf "${GREEN}✓${RESET} %s\n" "$1"
}

log_error() {
    printf "${RED}✗${RESET} %s\n" "$1" >&2
}

log_warning() {
    printf "${YELLOW}⚠${RESET} %s\n" "$1" >&2
}

# Check if Python 3 is available
check_python() {
    if command -v python3 >/dev/null 2>&1; then
        PYTHON_CMD="python3"
    elif command -v python >/dev/null 2>&1; then
        # Check if it's Python 3
        if python -c "import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)" 2>/dev/null; then
            PYTHON_CMD="python"
        else
            return 1
        fi
    else
        return 1
    fi
    return 0
}

# Main installation
main() {
    printf "\n${BOLD}langsmith-cli Installer${RESET}\n\n"

    # Check for Python
    if ! check_python; then
        log_error "Python 3.12+ is required but not found."
        printf "\nPlease install Python 3.12 or later from:\n"
        printf "  - https://www.python.org/downloads/\n"
        printf "  - Your system package manager (apt, brew, etc.)\n\n"
        exit 1
    fi

    PYTHON_VERSION=$($PYTHON_CMD --version 2>&1 | cut -d' ' -f2)
    log_info "Found Python $PYTHON_VERSION"

    # Download installer
    log_info "Downloading installer..."

    if command -v curl >/dev/null 2>&1; then
        curl -sSL "$INSTALLER_URL" -o "$TEMP_INSTALLER"
    elif command -v wget >/dev/null 2>&1; then
        wget -qO "$TEMP_INSTALLER" "$INSTALLER_URL"
    else
        log_error "Neither curl nor wget found. Please install one of them."
        exit 1
    fi

    if [ ! -f "$TEMP_INSTALLER" ]; then
        log_error "Failed to download installer"
        exit 1
    fi

    log_info "Running installer..."
    printf "\n"

    # Run installer
    $PYTHON_CMD "$TEMP_INSTALLER"
    EXIT_CODE=$?

    # Cleanup
    rm -f "$TEMP_INSTALLER"

    exit $EXIT_CODE
}

# Handle interrupts
trap 'printf "\n\nInstallation cancelled.\n"; rm -f "$TEMP_INSTALLER"; exit 130' INT TERM

# Run main
main "$@"
