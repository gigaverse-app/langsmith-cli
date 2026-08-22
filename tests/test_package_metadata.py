from importlib.metadata import metadata


CANONICAL_REPOSITORY_URL = "https://github.com/gigaverse-app/langsmith-cli"


def test_package_metadata_links_to_canonical_repository_and_release_history() -> None:
    """INVARIANT: PyPI exposes the canonical repository and its release history."""
    project_urls = metadata("langsmith-cli").get_all("Project-URL")

    assert project_urls is not None
    assert f"Repository, {CANONICAL_REPOSITORY_URL}" in project_urls
    assert f"Changelog, {CANONICAL_REPOSITORY_URL}/releases" in project_urls
