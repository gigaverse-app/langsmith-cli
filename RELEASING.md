# Release Process

This project uses **explicit versioning** where versions are tracked in multiple files and synchronized during release.

## Quick Release

To create a new release, use the release script:

```bash
./scripts/release.sh [patch|minor|major|VERSION]
```

Examples:
```bash
./scripts/release.sh          # Bump patch version (0.2.1 -> 0.2.2)
./scripts/release.sh minor    # Bump minor version (0.2.1 -> 0.3.0)
./scripts/release.sh major    # Bump major version (0.2.1 -> 1.0.0)
./scripts/release.sh 0.3.5    # Create specific version v0.3.5
```

## What Happens

The release script:

1. **Validates** your working directory is clean
2. **Checks** you're on the main branch
3. **Pulls** latest changes
4. **Installs** dependencies with `uv sync`
5. **Runs linters** (ruff check, ruff format)
6. **Runs type checks** (pyright)
7. **Runs tests** (pytest)
8. **Calculates** the new version based on your input
9. **Prompts** for confirmation
10. **Updates versions** in all files:
    - `pyproject.toml`
    - `.claude-plugin/plugin.json`
    - `.claude-plugin/marketplace.json`
11. **Updates lockfile** with `uv lock`
12. **Commits** version changes
13. **Creates and pushes** a git tag (e.g., `v0.2.2`)
14. **Pushes** commit and tag to remote

## CI/CD Automation

Once you push the tag, GitHub Actions automatically:

1. **Builds** the package
2. **Publishes** to PyPI using stored credentials
3. **Creates** a GitHub release with changelog

Monitor the release workflow at: https://github.com/gigaverse-app/langsmith-cli/actions

## Version File Locations

The release script updates versions in:

1. **pyproject.toml**: Project version for PyPI
   ```toml
   version = "0.2.1"
   ```

2. **.claude-plugin/plugin.json**: Claude plugin version
   ```json
   "version": "0.2.1"
   ```

3. **.claude-plugin/marketplace.json**: Marketplace metadata (2 locations)
   ```json
   "metadata": {
     "version": "0.2.1"
   },
   "plugins": [{
     "version": "0.2.1"
   }]
   ```

4. **uv.lock**: Automatically updated to reflect new version

## Manual Release (Advanced)

If you need to release manually without the script:

```bash
# 1. Update all version files
sed -i 's/version = "0.2.1"/version = "0.2.2"/' pyproject.toml
sed -i 's/"version": "0.2.1"/"version": "0.2.2"/' .claude-plugin/plugin.json
# Update both occurrences in marketplace.json
sed -i '0,/"version": "0.2.1"/s//"version": "0.2.2"/' .claude-plugin/marketplace.json
sed -i '0,/"version": "0.2.1"/! s/"version": "0.2.1"/"version": "0.2.2"/' .claude-plugin/marketplace.json

# 2. Update lockfile
uv lock

# 3. Commit changes
git add pyproject.toml .claude-plugin/plugin.json .claude-plugin/marketplace.json uv.lock
git commit -m "chore: Bump version to 0.2.2"

# 4. Create and push tag
git tag -a v0.2.2 -m "Release v0.2.2"
git push origin main
git push origin v0.2.2

# 5. Wait for CI/CD to complete
# Monitor at: https://github.com/gigaverse-app/langsmith-cli/actions
```

## Troubleshooting

### Version Mismatch Across Files

If versions are out of sync, manually verify all locations:

```bash
grep '^version = ' pyproject.toml
grep '"version":' .claude-plugin/plugin.json
grep '"version":' .claude-plugin/marketplace.json
```

All should show the same version.

### CI/CD Fails to Publish

- Verify `PYPI_API_TOKEN` secret is set in GitHub repository settings
- Check the workflow logs for specific errors
- Ensure the tag follows semantic versioning (v0.2.2, not 0.2.2)

### Working Directory Not Clean

If the script fails with "working directory is not clean":

```bash
git status                  # Check what changed
git stash                   # Stash uncommitted changes
./scripts/release.sh patch  # Run release
git stash pop              # Restore changes after release
```

### Wrong Branch

If you're not on main:

```bash
git checkout main
git pull
./scripts/release.sh patch
```

## Release Checklist

Before releasing, ensure:

- [ ] All tests pass locally (`uv run pytest`)
- [ ] Linters pass (`uv run ruff check .`)
- [ ] Type checks pass (`uv run pyright`)
- [ ] Working directory is clean (`git status`)
- [ ] On main branch (`git branch --show-current`)
- [ ] Latest changes pulled (`git pull`)
- [ ] CHANGELOG.md updated with notable changes (if applicable)

## Version Bump Guidelines

Use these rules for semantic versioning:

- **Patch** (0.2.1 → 0.2.2): Bug fixes, documentation, minor improvements
- **Minor** (0.2.1 → 0.3.0): New features, backward-compatible changes
- **Major** (0.2.1 → 1.0.0): Breaking changes, API redesigns

Examples:
- Add `--no-truncate` flag → **patch**
- Add new `runs export` command → **minor**
- Change JSON output format → **major**
