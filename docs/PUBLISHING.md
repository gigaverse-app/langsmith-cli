# Publishing to PyPI

This document describes how to publish `langsmith-cli` to PyPI using GitHub Actions.

## Prerequisites

### 1. PyPI Account Setup

1. Create an account on [PyPI](https://pypi.org/) if you don't have one
2. Enable 2FA (required for publishing)

### 2. PyPI Trusted Publishing Setup

We use PyPI's [Trusted Publishing](https://docs.pypi.org/trusted-publishers/) feature, which eliminates the need for API tokens. This is more secure and easier to manage.

#### For First-Time Publishing:

1. Go to [PyPI](https://pypi.org/)
2. Navigate to "Publishing" → "Add a new pending publisher"
3. Fill in the details:
   - **PyPI Project Name**: `langsmith-cli`
   - **Owner**: `aviadr1` (your GitHub username)
   - **Repository name**: `langsmith-cli`
   - **Workflow name**: `publish.yml`
   - **Environment name**: `pypi`

4. Click "Add"

#### For Subsequent Publishing:

Once the package is published for the first time, trusted publishing is automatically configured. No additional setup is needed.

### 3. GitHub Repository Setup

The repository needs a PyPI environment configured:

1. Go to your GitHub repository settings
2. Navigate to "Environments"
3. Create a new environment named `pypi`
4. (Optional) Add protection rules:
   - Require reviewers before deployment
   - Limit to specific branches (e.g., `main`)

## Publishing Process

### Option 1: Automated Publishing (Recommended)

The easiest way to publish is to create a git tag:

```bash
# 1. Update version in pyproject.toml
# Edit pyproject.toml and update the version field:
# version = "0.2.0"

# 2. Commit the version bump
git add pyproject.toml
git commit -m "Bump version to 0.2.0"

# 3. Create and push a version tag
git tag v0.2.0
git push origin main
git push origin v0.2.0
```

This will automatically:
1. Run tests
2. Build the package
3. Publish to PyPI
4. Create a GitHub release with artifacts

### Option 2: Manual Trigger

You can also manually trigger the workflow from GitHub:

1. Go to your repository on GitHub
2. Click "Actions" → "Publish to PyPI"
3. Click "Run workflow"
4. Select the branch
5. Click "Run workflow"

## Versioning

We follow [Semantic Versioning](https://semver.org/):

- **Major version** (1.0.0): Incompatible API changes
- **Minor version** (0.1.0): New features, backwards compatible
- **Patch version** (0.0.1): Bug fixes, backwards compatible

### Version Bump Checklist

Before bumping the version:

- [ ] All tests pass locally: `uv run pytest`
- [ ] Linting passes: `uv run ruff check .`
- [ ] Type checking passes: `uv run pyright`
- [ ] CHANGELOG updated (if you maintain one)
- [ ] Documentation updated
- [ ] All changes committed to main branch

## Workflow Details

### CI Workflow (`.github/workflows/ci.yml`)

Runs on every push and PR to `main`:
- Tests on Python 3.12 and 3.13
- Linting with Ruff
- Type checking with Pyright
- Coverage reporting

### Publish Workflow (`.github/workflows/publish.yml`)

Triggered by version tags (e.g., `v0.1.0`):

**Jobs:**
1. **test**: Runs full test suite
2. **build**: Creates wheel and source distribution
3. **publish-to-pypi**: Uploads to PyPI using trusted publishing
4. **github-release**: Creates GitHub release with artifacts

**Key Features:**
- ✅ Uses PyPI Trusted Publishing (no API tokens needed)
- ✅ Runs tests before publishing
- ✅ Creates GitHub releases automatically
- ✅ Uploads build artifacts to release

## Verifying the Release

After publishing:

1. **Check PyPI**: Visit https://pypi.org/project/langsmith-cli/
2. **Test installation**:
   ```bash
   # Create a fresh virtual environment
   python -m venv test-env
   source test-env/bin/activate

   # Install from PyPI
   pip install langsmith-cli

   # Test the CLI
   langsmith-cli --version
   langsmith-cli --help
   ```

3. **Check GitHub Release**: Visit https://github.com/aviadr1/langsmith-cli/releases

## Troubleshooting

### Error: "Project name not found"

**Cause**: Package hasn't been registered on PyPI yet with trusted publishing.

**Solution**: Follow the "For First-Time Publishing" steps above.

### Error: "Publishing blocked"

**Cause**: The PyPI environment protection rules are blocking the deployment.

**Solution**:
- Check GitHub repository "Environments" settings
- Approve the pending deployment if reviewers are required
- Ensure the tag is on an allowed branch

### Error: "Tests failed"

**Cause**: Tests, linting, or type checking failed.

**Solution**:
```bash
# Run locally to debug
uv run pytest
uv run ruff check .
uv run pyright
```

Fix the issues and create a new version tag.

### Error: "Build failed"

**Cause**: Package build configuration is incorrect.

**Solution**:
```bash
# Test build locally
uv build

# Check the distribution
uv pip install --system twine
twine check dist/*
```

## Rolling Back a Release

If you need to remove a broken release:

1. **Yank the PyPI release** (doesn't delete, but hides from `pip install`):
   - Go to https://pypi.org/project/langsmith-cli/
   - Click "Manage" → "Releases"
   - Find the version and click "Options" → "Yank"

2. **Delete the GitHub release**:
   ```bash
   gh release delete v0.1.0
   ```

3. **Delete the git tag**:
   ```bash
   git tag -d v0.1.0
   git push origin :refs/tags/v0.1.0
   ```

4. **Fix the issue** and create a new patch version

## Best Practices

1. **Test locally before tagging**:
   ```bash
   uv run pytest
   uv run ruff check .
   uv run pyright
   uv build
   ```

2. **Use descriptive commit messages** for version bumps:
   ```bash
   git commit -m "Release v0.2.0: Add watch mode and field pruning"
   ```

3. **Tag with annotations** for better git history:
   ```bash
   git tag -a v0.2.0 -m "Release v0.2.0: Add watch mode and field pruning"
   ```

4. **Document breaking changes** in release notes

5. **Keep versions in sync**:
   - pyproject.toml version field
   - Git tag
   - GitHub release

## Security Notes

- ✅ **No API tokens stored**: Uses PyPI Trusted Publishing
- ✅ **Minimal permissions**: Workflow only has write access during publishing
- ✅ **Environment protection**: Can require reviewers for production releases
- ✅ **Audit trail**: All publishes logged in GitHub Actions

## Additional Resources

- [PyPI Trusted Publishing Guide](https://docs.pypi.org/trusted-publishers/)
- [Python Packaging User Guide](https://packaging.python.org/)
- [GitHub Actions for PyPI](https://github.com/marketplace/actions/pypi-publish)
- [Semantic Versioning](https://semver.org/)
