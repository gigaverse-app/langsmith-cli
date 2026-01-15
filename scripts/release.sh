#!/bin/bash
# Release script: Bump versions, commit, tag, and push to trigger CI/CD
# Usage: ./scripts/release.sh [patch|minor|major|VERSION] [--skip-tests] [-y]
#   Default bump type is 'patch' if not specified
#   Or specify exact version like: ./scripts/release.sh 0.2.3
#   Add --skip-tests to skip running tests (faster releases)
#   Add -y to auto-confirm without prompts

set -e  # Exit on error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Check if we're in the project root
if [ ! -f "pyproject.toml" ]; then
    echo -e "${RED}Error: Must run from project root${NC}"
    exit 1
fi

# Parse arguments
BUMP_TYPE="patch"
SKIP_TESTS=false
AUTO_CONFIRM=false

while [ $# -gt 0 ]; do
    case "$1" in
        --skip-tests)
            SKIP_TESTS=true
            shift
            ;;
        -y|--yes)
            AUTO_CONFIRM=true
            shift
            ;;
        *)
            BUMP_TYPE="$1"
            shift
            ;;
    esac
done

# Validate bump type
if [[ ! "$BUMP_TYPE" =~ ^(patch|minor|major|[0-9]+\.[0-9]+\.[0-9]+)$ ]]; then
    echo -e "${RED}Error: Invalid argument '$BUMP_TYPE'${NC}"
    echo "Usage: ./scripts/release.sh [patch|minor|major|VERSION] [--skip-tests] [-y]"
    echo "Examples:"
    echo "  ./scripts/release.sh                      # Bump patch version"
    echo "  ./scripts/release.sh minor                # Bump minor version"
    echo "  ./scripts/release.sh 0.3.0                # Set specific version"
    echo "  ./scripts/release.sh --skip-tests         # Bump patch, skip tests"
    echo "  ./scripts/release.sh minor --skip-tests   # Bump minor, skip tests"
    echo "  ./scripts/release.sh -y                   # Bump patch, auto-confirm"
    echo "  ./scripts/release.sh minor --skip-tests -y # Fast release"
    exit 1
fi

# Get current version from pyproject.toml
CURRENT_VERSION=$(grep '^version = ' pyproject.toml | sed 's/version = "\(.*\)"/\1/')
echo -e "${GREEN}Current version: ${CURRENT_VERSION}${NC}"

# Check git status
if [ -n "$(git status --porcelain)" ]; then
    echo -e "${RED}Error: Working directory is not clean${NC}"
    echo -e "${YELLOW}Please commit or stash your changes first${NC}"
    git status --short
    exit 1
fi

# Make sure we're on main branch
CURRENT_BRANCH=$(git branch --show-current)
if [ "$CURRENT_BRANCH" != "main" ] && [ "$AUTO_CONFIRM" = false ]; then
    echo -e "${YELLOW}Warning: Not on main branch (currently on ${CURRENT_BRANCH})${NC}"
    read -p "Continue anyway? (y/N) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo -e "${YELLOW}Release cancelled${NC}"
        exit 0
    fi
fi

# Pull latest changes
echo -e "${GREEN}Pulling latest changes...${NC}"
git pull

# Ensure dependencies are installed
echo -e "${GREEN}Installing dependencies...${NC}"
uv sync

# Run linters
echo -e "${GREEN}Running linters...${NC}"
uv run ruff check --fix src/
uv run ruff format src/

# Run type checking
echo -e "${GREEN}Running type checks...${NC}"
uv run pyright src/

# Run tests (unless skipped)
if [ "$SKIP_TESTS" = false ]; then
    echo -e "${GREEN}Running tests...${NC}"
    uv run pytest tests/ -v
else
    echo -e "${YELLOW}Skipping tests (--skip-tests flag provided)${NC}"
fi

# Calculate new version
if [[ "$BUMP_TYPE" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    # Explicit version specified
    VERSION="$BUMP_TYPE"
else
    # Calculate bump from current version
    IFS='.' read -r -a version_parts <<< "$CURRENT_VERSION"
    major="${version_parts[0]}"
    minor="${version_parts[1]}"
    patch="${version_parts[2]}"

    case $BUMP_TYPE in
        major)
            VERSION="$((major + 1)).0.0"
            ;;
        minor)
            VERSION="${major}.$((minor + 1)).0"
            ;;
        patch)
            VERSION="${major}.${minor}.$((patch + 1))"
            ;;
    esac
fi

echo -e "${GREEN}New version: ${VERSION}${NC}"

# Confirm release (unless auto-confirm)
if [ "$AUTO_CONFIRM" = false ]; then
    read -p "Create release v${VERSION}? (y/N) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo -e "${YELLOW}Release cancelled${NC}"
        exit 0
    fi
else
    echo -e "${GREEN}Auto-confirming release v${VERSION}${NC}"
fi

# Update version in pyproject.toml
echo -e "${GREEN}Updating pyproject.toml...${NC}"
sed -i "s/^version = \".*\"/version = \"${VERSION}\"/" pyproject.toml

# Update version in .claude-plugin/plugin.json
echo -e "${GREEN}Updating .claude-plugin/plugin.json...${NC}"
sed -i "s/\"version\": \".*\"/\"version\": \"${VERSION}\"/" .claude-plugin/plugin.json

# Update version in .claude-plugin/marketplace.json (both locations)
echo -e "${GREEN}Updating .claude-plugin/marketplace.json...${NC}"
# Update metadata.version
sed -i "0,/\"version\": \".*\"/s//\"version\": \"${VERSION}\"/" .claude-plugin/marketplace.json
# Update plugins[0].version (second occurrence)
sed -i "0,/\"version\": \".*\"/! s/\"version\": \".*\"/\"version\": \"${VERSION}\"/" .claude-plugin/marketplace.json

# Update lockfile
echo -e "${GREEN}Updating uv.lock...${NC}"
uv lock

# Commit version changes
echo -e "${GREEN}Committing version changes...${NC}"
git add pyproject.toml .claude-plugin/plugin.json .claude-plugin/marketplace.json uv.lock
git commit -m "chore: Bump version to ${VERSION}

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"

# Create and push tag
echo -e "${GREEN}Creating tag v${VERSION}...${NC}"
git tag -a "v${VERSION}" -m "Release v${VERSION}"

# Push commit and tag
echo -e "${GREEN}Pushing changes and tag...${NC}"
git push origin main
git push origin "v${VERSION}"

echo -e "${GREEN}✓ Release v${VERSION} created!${NC}"
echo -e "${YELLOW}The GitHub Actions workflow will now:${NC}"
echo -e "  1. Build the package"
echo -e "  2. Publish to PyPI"
echo -e "  3. Create a GitHub release"
echo -e ""
echo -e "Monitor progress at: https://github.com/gigaverse-app/langsmith-cli/actions"
