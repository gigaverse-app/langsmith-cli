# Codecov Setup Guide

Quick guide to set up Codecov integration for coverage tracking and badges.

## 📋 Prerequisites

- GitHub repository with admin access
- CI workflow that generates `coverage.xml`

## 🚀 Setup Steps

### 1. Sign Up for Codecov

1. Go to [https://codecov.io](https://codecov.io)
2. Click "Sign up with GitHub"
3. Authorize Codecov to access your GitHub account

### 2. Add Repository

1. In Codecov dashboard, click "Add new repository"
2. Find `langsmith-cli` in the list
3. Click "Setup repo"

### 3. Get Upload Token

1. In repository settings, find "Settings" tab
2. Copy the "Repository Upload Token"
3. Format: `xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx`

### 4. Add Token to GitHub Secrets

1. Go to GitHub repository → Settings → Secrets and variables → Actions
2. Click "New repository secret"
3. Name: `CODECOV_TOKEN`
4. Value: Paste the token from step 3
5. Click "Add secret"

### 5. Verify Integration

1. Push a commit or trigger the CI workflow
2. Wait for CI to complete
3. Check Codecov dashboard for the first upload
4. You should see: "Latest commit coverage: 79.xx%"

### 6. Add Coverage Badge to README

Add this to the top of `README.md`:

```markdown
[![codecov](https://codecov.io/gh/gigaverse-app/langsmith-cli/branch/main/graph/badge.svg?token=YOUR_BADGE_TOKEN)](https://codecov.io/gh/gigaverse-app/langsmith-cli)
```

**Where to find `YOUR_BADGE_TOKEN`:**
1. Codecov dashboard → Settings → Badge
2. Copy the markdown snippet
3. Paste into README.md

## 🎯 What You Get

### 1. Coverage Badge

Shows current coverage percentage in README:

```
Coverage
79.45%
```

### 2. PR Annotations

Codecov comments on PRs with:
- Coverage delta (+2.5% or -1.2%)
- Diff coverage (coverage of changed lines)
- File-by-file breakdown
- Inline annotations on uncovered lines

### 3. Sunburst Graph

Visual representation of coverage by file/directory.

### 4. Trend Charts

Track coverage over time:
- Last 6 months
- Last 30 commits
- Per branch

### 5. Coverage Reports

Detailed reports showing:
- Uncovered lines
- Partially covered lines
- File-level coverage
- Function-level coverage

## ⚙️ Configuration (Optional)

Create `codecov.yml` in repo root for custom config:

```yaml
# codecov.yml
coverage:
  status:
    project:
      default:
        target: 79%  # Minimum coverage threshold
        threshold: 1%  # Allow 1% decrease
    patch:
      default:
        target: 80%  # New code should have 80% coverage

comment:
  layout: "reach,diff,flags,files,footer"
  behavior: default
  require_changes: false

ignore:
  - "tests/"
  - "docs/"
  - "**/__init__.py"
```

## 🔍 Troubleshooting

### No Coverage Data

**Symptom:** Codecov dashboard shows "No coverage data"

**Solutions:**
1. Check GitHub Actions logs for upload errors
2. Verify `CODECOV_TOKEN` secret is set correctly
3. Ensure `coverage.xml` is generated before upload
4. Check file path in workflow: `file: ./coverage.xml`

### Coverage Not Updating

**Symptom:** Old coverage percentage shows

**Solutions:**
1. Clear browser cache
2. Force refresh (Ctrl+F5)
3. Check that CI completed successfully
4. Verify upload logs show "Coverage uploaded successfully"

### PR Comments Not Appearing

**Symptom:** No Codecov comment on PRs

**Solutions:**
1. Check permissions in Codecov → Settings → GitHub Integration
2. Re-install Codecov GitHub App
3. Verify `pull-requests: write` permission in workflow
4. Check Codecov logs in PR checks

### Badge Not Showing

**Symptom:** Badge shows "unknown" or doesn't load

**Solutions:**
1. Verify badge token in markdown
2. Check repository is public or badge token is correct for private repos
3. Wait 5-10 minutes for first upload to process
4. Try clearing CDN cache: `?nocache=1` in URL

## 📊 Best Practices

### 1. Set Realistic Targets

Don't aim for 100% coverage immediately:
- Start with current coverage (79%)
- Increase target gradually (80% → 85% → 90%)
- Focus on critical paths first

### 2. Review Coverage in PRs

Use Codecov PR comments to:
- Ensure new code is tested
- Identify coverage gaps
- Block PRs that decrease coverage significantly

### 3. Ignore Non-Critical Files

Add to `codecov.yml`:
```yaml
ignore:
  - "tests/"
  - "**/__init__.py"
  - "setup.py"
  - "docs/"
```

### 4. Monitor Trends

Check coverage dashboard weekly:
- Is coverage increasing?
- Are new features tested?
- Which files need more tests?

### 5. Use Coverage as a Guide

Coverage is a metric, not a goal:
- ✅ 80% coverage with quality tests > 100% with poor tests
- ✅ Focus on edge cases, not coverage percentage
- ✅ Use coverage to find untested code, not as a pass/fail gate

## 🔗 Useful Links

- **Codecov Dashboard**: https://codecov.io/gh/gigaverse-app/langsmith-cli
- **Documentation**: https://docs.codecov.com/
- **GitHub App**: https://github.com/apps/codecov
- **Support**: https://codecov.io/support

## 🎓 Alternative Tools

If Codecov doesn't fit your needs:

1. **Coveralls** (https://coveralls.io)
   - Similar to Codecov
   - Free for open source
   - Good GitHub integration

2. **Code Climate** (https://codeclimate.com)
   - Coverage + code quality
   - More expensive
   - Better for enterprise

3. **GitHub Actions Artifacts**
   - Self-hosted HTML reports
   - Free (included in GitHub)
   - No external service
   - Requires manual downloading

4. **SonarCloud** (https://sonarcloud.io)
   - Coverage + security + quality
   - Free for open source
   - Comprehensive analysis

## ✅ Checklist

After setup, verify:

- [ ] Codecov account connected to GitHub
- [ ] Repository added to Codecov
- [ ] `CODECOV_TOKEN` secret added to GitHub
- [ ] CI workflow uploads coverage successfully
- [ ] Coverage badge added to README.md
- [ ] First coverage report visible in Codecov dashboard
- [ ] PR comment appears on test pull request
- [ ] Coverage threshold set in `codecov.yml` (optional)

---

**Need help?** File an issue or check [Codecov Support](https://codecov.io/support)
