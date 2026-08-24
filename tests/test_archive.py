"""Archive routing, reconciliation, and DuckDB query tests."""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from decimal import Decimal
import json
from pathlib import Path
from typing import Any, Iterator
from uuid import UUID

import pytest
from langsmith.schemas import Run

from conftest import create_run, parse_json_output
from langsmith_cli.archive.config import load_archive_config
from langsmith_cli.archive.models import ArchivePhase
from langsmith_cli.archive.query import (
    ArchiveRunQuery,
    count_archive_runs,
    query_archive_runs,
)
from langsmith_cli.archive.storage import create_store
from langsmith_cli.archive.sync import due_trace_dates, sync_project_day
from langsmith_cli.main import cli


class FakeRunsClient:
    def __init__(self, runs: list[Run]) -> None:
        self.runs = runs
        self.calls = 0

    def list_runs(self, **kwargs: Any) -> Iterator[Run]:
        self.calls += 1
        return iter(self.runs)


TYPED_DIMENSIONS_PARENT_ID = "22345678-1234-5678-1234-567812345678"


def test_route_config_selects_exactly_one_destination(tmp_path: Path) -> None:
    config_path = tmp_path / "archive.yaml"
    config_path.write_text(
        """routes:
  - name: dev
    project_pattern: dev/**
    archive_uri: s3://traces-dev/langsmith
  - name: staging
    project_pattern: stg/**
    archive_uri: s3://traces-stg/langsmith
""",
        encoding="utf-8",
    )
    config = load_archive_config(str(config_path))
    assert config.route_project("dev/agent").archive_uri == "s3://traces-dev/langsmith"
    assert config.route_project("stg/agent").name == "staging"
    with pytest.raises(ValueError, match="No archive route"):
        config.route_project("prd/agent")


def test_overlapping_routes_fail_fast(tmp_path: Path) -> None:
    config_path = tmp_path / "archive.yaml"
    config_path.write_text(
        """routes:
  - name: dev
    project_pattern: dev/**
    archive_uri: s3://traces-dev/langsmith
  - name: everything
    project_pattern: '**'
    archive_uri: s3://traces-all/langsmith
""",
        encoding="utf-8",
    )
    config = load_archive_config(str(config_path))
    with pytest.raises(ValueError, match="multiple archive routes"):
        config.route_project("dev/agent")


@pytest.mark.parametrize(
    "content",
    [
        "[]",
        "routes: invalid",
        "routes:\n  - invalid",
        "routes:\n  - name: dev\n    project_pattern: 'dev/**'",
        "routes:\n  - name: 7\n    project_pattern: 'dev/**'\n    archive_uri: /tmp/dev",
        "routes: []",
        """routes:
  - name: dev
    project_pattern: dev/**
    archive_uri: /tmp/dev
  - name: dev
    project_pattern: staging/**
    archive_uri: /tmp/staging
""",
    ],
)
def test_route_config_rejects_malformed_or_ambiguous_contracts(
    tmp_path: Path, content: str
) -> None:
    config_path = tmp_path / "invalid.yaml"
    config_path.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError):
        load_archive_config(str(config_path))


