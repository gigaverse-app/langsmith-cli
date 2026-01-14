import pytest
from click.testing import CliRunner
from datetime import datetime, timezone
from uuid import UUID, uuid4
from langsmith.schemas import Dataset, Example, Prompt


@pytest.fixture
def runner():
    """Fixture for invoking command-line interfaces."""
    return CliRunner()


def create_dataset(
    name: str = "test-dataset",
    description: str = "Test dataset",
    data_type: str = "kv",
    example_count: int = 10,
    session_count: int = 1,
) -> Dataset:
    """Create a real Dataset Pydantic model instance."""
    return Dataset(
        name=name,
        description=description,
        data_type=data_type,
        id=UUID("ae99b6fa-a6db-4f1c-8868-bc6764f4c29e"),
        created_at=datetime(2024, 7, 3, 9, 27, 16, 98548, tzinfo=timezone.utc),
        modified_at=datetime(2024, 7, 3, 9, 27, 16, 98548, tzinfo=timezone.utc),
        example_count=example_count,
        session_count=session_count,
    )


def create_example(
    id_str: str = "3442bd7c-27a2-437b-a38c-f278e455d87b",
    dataset_id: str = "ae99b6fa-a6db-4f1c-8868-bc6764f4c29e",
    inputs: dict = None,
    outputs: dict = None,
    metadata: dict = None,
    index: int = 0,
) -> Example:
    """Create a real Example Pydantic model instance.

    Args:
        id_str: UUID string, or "auto" to generate based on index
        dataset_id: UUID string for dataset
        inputs: Dictionary of input data
        outputs: Dictionary of output data
        metadata: Dictionary of metadata
        index: Index for auto-generating UUIDs
    """
    if inputs is None:
        inputs = {"text": "Example input"}
    if outputs is None:
        outputs = {"result": "Example output"}
    if metadata is None:
        metadata = {"dataset_split": ["base"]}

    # Handle auto-generated UUIDs for testing
    if id_str == "auto":
        # Generate a valid random UUID
        id_str = str(uuid4())

    return Example(
        id=UUID(id_str),
        dataset_id=UUID(dataset_id),
        inputs=inputs,
        outputs=outputs,
        metadata=metadata,
        created_at=datetime(2024, 8, 15, 19, 47, 22, 513097, tzinfo=timezone.utc),
        modified_at=datetime(2024, 8, 15, 19, 47, 22, 513097, tzinfo=timezone.utc),
    )


def create_prompt(
    repo_handle: str = "test-prompt",
    full_name: str = "owner/test-prompt",
    owner: str = "owner",
    description: str = "Test prompt",
    is_public: bool = True,
) -> Prompt:
    """Create a real Prompt Pydantic model instance.

    All required fields are populated with valid values.
    Uses UUIDs as strings (required by Pydantic model).
    """
    return Prompt(
        repo_handle=repo_handle,
        full_name=full_name,
        id="a9adf0cb-6238-453f-abab-f75361a39ea8",  # id must be string
        tenant_id="00000000-0000-0000-0000-000000000000",  # required field
        owner=owner,
        description=description,
        is_public=is_public,
        is_archived=False,  # required field
        tags=["ChatPromptTemplate"],
        num_likes=0,
        num_downloads=772,
        num_views=23,
        num_commits=1,  # required field
        created_at=datetime(2024, 7, 3, 9, 27, 16, tzinfo=timezone.utc),
        updated_at=datetime(2024, 7, 3, 9, 27, 16, tzinfo=timezone.utc),
    )
