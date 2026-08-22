"""Archive routing, reconciliation, and DuckDB query tests."""

from __future__ import annotations

from datetime import date, datetime
import json
from pathlib import Path
from typing import Any, Iterator

import pytest
from langsmith.schemas import Run

from conftest import create_run, parse_json_output
from langsmith_cli.archive.config import load_archive_config
from langsmith_cli.archive.models import ArchivePhase
from langsmith_cli.archive.query import ArchiveRunQuery, query_archive_runs
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

    common = dict(
        project_id="f47ac10b-58cc-4372-a567-0e02b2c3d479",
        project_name="dev/rowgroups",
        trace_date=date(2024, 7, 3),
    )
    sync_project_day(
        FakeRunsClient(_runs(["0", "1", "2"], "primary")),
        store,
        phase=ArchivePhase.PRIMARY,
        **common,
    )
    manifest = sync_project_day(
        FakeRunsClient(_runs(["1", "2", "3"], "reconciliation")),
        store,
        phase=ArchivePhase.RECONCILIATION,
        **common,
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
    common = dict(
        project_id="f47ac10b-58cc-4372-a567-0e02b2c3d479",
        project_name="dev/legacy",
        trace_date=date(2024, 7, 3),
    )

    def _legacy_serialize(run: Run) -> bytes:
        payload = run.model_dump(mode="python")
        line = json.dumps(
            payload, ensure_ascii=False, default=sync_module._archive_json_default
        )
        return line.encode("utf-8") + b"\n"

    with monkeypatch.context() as legacy:
        legacy.setattr(sync_module, "_serialize_run_line", _legacy_serialize)
        sync_project_day(
            FakeRunsClient([create_run(inputs={"deep": {"legacy": True}})]),
            store,
            phase=ArchivePhase.PRIMARY,
            **common,
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
        **common,
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
    common = dict(
        project_id="f47ac10b-58cc-4372-a567-0e02b2c3d479",
        project_name="dev/late-day",
        trace_date=date(2024, 7, 3),
    )
    sync_project_day(FakeRunsClient([]), store, phase=ArchivePhase.PRIMARY, **common)
    manifest = sync_project_day(
        FakeRunsClient([create_run(outputs={"late": True})]),
        store,
        phase=ArchivePhase.RECONCILIATION,
        **common,
    )
    assert manifest.sealed is True
    assert manifest.canonical_run_count == 1
    archived = query_archive_runs(ArchiveRunQuery(project="dev/late-day", limit=0))
    assert (archived[0].outputs or {})["late"] is True
