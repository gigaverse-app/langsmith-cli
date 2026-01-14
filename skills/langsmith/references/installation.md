# Installation Guide

## Prerequisites

- Python 3.12 or later
- LangSmith API key (get one at [smith.langchain.com](https://smith.langchain.com))

## Installation Methods

### Quick Install (Recommended for end users)

**Linux/macOS:**
```bash
curl -sSL https://raw.githubusercontent.com/langchain-ai/langsmith-cli/main/scripts/install.sh | sh
```

**Windows:**
```powershell
iwr -useb https://raw.githubusercontent.com/langchain-ai/langsmith-cli/main/scripts/install.ps1 | iex
```

The standalone installer:
- Creates an isolated environment (no package conflicts)
- Automatically adds `langsmith-cli` to your PATH
- Works without manually managing Python packages
- Can be uninstalled cleanly

### Using `uv` (Recommended for developers)

```bash
uv tool install langsmith-cli
```

**Advantages:**
- Fast installation
- Automatic virtual environment management
- Easy updates: `uv tool upgrade langsmith-cli`
- Easy removal: `uv tool uninstall langsmith-cli`

### Using `pip`

```bash
pip install langsmith-cli
```

**Or with pipx for isolated installation:**
```bash
pipx install langsmith-cli
```

### From Source (for contributors)

```bash
git clone https://github.com/langchain-ai/langsmith-cli.git
cd langsmith-cli
uv sync
uv run langsmith-cli --help
```

## Authentication

After installation, authenticate with LangSmith:

```bash
langsmith-cli auth login
```

This creates a `.env` file with your `LANGSMITH_API_KEY`.

**Or manually create `.env`:**
```bash
echo "LANGSMITH_API_KEY=lsv2_pt_..." > .env
```

## Verification

Verify installation:

```bash
langsmith-cli --version
langsmith-cli --help
langsmith-cli projects list
```

## Installing as a Claude Code Plugin

**Step 1:** Install the CLI using any method above

**Step 2:** Add the skill to Claude Code:
```bash
/plugin marketplace add gigaverse-app/langsmith-cli
```

This gives Claude instant access to all LangSmith commands.

## Uninstallation

### Standalone Installation

```bash
# Download and run uninstaller
curl -sSL https://raw.githubusercontent.com/langchain-ai/langsmith-cli/main/scripts/uninstall.py | python3 -

# Or manually
python3 scripts/uninstall.py
```

The uninstaller removes:
- Virtual environment
- Wrapper scripts
- Configuration files
- PATH entries

### uv

```bash
uv tool uninstall langsmith-cli
```

### pip

```bash
pip uninstall langsmith-cli
```

### pipx

```bash
pipx uninstall langsmith-cli
```

## Troubleshooting

### Python Version Issues

**Error:** "Python 3.12+ required"

**Solution:** Install Python 3.12 or later:
- **macOS:** `brew install python@3.12`
- **Ubuntu/Debian:** `sudo apt install python3.12`
- **Windows:** Download from [python.org](https://www.python.org/downloads/)

### Command Not Found

**Error:** `langsmith-cli: command not found`

**Solutions:**
1. **Reload shell profile:**
   ```bash
   source ~/.bashrc  # or ~/.zshrc
   ```

2. **Check PATH:**
   ```bash
   echo $PATH | grep -o ".local/bin"
   ```

   If missing, add to your shell profile:
   ```bash
   echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
   source ~/.bashrc
   ```

3. **Verify installation location:**
   ```bash
   which langsmith-cli
   # Should show: /home/user/.local/bin/langsmith-cli (or similar)
   ```

### Permission Errors

**Error:** Permission denied during installation

**Solution:** Don't use `sudo`. Install in user space:
```bash
pip install --user langsmith-cli
# or
uv tool install langsmith-cli  # Already installs in user space
```

### Import Errors

**Error:** `ModuleNotFoundError: No module named 'langsmith_cli'`

**Solution:** This usually means:
1. Wrong Python environment is active
2. Installation failed silently

**Fix:**
```bash
# Reinstall with verbose output
pip install --user --force-reinstall langsmith-cli
```

### API Key Issues

**Error:** `LangSmithAuthError: API key not found`

**Solutions:**
1. Run `langsmith-cli auth login`
2. Check `.env` file exists with valid key
3. Set environment variable:
   ```bash
   export LANGSMITH_API_KEY="lsv2_pt_..."
   ```

## Updating

### Standalone Installation

Reinstall with the install script (it removes the old version first):
```bash
curl -sSL https://raw.githubusercontent.com/langchain-ai/langsmith-cli/main/scripts/install.sh | sh
```

### uv

```bash
uv tool upgrade langsmith-cli
```

### pip

```bash
pip install --upgrade langsmith-cli
```

## Platform-Specific Notes

### macOS

- If using Homebrew Python, ensure it's in PATH: `/opt/homebrew/bin/python3`
- M1/M2 Macs work perfectly (arm64 supported)

### Windows

- Use PowerShell (not Command Prompt) for the installer
- Windows Terminal recommended for best experience
- PATH updates require terminal restart

### Linux

- Most distributions work out of the box
- WSL2 fully supported
- Some minimal distros may need: `apt install python3-venv`

### Docker/Containers

```dockerfile
FROM python:3.12-slim

# Install langsmith-cli
RUN pip install langsmith-cli

# Or use standalone installer
RUN curl -sSL https://raw.githubusercontent.com/langchain-ai/langsmith-cli/main/scripts/install.sh | sh

# Set API key
ENV LANGSMITH_API_KEY="lsv2_pt_..."
```

## Advanced Configuration

### Custom Installation Location

**Standalone installer respects environment variables:**
```bash
# Custom installation directory
export XDG_DATA_HOME="$HOME/.custom/share"
export XDG_CONFIG_HOME="$HOME/.custom/config"

# Run installer - will install to custom location
curl -sSL https://... | sh
```

### Using with Virtual Environments

```bash
# Create venv
python3 -m venv myproject-venv
source myproject-venv/bin/activate

# Install in venv
pip install langsmith-cli

# Use within venv
langsmith-cli --help
```

### CI/CD Installation

**GitHub Actions:**
```yaml
- name: Install langsmith-cli
  run: pip install langsmith-cli

- name: Test CLI
  env:
    LANGSMITH_API_KEY: ${{ secrets.LANGSMITH_API_KEY }}
  run: langsmith-cli runs list --limit 1
```

**GitLab CI:**
```yaml
test:
  image: python:3.12
  script:
    - pip install langsmith-cli
    - langsmith-cli --version
```

## Getting Help

- **GitHub Issues:** [github.com/langchain-ai/langsmith-cli/issues](https://github.com/langchain-ai/langsmith-cli/issues)
- **Documentation:** [github.com/langchain-ai/langsmith-cli](https://github.com/langchain-ai/langsmith-cli)
- **LangSmith Docs:** [docs.smith.langchain.com](https://docs.smith.langchain.com)
