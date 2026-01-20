import os

# Set COLUMNS before any imports to ensure Rich Console detects correct width
# This must happen before importing any modules that create Console instances
os.environ["COLUMNS"] = "160"

import pytest
from click import unstyle
from click.testing import CliRunner
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4
from langsmith.schemas import Dataset, Example, Prompt, TracerSessionResult, Run


@pytest.fixture(scope="module")
def runner():
    """Fixture for invoking command-line interfaces.

    Module-scoped for performance - CliRunner creates isolated environments
    for each invoke() call, making it safe to share across tests.

    Uses wider terminal width (160 chars) to accommodate table with 6 columns.
    COLUMNS is set at module level before imports to ensure Rich detects it.
    """
    return CliRunner(env={"COLUMNS": "160"})


def strip_ansi(text: str) -> str:
    """Remove ANSI escape codes from text.

    Rich library outputs ANSI codes for colors and formatting. Tests need
    to strip these codes to check for plain text content.

    Uses Click's built-in unstyle() function which is robust and well-tested.

    Args:
        text: String potentially containing ANSI escape codes

    Returns:
        String with all ANSI codes removed

    Example:
        >>> strip_ansi("\\x1b[31mRed text\\x1b[0m")
        'Red text'
    """
    return unstyle(text)


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
    inputs: dict | None = None,
    outputs: dict | None = None,
    metadata: dict | None = None,
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


def create_project(
    name: str = "test-project",
    project_type: str = "tracer",  # Unused, kept for backward compatibility
    run_count: int = 0,
    last_run_start_time: datetime | None = None,
    error_rate: float | None = None,
    total_cost: float | None = None,
) -> TracerSessionResult:
    """Create a real TracerSessionResult (project) Pydantic model instance.

    Args:
        name: Project name
        project_type: Unused (kept for backward compatibility, SDK doesn't support this field)
        run_count: Number of runs in the project
        last_run_start_time: Last run start time (optional)
        error_rate: Error rate as a decimal (e.g., 0.05 for 5%)
        total_cost: Total cost in dollars (optional)
    """
    # SDK expects Decimal for total_cost, convert from float
    total_cost_decimal = Decimal(str(total_cost)) if total_cost is not None else None

    return TracerSessionResult(
        id=UUID("f47ac10b-58cc-4372-a567-0e02b2c3d479"),
        name=name,
        description="Test project",
        start_time=datetime(2024, 7, 3, 9, 27, 16, tzinfo=timezone.utc),
        run_count=run_count,
        tenant_id=UUID("00000000-0000-0000-0000-000000000000"),  # required field
        reference_dataset_id=None,  # optional field
        last_run_start_time=last_run_start_time,
        error_rate=error_rate,
        total_cost=total_cost_decimal,
    )


def create_run(
    name: str = "test-run",
    id_str: str = "12345678-1234-5678-1234-567812345678",
    run_type: str = "chain",
    tags: list[str] | None = None,
    metadata: dict | None = None,
    inputs: dict | None = None,
    outputs: dict | None = None,
    error: str | None = None,
    extra: dict | None = None,
    parent_run_id: str | None = None,
    trace_id: str | None = None,
    latency: float | None = None,
    total_tokens: int | None = None,
    status: str | None = None,
) -> Run:
    """Create a real Run Pydantic model instance.

    Args:
        name: Run name
        id_str: UUID string, or "auto" to generate random UUID
        run_type: Type of run (chain, llm, tool, etc.)
        tags: List of tags
        metadata: Dictionary of metadata (will be stored in extra['metadata'])
        inputs: Dictionary of input data
        outputs: Dictionary of output data
        error: Error message if run failed
        extra: Dictionary of extra data
        parent_run_id: UUID string of parent run (None for root runs)
        trace_id: UUID string of the trace (defaults to run id for root runs)
        latency: Run latency in seconds
        total_tokens: Total tokens used
        status: Override status (defaults based on error)
    """
    if tags is None:
        tags = []
    if inputs is None:
        inputs = {}
    if outputs is None:
        outputs = {}
    if extra is None:
        extra = {}

    # Handle auto-generated UUIDs
    if id_str == "auto":
        id_str = str(uuid4())

    # Metadata is stored in extra['metadata'] in the Run model
    if metadata:
        if "metadata" not in extra:
            extra["metadata"] = {}
        extra["metadata"].update(metadata)

    # Convert parent_run_id to UUID if provided
    parent_uuid = UUID(parent_run_id) if parent_run_id else None

    # trace_id defaults to run id for root runs
    if trace_id is None and parent_run_id is None:
        trace_uuid = UUID(id_str)
    elif trace_id:
        trace_uuid = UUID(trace_id)
    else:
        trace_uuid = None

    # Determine status
    if status is None:
        status = "success" if error is None else "error"

    return Run(
        id=UUID(id_str),
        name=name,
        run_type=run_type,
        start_time=datetime(2024, 7, 3, 9, 27, 16, tzinfo=timezone.utc),
        tags=tags,
        inputs=inputs,
        outputs=outputs,
        error=error,
        extra=extra,
        status=status,
        parent_run_id=parent_uuid,
        trace_id=trace_uuid,
        latency=latency,
        total_tokens=total_tokens,
    )


def make_run_id(n: int) -> str:
    """Generate a sequential UUID string for testing.

    Args:
        n: Sequence number (0-999999)

    Returns:
        UUID string like "00000000-0000-0000-0000-000000000001"
    """
    return f"00000000-0000-0000-0000-{n:012d}"


@pytest.fixture
def mock_client():
    """Fixture providing a mocked LangSmith client.

    Usage:
        def test_example(mock_client):
            mock_client.list_runs.return_value = [create_run()]
            # ... invoke CLI ...
    """
    from unittest.mock import patch, MagicMock

    with patch("langsmith.Client") as MockClient:
        client = MagicMock()
        MockClient.return_value = client
        yield client


@pytest.fixture
def sample_runs_file(tmp_path):
    """Create a JSONL file with sample runs for testing view-file command."""
    import json

    test_file = tmp_path / "test_runs.jsonl"
    run1 = create_run(
        name="Test Run 1",
        id_str="12345678-1234-5678-1234-567812345678",
        run_type="llm",
        status="success",
        latency=1.5,
        total_tokens=1000,
    )
    run2 = create_run(
        name="Test Run 2",
        id_str="87654321-4321-8765-4321-876543218765",
        run_type="chain",
        error="Test error",
        latency=2.3,
    )

    with open(test_file, "w") as f:
        f.write(json.dumps(run1.model_dump(mode="json")) + "\n")
        f.write(json.dumps(run2.model_dump(mode="json")) + "\n")

    return test_file
