# Standalone installer wrapper for langsmith-cli (Windows)
#
# This script downloads and runs the Python installer.
#
# Usage:
# irm https://raw.githubusercontent.com/langchain-ai/langsmith-cli/main/scripts/install.ps1 | iex
# iwr -useb https://raw.githubusercontent.com/langchain-ai/langsmith-cli/main/scripts/install.ps1 | iex

$ErrorActionPreference = "Stop"

# Configuration
$InstallerUrl = "https://raw.githubusercontent.com/langchain-ai/langsmith-cli/main/scripts/install.py"
$TempInstaller = "$env:TEMP\langsmith-cli-install.py"

function Write-Info {
    param([string]$Message)
    Write-Host "✓ $Message" -ForegroundColor Green
}

function Write-Error-Message {
    param([string]$Message)
    Write-Host "✗ $Message" -ForegroundColor Red
}

function Write-Warning-Message {
    param([string]$Message)
    Write-Host "⚠ $Message" -ForegroundColor Yellow
}

function Test-PythonInstalled {
    # Try python3 first
    try {
        $version = & python3 --version 2>&1
        if ($version -match "Python (\d+)\.(\d+)") {
            $major = [int]$Matches[1]
            $minor = [int]$Matches[2]
            if ($major -ge 3 -and $minor -ge 12) {
                return @{
                    Command = "python3"
                    Version = $version
                }
            }
        }
    }
    catch {
        # python3 not found, try python
    }

    # Try python
    try {
        $version = & python --version 2>&1
        if ($version -match "Python (\d+)\.(\d+)") {
            $major = [int]$Matches[1]
            $minor = [int]$Matches[2]
            if ($major -ge 3 -and $minor -ge 12) {
                return @{
                    Command = "python"
                    Version = $version
                }
            }
        }
    }
    catch {
        # python not found
    }

    return $null
}

# Main installation
try {
    Write-Host ""
    Write-Host "langsmith-cli Installer" -ForegroundColor White -BackgroundColor Black
    Write-Host ""

    # Check for Python
    $pythonInfo = Test-PythonInstalled
    if ($null -eq $pythonInfo) {
        Write-Error-Message "Python 3.12+ is required but not found."
        Write-Host ""
        Write-Host "Please install Python 3.12 or later from:"
        Write-Host "  - https://www.python.org/downloads/"
        Write-Host "  - Microsoft Store (search for 'Python')"
        Write-Host ""
        exit 1
    }

    Write-Info "Found $($pythonInfo.Version)"

    # Download installer
    Write-Info "Downloading installer..."

    try {
        Invoke-WebRequest -Uri $InstallerUrl -OutFile $TempInstaller -UseBasicParsing
    }
    catch {
        Write-Error-Message "Failed to download installer: $_"
        exit 1
    }

    if (-not (Test-Path $TempInstaller)) {
        Write-Error-Message "Failed to download installer"
        exit 1
    }

    Write-Info "Running installer..."
    Write-Host ""

    # Run installer
    $process = Start-Process -FilePath $pythonInfo.Command -ArgumentList $TempInstaller `
        -Wait -NoNewWindow -PassThru

    $exitCode = $process.ExitCode

    # Cleanup
    if (Test-Path $TempInstaller) {
        Remove-Item $TempInstaller -Force
    }

    exit $exitCode
}
catch {
    Write-Error-Message "Installation failed: $_"

    # Cleanup on error
    if (Test-Path $TempInstaller) {
        Remove-Item $TempInstaller -Force
    }

    exit 1
}