def test_route_config_requires_a_source(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LANGSMITH_ARCHIVE_CONFIG", raising=False)
    monkeypatch.delenv("LANGSMITH_ARCHIVE_URI", raising=False)
    with pytest.raises(ValueError, match="LANGSMITH_ARCHIVE_URI"):
        load_archive_config()


def test_route_config_requires_exact_selected_route(tmp_path: Path) -> None:
    config = load_archive_config(str(_write_single_route_config(tmp_path)))
    with pytest.raises(ValueError, match="exist exactly once"):
        config.route_named("missing")


def _write_single_route_config(tmp_path: Path) -> Path:
    config_path = tmp_path / "single-route.yaml"
    config_path.write_text(
        "routes:\n  - name: dev\n    project_pattern: 'dev/**'\n    archive_uri: /tmp/dev\n",
        encoding="utf-8",
    )
    return config_path


def test_due_dates_leave_two_day_repair_buffer() -> None:
    assert due_trace_dates(date(2026, 8, 21), 14) == (
        (date(2026, 8, 19), ArchivePhase.PRIMARY),
        (date(2026, 8, 9), ArchivePhase.RECONCILIATION),
    )


def test_binary_trace_payload_is_preserved_as_base64(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_uri = str(tmp_path / "archive")
    monkeypatch.setenv("LANGSMITH_ARCHIVE_URI", archive_uri)
    binary_run = create_run(inputs={"image": b"\xff\x00\x80"})
    sync_project_day(
        FakeRunsClient([binary_run]),
        create_store(archive_uri),
        project_id="f47ac10b-58cc-4372-a567-0e02b2c3d479",
        project_name="dev/binary",
        trace_date=date(2024, 7, 3),
        phase=ArchivePhase.PRIMARY,
    )
    archived = query_archive_runs(ArchiveRunQuery(project="dev/binary", limit=0))
    assert archived[0].inputs["image"] == {
        "__langsmith_archive_encoding__": "base64",
        "data": "/wCA",
    }


def test_reconciliation_deduplicates_and_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runner
) -> None:
    archive_uri = str(tmp_path / "archive")
    monkeypatch.setenv("LANGSMITH_ARCHIVE_URI", archive_uri)
    store = create_store(archive_uri)
    trace_date = date(2024, 7, 3)
    project_id = "f47ac10b-58cc-4372-a567-0e02b2c3d479"

    primary_run = create_run(outputs={"version": "primary"})
    primary_client = FakeRunsClient([primary_run])
    sync_project_day(
        primary_client,
        store,
        project_id=project_id,
        project_name="dev/agent",
        trace_date=trace_date,
        phase=ArchivePhase.PRIMARY,
    )

    updated_run = create_run(outputs={"version": "reconciled"})
    late_child = create_run(
        id_str="22345678-1234-5678-1234-567812345678",
        name="late-child",
        parent_run_id=str(primary_run.id),
        trace_id=str(primary_run.id),
    )
    reconciliation_client = FakeRunsClient([updated_run, late_child])
    manifest = sync_project_day(
        reconciliation_client,
        store,
        project_id=project_id,
        project_name="dev/agent",
        trace_date=trace_date,
        phase=ArchivePhase.RECONCILIATION,
    )
    assert manifest.sealed is True
    assert manifest.canonical_run_count == 2

    archived = query_archive_runs(
        ArchiveRunQuery(
            project="dev/agent",
            since=datetime(2024, 7, 3),
            before=datetime(2024, 7, 4),
            limit=0,
        )
    )
    assert {str(run.id) for run in archived} == {
        str(primary_run.id),
        str(late_child.id),
    }
    root = next(run for run in archived if run.id == primary_run.id)
    assert root.outputs == {"version": "reconciled"}

    sync_project_day(
        reconciliation_client,
        store,
        project_id=project_id,
        project_name="dev/agent",
        trace_date=trace_date,
        phase=ArchivePhase.RECONCILIATION,
    )
    assert reconciliation_client.calls == 1

    search_result = runner.invoke(
        cli,
        [
            "--json",
            "runs",
            "search",
            "reconciled",
            "--archive",
            "--project",
            "dev/agent",
        ],
    )
    assert search_result.exit_code == 0, search_result.output
    search_data = parse_json_output(search_result.output)
    assert [item["id"] for item in search_data] == [str(primary_run.id)]

    get_result = runner.invoke(
        cli,
        [
            "--json",
            "runs",
            "get",
            str(primary_run.id),
            "--archive",
            "--follow-children",
        ],
    )
    assert get_result.exit_code == 0, get_result.output
    get_data = parse_json_output(get_result.output)
    assert get_data["outputs"] == {"version": "reconciled"}
    assert [child["name"] for child in get_data["_children"]] == ["late-child"]

    count_result = runner.invoke(
        cli,
        [
            "--json",
            "runs",
            "list",
            "--archive",
            "--project",
            "dev/agent",
            "--since",
            "2024-07-03",
            "--before",
            "2024-07-04",
            "--count",
        ],
    )
    assert count_result.exit_code == 0, count_result.output
    assert count_result.output.strip() == "2"


def test_runs_snapshot_stores_payloads_as_text_not_inferred_structs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    read_json_auto STRUCT inference materializes trace payloads as typed nested
    columns, and its memory scales with payload complexity: a real project-day
    OOMed a 2.5 GiB DuckDB bound inside _write_runs_parquet before canonicalization
    ever ran. The CLI serializes the JSONL itself, so nested payload fields must be
    pre-serialized JSON text — the same VARCHAR shape Bulk Export v2 produces,
    which canonicalization already unifies. Snapshot memory then scales with row
    size, not payload nesting depth.
    """
    import duckdb

    archive_uri = str(tmp_path / "archive")
    monkeypatch.setenv("LANGSMITH_ARCHIVE_URI", archive_uri)
    store = create_store(archive_uri)
    nested_run = create_run(
        inputs={"deep": {"x": [1, 2, {"y": "z"}]}},
        outputs={"answer": {"content": "ok"}},
        tags=["t1", "t2"],
    )
    manifest = sync_project_day(
        FakeRunsClient([nested_run]),
        store,
        project_id="f47ac10b-58cc-4372-a567-0e02b2c3d479",
        project_name="dev/nested",
        trace_date=date(2024, 7, 3),
        phase=ArchivePhase.PRIMARY,
    )
    assert manifest.primary is not None
    raw_path = Path(store.base_uri) / manifest.primary.raw_key
    connection = duckdb.connect()
    try:
        described = {
            str(row[0]): str(row[1])
            for row in connection.execute(
                f"DESCRIBE SELECT * FROM read_parquet('{raw_path.as_posix()}')"
            ).fetchall()
        }
    finally:
        connection.close()
    for column in ("inputs", "outputs", "tags"):
        assert "STRUCT" not in described[column], (column, described[column])
    # Semantic preservation: canonical readers still see the full nested value.
    archived = query_archive_runs(ArchiveRunQuery(project="dev/nested", limit=0))
    assert archived[0].inputs == {"deep": {"x": [1, 2, {"y": "z"}]}}
    assert archived[0].tags == ["t1", "t2"]


def test_canonical_schema_v2_uses_stable_queryable_types(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """INVARIANT: query dimensions have one shape-independent Parquet type."""
    import duckdb

    archive_uri = str(tmp_path / "archive")
    monkeypatch.setenv("LANGSMITH_ARCHIVE_URI", archive_uri)
    store = create_store(archive_uri)
    run = create_run(
        tags=["production", "billing"],
        metadata={
            "environment": "production",
            "attempt": 3,
            "temperature": 0.25,
            "enabled": True,
            "deployment": {"region": "us-east-1"},
            "nothing": None,
            "price": Decimal("1.25"),
            "moment": datetime(2024, 7, 3, 9, 27, 16, tzinfo=timezone.utc),
            "clock": time(9, 27, 16),
            "correlation_id": UUID(TYPED_DIMENSIONS_PARENT_ID),
            "phase": ArchivePhase.PRIMARY,
        },
        inputs={"arbitrary": {"shape": [1, {"two": 2}]}},
        outputs={"answer": {"nested": True}},
    ).model_copy(
        update={
            "parent_run_ids": [UUID(TYPED_DIMENSIONS_PARENT_ID)],
            "prompt_token_details": {"cache_read": 7, "audio": 2},
            "completion_token_details": {"reasoning": 11},
            "prompt_cost_details": {"cache_read": Decimal("0.0000012")},
            "completion_cost_details": {"reasoning": Decimal("0.0000045")},
        }
    )

    manifest = sync_project_day(
        FakeRunsClient([run]),
        store,
        project_id="f47ac10b-58cc-4372-a567-0e02b2c3d479",
        project_name="dev/typed-dimensions",
        trace_date=date(2024, 7, 3),
        phase=ArchivePhase.PRIMARY,
    )

    assert manifest.schema_version == 2
    assert manifest.canonical_key is not None
    canonical_path = Path(store.base_uri) / manifest.canonical_key
    connection = duckdb.connect()
    try:
        described = {
            str(row[0]): str(row[1])
            for row in connection.execute(
                f"DESCRIBE SELECT * FROM read_parquet('{canonical_path.as_posix()}')"
            ).fetchall()
        }
        dimensions = connection.execute(
            "SELECT list_contains(tags, 'production'), "
            "list_contains(parent_run_ids, ?), metadata['environment'], "
            "metadata['attempt'], metadata['deployment'], "
            "prompt_token_details['cache_read'], "
            "completion_cost_details['reasoning'], metadata "
            f"FROM read_parquet('{canonical_path.as_posix()}')",
            [TYPED_DIMENSIONS_PARENT_ID],
        ).fetchone()
    finally:
        connection.close()

    assert described["tags"] == "VARCHAR[]"
    assert described["parent_run_ids"] == "VARCHAR[]"
    assert described["metadata"] == "MAP(VARCHAR, VARCHAR)"
    assert described["prompt_token_details"] == "MAP(VARCHAR, BIGINT)"
    assert described["completion_token_details"] == "MAP(VARCHAR, BIGINT)"
    assert described["prompt_cost_details"] == "MAP(VARCHAR, DECIMAL(38,18))"
    assert described["completion_cost_details"] == "MAP(VARCHAR, DECIMAL(38,18))"
    for payload_column in ("inputs", "outputs", "extra", "feedback_stats", "events"):
        assert described[payload_column] == "VARCHAR"
    assert dimensions == (
        True,
        True,
        "production",
        "3",
        '{"region":"us-east-1"}',
        7,
        Decimal("0.000004500000000000"),
        {
            "environment": "production",
            "attempt": "3",
            "temperature": "0.25",
            "enabled": "true",
            "deployment": '{"region":"us-east-1"}',
            "nothing": None,
            "price": "1.25",
            "moment": "2024-07-03T09:27:16+00:00",
            "clock": "09:27:16",
            "correlation_id": TYPED_DIMENSIONS_PARENT_ID,
            "phase": "primary",
        },
    )

    archived = query_archive_runs(
        ArchiveRunQuery(project="dev/typed-dimensions", tags=("production",), limit=0)
    )
    assert archived[0].tags == ["production", "billing"]
    assert archived[0].parent_run_ids == [UUID(TYPED_DIMENSIONS_PARENT_ID)]
    assert archived[0].prompt_token_details == {"cache_read": 7, "audio": 2}
    assert archived[0].completion_cost_details == {
        "reasoning": Decimal("0.000004500000000000")
    }


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        ({"metadata": "not-an-object"}, "must be an object"),
        ({"metadata": {7: "not-a-string-key"}}, "keys must be strings"),
    ],
)
def test_runs_api_snapshot_rejects_invalid_metadata_contracts(
    tmp_path: Path,
    extra: dict[object, object],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        sync_project_day(
            FakeRunsClient([create_run(extra=extra)]),
            create_store(str(tmp_path / "archive")),
            project_id="f47ac10b-58cc-4372-a567-0e02b2c3d479",
            project_name="dev/invalid-metadata",
            trace_date=date(2024, 7, 3),
            phase=ArchivePhase.PRIMARY,
        )


def test_streaming_schema_guard_rejects_noncanonical_dimension_types() -> None:
    import pyarrow

    from langsmith_cli.archive.sync import _query_dimensions_are_typed

    canonical = {
        "tags": pyarrow.list_(pyarrow.string()),
        "parent_run_ids": pyarrow.list_(pyarrow.string()),
        "prompt_token_details": pyarrow.map_(pyarrow.string(), pyarrow.int64()),
        "completion_token_details": pyarrow.map_(pyarrow.string(), pyarrow.int64()),
        "prompt_cost_details": pyarrow.map_(
            pyarrow.string(), pyarrow.decimal128(38, 18)
        ),
        "completion_cost_details": pyarrow.map_(
            pyarrow.string(), pyarrow.decimal128(38, 18)
        ),
        "metadata": pyarrow.map_(pyarrow.string(), pyarrow.string()),
    }
    invalid_types = (
        ("tags", pyarrow.list_(pyarrow.int64())),
        ("prompt_token_details", pyarrow.string()),
        ("prompt_token_details", pyarrow.map_(pyarrow.int64(), pyarrow.int64())),
        ("prompt_token_details", pyarrow.map_(pyarrow.string(), pyarrow.float64())),
    )

    for column, data_type in invalid_types:
        invalid = {**canonical, column: data_type}
        assert _query_dimensions_are_typed(pyarrow.schema(invalid)) is False


def test_mixed_v1_v2_archive_days_query_through_one_normalized_relation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """INVARIANT: schema upgrades never strand already-published project-days."""
    import duckdb

    archive_uri = str(tmp_path / "archive")
    monkeypatch.setenv("LANGSMITH_ARCHIVE_URI", archive_uri)
    store = create_store(archive_uri)
    project_id = "f47ac10b-58cc-4372-a567-0e02b2c3d479"
    legacy_run = create_run(
        tags=["shared", "legacy"],
        metadata={"environment": "legacy", "attempt": 1},
    ).model_copy(
        update={
            "parent_run_ids": [UUID(TYPED_DIMENSIONS_PARENT_ID)],
            "prompt_token_details": {"cache_read": 5},
            "completion_token_details": {"reasoning": 8},
            "prompt_cost_details": {"cache_read": Decimal("0.000001")},
            "completion_cost_details": {"reasoning": Decimal("0.000004")},
        }
    )
    legacy_manifest = sync_project_day(
        FakeRunsClient([legacy_run]),
        store,
        project_id=project_id,
        project_name="dev/mixed-schema",
        trace_date=date(2024, 7, 3),
        phase=ArchivePhase.PRIMARY,
    )
    assert legacy_manifest.canonical_key is not None
    current_path = Path(store.base_uri) / legacy_manifest.canonical_key
    legacy_path = current_path.with_suffix(".legacy.parquet")
    connection = duckdb.connect()
    try:
        connection.execute(
            "COPY (SELECT * EXCLUDE (tags, parent_run_ids, metadata, "
            "prompt_token_details, completion_token_details, "
            "prompt_cost_details, completion_cost_details), "
            "CAST(to_json(tags) AS VARCHAR) AS tags, "
            "CAST(to_json(parent_run_ids) AS VARCHAR) AS parent_run_ids, "
            "CAST(to_json(prompt_token_details) AS VARCHAR) "
            "AS prompt_token_details, "
            "CAST(to_json(completion_token_details) AS VARCHAR) "
            "AS completion_token_details, "
            "CAST(to_json(prompt_cost_details) AS VARCHAR) "
            "AS prompt_cost_details, "
            "CAST(to_json(completion_cost_details) AS VARCHAR) "
            "AS completion_cost_details "
            f"FROM read_parquet('{current_path.as_posix()}')) "
            f"TO '{legacy_path.as_posix()}' (FORMAT PARQUET)"
        )
    finally:
        connection.close()
    legacy_path.replace(current_path)
    manifest_key = (
        Path(store.base_uri)
        / "manifests"
        / f"project_id={project_id}"
        / "date=2024-07-03.json"
    )
    manifest_payload = json.loads(manifest_key.read_text(encoding="utf-8"))
    manifest_payload["schema_version"] = 1
    manifest_key.write_text(json.dumps(manifest_payload), encoding="utf-8")

    current_run = create_run(
        id_str="32345678-1234-5678-1234-567812345678",
        tags=["shared", "current"],
        metadata={"environment": "current", "attempt": 2},
    )
    sync_project_day(
        FakeRunsClient([current_run]),
        store,
        project_id=project_id,
        project_name="dev/mixed-schema",
        trace_date=date(2024, 7, 4),
        phase=ArchivePhase.PRIMARY,
    )

    query = ArchiveRunQuery(project="dev/mixed-schema", tags=("shared",), limit=0)
    archived = query_archive_runs(query)
    assert {str(run.id) for run in archived} == {
        str(legacy_run.id),
        str(current_run.id),
    }
    assert count_archive_runs(query) == 2
    legacy = next(run for run in archived if run.id == legacy_run.id)
    assert legacy.tags == ["shared", "legacy"]
    assert legacy.parent_run_ids == [UUID(TYPED_DIMENSIONS_PARENT_ID)]
    assert legacy.prompt_token_details == {"cache_read": 5}


def test_reconciliation_upgrades_an_unsealed_v1_manifest_to_v2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_uri = str(tmp_path / "archive")
    monkeypatch.setenv("LANGSMITH_ARCHIVE_URI", archive_uri)
    store = create_store(archive_uri)
    project_id = "f47ac10b-58cc-4372-a567-0e02b2c3d479"
    primary = sync_project_day(
        FakeRunsClient([create_run()]),
        store,
        project_id=project_id,
        project_name="dev/upgrade-schema",
        trace_date=date(2024, 7, 3),
        phase=ArchivePhase.PRIMARY,
    )
    manifest_path = (
        Path(store.base_uri)
        / "manifests"
        / f"project_id={project_id}"
        / "date=2024-07-03.json"
    )
    payload = primary.to_dict()
    payload["schema_version"] = 1
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    reconciled = sync_project_day(
        FakeRunsClient([create_run()]),
        store,
        project_id=project_id,
        project_name="dev/upgrade-schema",
        trace_date=date(2024, 7, 3),
        phase=ArchivePhase.RECONCILIATION,
    )

    assert reconciled.schema_version == 2
    assert reconciled.sealed is True


def test_oversized_days_are_staged_and_converted_in_bounded_pieces(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The JSON reader's reconstruction buffers and string chunks scale with the file
    it scans, so converting one whole project-day at once OOMed real days even at a
    2 GiB bound. Staging must split into byte-bounded pieces converted one at a
    time; a day of any size then converts inside a fixed working set. This forces
    every run into its own piece and proves the multi-piece path publishes the same
    canonical contract: all rows, unique IDs, values intact.
    """
    from langsmith_cli.archive import sync as sync_module

    monkeypatch.setattr(sync_module, "STAGING_PIECE_MAX_BYTES", 1)
    archive_uri = str(tmp_path / "archive")
    monkeypatch.setenv("LANGSMITH_ARCHIVE_URI", archive_uri)
    runs = [
        create_run(
            id_str=f"12345678-1234-5678-1234-56781234567{index}",
            inputs={"deep": {"index": index}},
            outputs={"answer": f"value-{index}"},
            # One piece carries a real error while the others are all-null, so the
            # per-piece inferred types disagree (JSON extension vs string) — the
            # exact mismatch the combine's schema unification must absorb.
            error="boom" if index == 1 else None,
        )
        for index in range(3)
    ]
    manifest = sync_project_day(
        FakeRunsClient(runs),
        create_store(archive_uri),
        project_id="f47ac10b-58cc-4372-a567-0e02b2c3d479",
        project_name="dev/pieces",
        trace_date=date(2024, 7, 3),
        phase=ArchivePhase.PRIMARY,
    )
    assert manifest.canonical_run_count == 3
    archived = query_archive_runs(ArchiveRunQuery(project="dev/pieces", limit=0))
    assert {(run.outputs or {}).get("answer") for run in archived} == {
        "value-0",
        "value-1",
        "value-2",
    }
    assert {str(run.inputs["deep"]["index"]) for run in archived} == {"0", "1", "2"}
    # No staging litter survives a successful conversion.
    day_directory = Path(archive_uri)
    leftovers = [p for p in day_directory.rglob("*") if p.suffix in {".jsonl", ".part"}]
    assert leftovers == []


def test_streaming_dedup_holds_across_row_groups_and_snapshots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Dedup tests elsewhere use single-row-group days; this forces every run into its
    own piece (hence its own row group in raw) and proves reconciliation precedence
    holds across group boundaries: shared IDs take the reconciliation value,
    primary-only rows survive, late reconciliation-only rows are added.
    """
    from langsmith_cli.archive import sync as sync_module

    monkeypatch.setattr(sync_module, "STAGING_PIECE_MAX_BYTES", 1)

    def _legacy_sql_path_is_forbidden(*args: object, **kwargs: object) -> int:
        raise AssertionError("canonical v2 raw must stay on the bounded streaming path")

    monkeypatch.setattr(
        sync_module, "_canonicalize_duckdb", _legacy_sql_path_is_forbidden
    )
    archive_uri = str(tmp_path / "archive")
    monkeypatch.setenv("LANGSMITH_ARCHIVE_URI", archive_uri)
    store = create_store(archive_uri)

    def _runs(ids: list[str], version: str) -> list[Run]:
        return [
            create_run(
                id_str=f"12345678-1234-5678-1234-56781234567{suffix}",
                outputs={"version": version},
            )
            for suffix in ids
        ]

    sync_project_day(
        FakeRunsClient(_runs(["0", "1", "2"], "primary")),
        store,
        phase=ArchivePhase.PRIMARY,
        project_id="f47ac10b-58cc-4372-a567-0e02b2c3d479",
        project_name="dev/rowgroups",
        trace_date=date(2024, 7, 3),
    )
    manifest = sync_project_day(
        FakeRunsClient(_runs(["1", "2", "3"], "reconciliation")),
        store,
        phase=ArchivePhase.RECONCILIATION,
        project_id="f47ac10b-58cc-4372-a567-0e02b2c3d479",
        project_name="dev/rowgroups",
        trace_date=date(2024, 7, 3),
    )
    assert manifest.canonical_run_count == 4
    archived = query_archive_runs(ArchiveRunQuery(project="dev/rowgroups", limit=0))
    versions = {str(run.id)[-1]: (run.outputs or {})["version"] for run in archived}
    assert versions == {
        "0": "primary",
        "1": "reconciliation",
        "2": "reconciliation",
        "3": "reconciliation",
    }


def test_legacy_struct_raw_still_canonicalizes_through_the_sql_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Raw generations written before byte-bounded staging carry inferred STRUCT
    payload columns; they self-expire with the raw/ lifecycle, but until then an
    unsealed day can pair a legacy-primary with a new-format reconciliation. The
    canonicalize dispatcher must detect the non-text payload and route the SQL
    normalization path, producing the same JSON-text canonical contract.
    """
    from langsmith_cli.archive import sync as sync_module

    archive_uri = str(tmp_path / "archive")
    monkeypatch.setenv("LANGSMITH_ARCHIVE_URI", archive_uri)
    store = create_store(archive_uri)

    def _v2_streaming_path_is_forbidden(*args: object, **kwargs: object) -> int:
        raise AssertionError("legacy inferred payloads require SQL normalization")

    monkeypatch.setattr(
        sync_module, "_canonicalize_streaming", _v2_streaming_path_is_forbidden
    )

    def _legacy_serialize(run: Run) -> bytes:
        payload = run.model_dump(mode="python")
        line = json.dumps(
            payload, ensure_ascii=False, default=sync_module._archive_json_default
        )
        return line.encode("utf-8") + b"\n"

    with monkeypatch.context() as legacy:
        legacy.setattr(sync_module, "_serialize_run_line", _legacy_serialize)
        legacy.setattr(sync_module, "_runs_api_snapshot_select", lambda source: source)
        sync_project_day(
            FakeRunsClient([create_run(inputs={"deep": {"legacy": True}})]),
            store,
            phase=ArchivePhase.PRIMARY,
            project_id="f47ac10b-58cc-4372-a567-0e02b2c3d479",
            project_name="dev/legacy",
            trace_date=date(2024, 7, 3),
        )
    manifest = sync_project_day(
        FakeRunsClient(
            [
                create_run(inputs={"deep": {"legacy": True}}),
                create_run(
                    id_str="12345678-1234-5678-1234-567812345679",
                    inputs={"late": True},
                ),
            ]
        ),
        store,
        phase=ArchivePhase.RECONCILIATION,
        project_id="f47ac10b-58cc-4372-a567-0e02b2c3d479",
        project_name="dev/legacy",
        trace_date=date(2024, 7, 3),
    )
    assert manifest.canonical_run_count == 2
    archived = query_archive_runs(ArchiveRunQuery(project="dev/legacy", limit=0))
    assert {json.dumps(run.inputs, sort_keys=True) for run in archived} == {
        json.dumps({"deep": {"legacy": True}}, sort_keys=True),
        json.dumps({"late": True}, sort_keys=True),
    }


def test_empty_primary_with_late_reconciliation_seals_the_late_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A day empty at D+2 whose runs only appear by D+12 must still seal them."""
    archive_uri = str(tmp_path / "archive")
    monkeypatch.setenv("LANGSMITH_ARCHIVE_URI", archive_uri)
    store = create_store(archive_uri)
    sync_project_day(
        FakeRunsClient([]),
        store,
        phase=ArchivePhase.PRIMARY,
        project_id="f47ac10b-58cc-4372-a567-0e02b2c3d479",
        project_name="dev/late-day",
        trace_date=date(2024, 7, 3),
    )
    manifest = sync_project_day(
        FakeRunsClient([create_run(outputs={"late": True})]),
        store,
        phase=ArchivePhase.RECONCILIATION,
        project_id="f47ac10b-58cc-4372-a567-0e02b2c3d479",
        project_name="dev/late-day",
        trace_date=date(2024, 7, 3),
    )
    assert manifest.sealed is True
    assert manifest.canonical_run_count == 1
    archived = query_archive_runs(ArchiveRunQuery(project="dev/late-day", limit=0))
    assert (archived[0].outputs or {})["late"] is True


def test_empty_snapshot_does_not_route_the_day_through_the_sql_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Empty phases write an id-only Parquet carrying none of the typed dimension
    columns. That absence must never veto the bounded streaming path for the
    day's real snapshot: an empty phase paired with a whale phase is exactly the
    day-scaled SQL union that OOMed every fixed production memory bound.
    """
    from langsmith_cli.archive import sync as sync_module

    def _sql_path_is_forbidden(*args: object, **kwargs: object) -> int:
        raise AssertionError("empty snapshots must stay on the bounded streaming path")

    monkeypatch.setattr(sync_module, "_canonicalize_duckdb", _sql_path_is_forbidden)
    archive_uri = str(tmp_path / "archive")
    monkeypatch.setenv("LANGSMITH_ARCHIVE_URI", archive_uri)
    store = create_store(archive_uri)
    project_id = "f47ac10b-58cc-4372-a567-0e02b2c3d479"
    sync_project_day(
        FakeRunsClient([]),
        store,
        project_id=project_id,
        project_name="dev/empty-phase",
        trace_date=date(2024, 7, 3),
        phase=ArchivePhase.PRIMARY,
    )
    manifest = sync_project_day(
        FakeRunsClient([create_run(tags=["kept"], metadata={"attempt": 1})]),
        store,
        project_id=project_id,
        project_name="dev/empty-phase",
        trace_date=date(2024, 7, 3),
        phase=ArchivePhase.RECONCILIATION,
    )
    assert manifest.canonical_run_count == 1
    archived = query_archive_runs(
        ArchiveRunQuery(project="dev/empty-phase", tags=("kept",), limit=0)
    )
    assert [run.tags for run in archived] == [["kept"]]
    # Control: a day where every snapshot is empty still publishes (zero rows).
    all_empty = sync_project_day(
        FakeRunsClient([]),
        store,
        project_id=project_id,
        project_name="dev/empty-phase",
        trace_date=date(2024, 7, 4),
        phase=ArchivePhase.PRIMARY,
    )
    assert all_empty.canonical_run_count == 0


def test_unsealed_v1_text_raw_migrates_to_v2_on_the_bounded_streaming_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Feed the genuine migration input: raw staged by the v1 writer, which stored
    tags/parent_run_ids as JSON text, token/cost details as inferred columns
    (Decimal costs as JSON strings), and no metadata column. Resuming the
    unsealed day must publish typed v2 without ever touching the day-scaled SQL
    union — unsealed v1 days at deploy time include the whale days that
    motivated the memory bounds.
    """
    from langsmith_cli.archive import sync as sync_module
    from langsmith_cli.archive.sync import BULK_EXPORT_JSON_COLUMNS

    archive_uri = str(tmp_path / "archive")
    monkeypatch.setenv("LANGSMITH_ARCHIVE_URI", archive_uri)
    store = create_store(archive_uri)
    project_id = "f47ac10b-58cc-4372-a567-0e02b2c3d479"

    def _v1_serialize(run: Run) -> bytes:
        payload = run.model_dump(mode="python")
        # The v1 taxonomy pre-serialized payloads AND tags/parent_run_ids as
        # JSON text; token/cost details were left for read_json_auto inference.
        for column in BULK_EXPORT_JSON_COLUMNS:
            value = payload.get(column)
            if value is not None:
                payload[column] = json.dumps(
                    value,
                    ensure_ascii=False,
                    default=sync_module._archive_json_default,
                )
        line = json.dumps(
            payload, ensure_ascii=False, default=sync_module._archive_json_default
        )
        return line.encode("utf-8") + b"\n"

    v1_run = create_run(
        tags=["shared", "v1"],
        metadata={"environment": "legacy", "attempt": 1},
    ).model_copy(
        update={
            "parent_run_ids": [UUID(TYPED_DIMENSIONS_PARENT_ID)],
            "prompt_token_details": {"cache_read": 5},
            "completion_token_details": {"reasoning": 8},
            "prompt_cost_details": {"cache_read": Decimal("0.000001")},
            "completion_cost_details": {"reasoning": Decimal("0.000004")},
        }
    )
    with monkeypatch.context() as v1:
        v1.setattr(sync_module, "_serialize_run_line", _v1_serialize)
        v1.setattr(sync_module, "_runs_api_snapshot_select", lambda source: source)
        sync_project_day(
            FakeRunsClient([v1_run]),
            store,
            project_id=project_id,
            project_name="dev/migrate-v1",
            trace_date=date(2024, 7, 3),
            phase=ArchivePhase.PRIMARY,
        )
    manifest_path = (
        Path(store.base_uri)
        / "manifests"
        / f"project_id={project_id}"
        / "date=2024-07-03.json"
    )
    manifest_payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_payload["schema_version"] = 1
    manifest_path.write_text(json.dumps(manifest_payload), encoding="utf-8")

    def _sql_path_is_forbidden(*args: object, **kwargs: object) -> int:
        raise AssertionError("v1 text raw must migrate on the bounded streaming path")

    monkeypatch.setattr(sync_module, "_canonicalize_duckdb", _sql_path_is_forbidden)
    late_run = create_run(
        id_str="32345678-1234-5678-1234-567812345678",
        tags=["shared", "late"],
        metadata={"environment": "current"},
    )
    reconciled = sync_project_day(
        FakeRunsClient([late_run]),
        store,
        project_id=project_id,
        project_name="dev/migrate-v1",
        trace_date=date(2024, 7, 3),
        phase=ArchivePhase.RECONCILIATION,
    )
    assert reconciled.schema_version == 2
    assert reconciled.sealed is True
    assert reconciled.canonical_run_count == 2
    assert reconciled.canonical_key is not None

    import pyarrow.parquet

    canonical_schema = pyarrow.parquet.ParquetFile(
        str(Path(store.base_uri) / reconciled.canonical_key)
    ).schema_arrow
    from langsmith_cli.archive.sync import _query_dimensions_are_typed

    assert _query_dimensions_are_typed(canonical_schema) is True

    archived = query_archive_runs(
        ArchiveRunQuery(project="dev/migrate-v1", tags=("shared",), limit=0)
    )
    assert {str(run.id) for run in archived} == {str(v1_run.id), str(late_run.id)}
    migrated = next(run for run in archived if run.id == v1_run.id)
    assert migrated.tags == ["shared", "v1"]
    assert migrated.parent_run_ids == [UUID(TYPED_DIMENSIONS_PARENT_ID)]
    assert migrated.prompt_token_details == {"cache_read": 5}
    assert migrated.completion_token_details == {"reasoning": 8}
    assert migrated.prompt_cost_details == {"cache_read": Decimal("0.000001")}
    assert migrated.completion_cost_details == {"reasoning": Decimal("0.000004")}
    assert (migrated.extra or {})["metadata"] == {
        "environment": "legacy",
        "attempt": 1,
    }
    only_v1 = query_archive_runs(
        ArchiveRunQuery(project="dev/migrate-v1", tags=("v1",), limit=0)
    )
    assert [run.id for run in only_v1] == [v1_run.id]
