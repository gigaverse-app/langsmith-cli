import pytest
from click.testing import CliRunner


@pytest.fixture
def runner():
    """Fixture for invoking command-line interfaces."""
    return CliRunner()
