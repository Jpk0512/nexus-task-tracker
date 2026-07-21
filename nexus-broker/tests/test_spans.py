"""F2-05 OTel-style span store — `nexus-foundation/plans/wave-2.md` §(e),
FDEC-8 (OTLP-compatible span model, dual-sink), ADR-001 (DuckDB Tier 2,
single writer).

Covers exactly this leaf's acceptance surface:
  - `spans.validate_span` rejects a malformed span with a typed
    `SpanValidationError` (never silently drops it, never lets a malformed
    row reach the store) and normalizes a well-formed one;
  - `spans.SpanStore.record` persists a well-formed span as ONE real DuckDB
    row (never mocked — tdd-core's "no mocking the analytics DB" rule);
  - parent-child linkage across dispatch -> gate -> leg is queryable
    straight off the DuckDB file, proving the shape FDEC-8 requires
    (`trace(session) > span(dispatch|gate|leg)`);
  - DuckDB itself is the single-writer enforcement mechanism (ADR-001), not
    just documentation — a second write connection against the same file
    fails loud;
  - `event_bus.handle_span_emit` is upgraded from the F2-02 forward-stub
    (notepad #331) to actually write through `SpanStore`, with shape
    validation at the write boundary — a malformed span raises before
    touching disk, and the daemon-facing state stays usable afterward (the
    "never crashes the daemon" acceptance criterion; the actual no-crash
    guarantee is `server._client_loop`'s existing catch-all, exercised here
    only at the handler-contract level).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import duckdb
import pytest

from broker.daemon import event_bus, spans
from broker.daemon.server import DaemonState, handle_request

# ── spans.validate_span — shape validation at the write boundary ───────────


def test_validate_span_accepts_a_well_formed_dispatch_span() -> None:
    clean = spans.validate_span(
        {
            "trace_id": "t1",
            "span_id": "s1",
            "parent_span_id": None,
            "name": "dispatch:pipeline-data",
            "kind": "dispatch",
            "status": "OK",
            "duration_ms": 42.5,
            "tokens": 100,
            "attributes": {"persona": "pipeline-data"},
        }
    )
    assert clean["trace_id"] == "t1"
    assert clean["span_id"] == "s1"
    assert clean["parent_span_id"] is None
    assert clean["kind"] == "dispatch"
    assert clean["duration_ms"] == 42.5
    assert clean["tokens"] == 100
    assert '"persona": "pipeline-data"' in clean["attributes"]


def test_validate_span_fills_safe_defaults_when_optional_fields_absent() -> None:
    clean = spans.validate_span({"trace_id": "t1", "span_id": "s1", "name": "leg:x", "kind": "leg"})
    assert clean["parent_span_id"] is None
    assert clean["status"] == "UNSET"
    assert clean["duration_ms"] is None
    assert clean["tokens"] is None
    assert clean["attributes"] == "{}"
    assert isinstance(clean["start_time"], str) and clean["start_time"]


@pytest.mark.parametrize(
    "bad_span",
    [
        "not-a-dict",
        {},
        {"trace_id": "t1"},  # missing span_id/name/kind
        {"trace_id": "t1", "span_id": "s1", "name": "x", "kind": "not-a-real-kind"},
        {"trace_id": "t1", "span_id": "s1", "name": "x", "kind": "dispatch", "duration_ms": "fast"},
        {"trace_id": "t1", "span_id": "s1", "name": "x", "kind": "dispatch", "attributes": "nope"},
        {"trace_id": "t1", "span_id": "s1", "name": "x", "kind": "dispatch", "status": "bogus"},
        {"trace_id": "", "span_id": "s1", "name": "x", "kind": "dispatch"},
    ],
)
def test_validate_span_rejects_malformed_spans_with_typed_error(bad_span) -> None:
    with pytest.raises(spans.SpanValidationError):
        spans.validate_span(bad_span)


def test_validate_span_rejects_parent_equal_to_self() -> None:
    with pytest.raises(spans.SpanValidationError):
        spans.validate_span(
            {"trace_id": "t1", "span_id": "s1", "parent_span_id": "s1", "name": "x", "kind": "leg"}
        )


# ── TASK-093 stage 1/2: the extended dispatch-span attribute schema ────────
# (task_id, workflow_id, persona, model, task_tier, marker, tokens,
# token_source, tool_uses) — spans.py's `validate_dispatch_attributes`,
# called from `validate_span` ONLY for kind="dispatch" spans.


def test_validate_span_accepts_full_extended_dispatch_attribute_schema() -> None:
    """Every field of the TASK-093 extended schema, all present and
    well-formed, round-trips through `validate_span` unchanged (attributes
    end up JSON-encoded in the returned `attributes` string)."""
    clean = spans.validate_span(
        {
            "trace_id": "t1",
            "span_id": "s1",
            "name": "dispatch:hermes",
            "kind": "dispatch",
            "attributes": {
                "task_id": "TASK-093-capture",
                "workflow_id": "wf-1",
                "persona": "hermes",
                "model": "sonnet",
                "task_tier": "T2",
                "marker": "DONE",
                "tokens": 1234,
                "token_source": "exact",
                "tool_uses": 7,
            },
        }
    )
    decoded = json.loads(clean["attributes"])
    assert decoded["task_id"] == "TASK-093-capture"
    assert decoded["workflow_id"] == "wf-1"
    assert decoded["persona"] == "hermes"
    assert decoded["model"] == "sonnet"
    assert decoded["task_tier"] == "T2"
    assert decoded["marker"] == "DONE"
    assert decoded["tokens"] == 1234
    assert decoded["token_source"] == "exact"
    assert decoded["tool_uses"] == 7


def test_validate_span_dispatch_attributes_all_optional() -> None:
    """Every extended-schema field is optional — an entirely absent
    `attributes` dict on a dispatch span is well-formed, never raises."""
    clean = spans.validate_span({"trace_id": "t1", "span_id": "s1", "name": "dispatch:x", "kind": "dispatch"})
    assert clean["attributes"] == "{}"


@pytest.mark.parametrize("field", ["task_id", "workflow_id", "persona", "model", "task_tier", "marker"])
def test_validate_span_rejects_non_string_dispatch_str_attrs(field: str) -> None:
    with pytest.raises(spans.SpanValidationError):
        spans.validate_span(
            {
                "trace_id": "t1", "span_id": "s1", "name": "dispatch:x", "kind": "dispatch",
                "attributes": {field: 42},
            }
        )


@pytest.mark.parametrize("field", ["task_id", "workflow_id", "persona", "model", "task_tier", "marker"])
def test_validate_span_rejects_empty_string_dispatch_str_attrs(field: str) -> None:
    with pytest.raises(spans.SpanValidationError):
        spans.validate_span(
            {
                "trace_id": "t1", "span_id": "s1", "name": "dispatch:x", "kind": "dispatch",
                "attributes": {field: ""},
            }
        )


@pytest.mark.parametrize("field", ["tokens", "tool_uses"])
def test_validate_span_rejects_non_numeric_dispatch_numeric_attrs(field: str) -> None:
    with pytest.raises(spans.SpanValidationError):
        spans.validate_span(
            {
                "trace_id": "t1", "span_id": "s1", "name": "dispatch:x", "kind": "dispatch",
                "attributes": {field: "not-a-number"},
            }
        )


@pytest.mark.parametrize("field", ["tokens", "tool_uses"])
def test_validate_span_rejects_bool_for_dispatch_numeric_attrs(field: str) -> None:
    """`bool` is an `int` subclass in Python — the same guard `_optional_number`
    already applies to top-level numeric fields must also reject it here."""
    with pytest.raises(spans.SpanValidationError):
        spans.validate_span(
            {
                "trace_id": "t1", "span_id": "s1", "name": "dispatch:x", "kind": "dispatch",
                "attributes": {field: True},
            }
        )


def test_validate_span_rejects_unknown_token_source() -> None:
    with pytest.raises(spans.SpanValidationError):
        spans.validate_span(
            {
                "trace_id": "t1", "span_id": "s1", "name": "dispatch:x", "kind": "dispatch",
                "attributes": {"token_source": "bogus"},
            }
        )


@pytest.mark.parametrize("token_source", sorted(spans.VALID_TOKEN_SOURCES))
def test_validate_span_accepts_every_valid_token_source(token_source: str) -> None:
    clean = spans.validate_span(
        {
            "trace_id": "t1", "span_id": "s1", "name": "dispatch:x", "kind": "dispatch",
            "attributes": {"token_source": token_source},
        }
    )
    assert json.loads(clean["attributes"])["token_source"] == token_source


def test_validate_span_dispatch_schema_not_enforced_on_gate_or_leg_spans() -> None:
    """FDEC-8 dual-sink model (wtcs.py docstring point 2): only dispatch
    spans carry harness-telemetry-shaped attributes — a gate/leg span with a
    "malformed" dispatch-shaped field (e.g. a non-string `marker`) is NOT
    held to `validate_dispatch_attributes` at all."""
    clean = spans.validate_span(
        {
            "trace_id": "t1", "span_id": "s1", "name": "gate:x", "kind": "gate",
            "attributes": {"marker": 42, "tokens": "not-a-number"},
        }
    )
    decoded = json.loads(clean["attributes"])
    assert decoded["marker"] == 42
    assert decoded["tokens"] == "not-a-number"


# ── SpanStore — real DuckDB file, never mocked ──────────────────────────────


def test_span_store_persists_a_row_queryable_by_trace_id(tmp_path: Path) -> None:
    store = spans.SpanStore(tmp_path / "spans.duckdb")
    try:
        store.record({"trace_id": "t1", "span_id": "s1", "name": "dispatch:x", "kind": "dispatch"})
        assert store.span_count == 1
        assert store.count() == 1
        rows = store.query_trace("t1")
        assert len(rows) == 1
        assert rows[0]["span_id"] == "s1"
        assert rows[0]["kind"] == "dispatch"
    finally:
        store.close()


def test_span_store_rejects_malformed_span_without_writing_a_row(tmp_path: Path) -> None:
    store = spans.SpanStore(tmp_path / "spans.duckdb")
    try:
        with pytest.raises(spans.SpanValidationError):
            store.record({"trace_id": "t1"})  # missing span_id/name/kind
        assert store.span_count == 0
        assert store.count() == 0
    finally:
        store.close()


def test_span_store_proves_parent_child_linkage_dispatch_gate_leg(tmp_path: Path) -> None:
    """The `trace(session) > span(dispatch|gate|leg)` shape FDEC-8 /
    wave-2.md §(e) requires — this is the exact query `span_smoke.py` runs
    against a real spawned daemon, replayed here in-process against a real
    DuckDB file (never mocked)."""
    store = spans.SpanStore(tmp_path / "spans.duckdb")
    try:
        store.record({"trace_id": "t1", "span_id": "d1", "name": "dispatch", "kind": "dispatch"})
        store.record({"trace_id": "t1", "span_id": "g1", "parent_span_id": "d1", "name": "gate", "kind": "gate"})
        store.record({"trace_id": "t1", "span_id": "l1", "parent_span_id": "g1", "name": "leg", "kind": "leg"})

        rows = store.query_trace("t1")
        by_id = {row["span_id"]: row["parent_span_id"] for row in rows}
        assert len(rows) == 3
        assert by_id["d1"] is None
        assert by_id["g1"] == "d1"
        assert by_id["l1"] == "g1"
    finally:
        store.close()


def test_span_store_is_the_single_open_writer_second_process_fails_loud(tmp_path: Path) -> None:
    """Structural single-writer proof (ADR-001) — not just documented,
    DuckDB itself enforces it ACROSS PROCESSES: a second, genuinely separate
    process opening the SAME file for write while this one holds it open
    fails loud (verified empirically before writing this test). Within a
    single process DuckDB caches one shared engine instance per absolute
    path, so a same-process second `connect()` call would NOT exercise the
    real guard — it must be a real subprocess, mirroring the actual
    single-daemon-writer scenario this protects."""
    db_path = tmp_path / "spans.duckdb"
    store = spans.SpanStore(db_path)
    try:
        proc = subprocess.run(
            [sys.executable, "-c", "import duckdb, sys; duckdb.connect(sys.argv[1])", str(db_path)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert proc.returncode != 0
        assert "IOException" in proc.stderr or "lock" in proc.stderr.lower()
    finally:
        store.close()


# ── event_bus.handle_span_emit — the daemon RPC write boundary ─────────────


@pytest.fixture()
def eventbus_project(tmp_path) -> Path:
    project = tmp_path / "proj"
    (project / ".memory").mkdir(parents=True)
    return project


def test_handle_span_emit_writes_a_real_duckdb_row(eventbus_project: Path) -> None:
    state = event_bus.EventBusState(eventbus_project)
    result = event_bus.handle_span_emit(
        state, {"span": {"trace_id": "t1", "span_id": "s1", "name": "dispatch:x", "kind": "dispatch"}}
    )
    assert result["accepted"] is True
    assert result["trace_id"] == "t1"
    assert result["span_id"] == "s1"
    assert state.span_count == 1

    db_path = spans.spans_db_path_for(eventbus_project)
    assert db_path.is_file()
    state.close_span_store()
    conn = duckdb.connect(str(db_path), read_only=True)
    try:
        rows = conn.execute("SELECT trace_id, span_id FROM spans").fetchall()
    finally:
        conn.close()
    assert rows == [("t1", "s1")]


def test_handle_span_emit_accepts_and_counts(eventbus_project: Path) -> None:
    state = event_bus.EventBusState(eventbus_project)
    r1 = event_bus.handle_span_emit(
        state, {"span": {"trace_id": "t1", "span_id": "s1", "name": "dispatch", "kind": "dispatch"}}
    )
    assert r1["accepted"] is True
    r2 = event_bus.handle_span_emit(
        state, {"span": {"trace_id": "t1", "span_id": "s2", "parent_span_id": "s1", "name": "gate", "kind": "gate"}}
    )
    assert r2["accepted"] is True
    assert state.span_count == 2


def test_handle_span_emit_rejects_malformed_span_typed_error_state_stays_usable(eventbus_project: Path) -> None:
    state = event_bus.EventBusState(eventbus_project)
    with pytest.raises(spans.SpanValidationError):
        event_bus.handle_span_emit(state, {"span": {"trace_id": "t1"}})  # missing span_id/name/kind
    assert state.span_count == 0

    # the daemon-facing contract: a rejected span never corrupts state — a
    # well-formed call right after still works.
    result = event_bus.handle_span_emit(
        state, {"span": {"trace_id": "t1", "span_id": "s1", "name": "dispatch:x", "kind": "dispatch"}}
    )
    assert result["accepted"] is True
    assert state.span_count == 1


def test_handle_span_emit_requires_span_dict(eventbus_project: Path) -> None:
    state = event_bus.EventBusState(eventbus_project)
    with pytest.raises(ValueError, match="span:dict"):
        event_bus.handle_span_emit(state, {})


# ── TASK-093 stage 3: record_telemetry RPC bridges dispatch_telemetry rows
# into durable spans (server._emit_dispatch_span_from_telemetry, wired into
# handle_request's "record_telemetry" branch) — closes the "built but never
# called" gap the f6043f3/e1e37f3 module docstring documents, at the one
# daemon-RPC entry point every telemetry write can reach.


def test_record_telemetry_rpc_bridges_dispatch_row_into_a_durable_span(eventbus_project: Path) -> None:
    state = DaemonState(eventbus_project)
    result = handle_request(
        state,
        "record_telemetry",
        {
            "table": "dispatch_telemetry",
            "row": {
                "session_id": "sess-bridge",
                "dispatch_id": "disp-1",
                "persona": "hermes",
                "model": "sonnet",
                "task_id": "TASK-093",
                "marker": "DONE",
                "tokens": 1234,
                "token_source": "exact",
                "tool_uses": 3,
            },
        },
    )
    assert result["accepted"] is True

    rows = state.event_bus.span_store.query_trace("sess-bridge")
    assert len(rows) == 1
    row = rows[0]
    assert row["span_id"] == "disp-1"
    assert row["kind"] == "dispatch"
    assert row["status"] == "OK"  # marker DONE -> span status OK
    assert row["name"] == "dispatch:hermes"
    attrs = json.loads(row["attributes"])
    assert attrs["task_id"] == "TASK-093"
    assert attrs["persona"] == "hermes"
    assert attrs["model"] == "sonnet"
    assert attrs["marker"] == "DONE"
    assert attrs["tokens"] == 1234
    assert attrs["token_source"] == "exact"
    assert attrs["tool_uses"] == 3


def test_record_telemetry_rpc_generates_a_span_id_when_dispatch_id_absent(eventbus_project: Path) -> None:
    state = DaemonState(eventbus_project)
    handle_request(
        state,
        "record_telemetry",
        {"table": "dispatch_telemetry", "row": {"session_id": "sess-noid", "persona": "scout"}},
    )
    rows = state.event_bus.span_store.query_trace("sess-noid")
    assert len(rows) == 1
    assert rows[0]["span_id"].startswith("span-")
    assert rows[0]["status"] == "UNSET"  # no marker supplied -> never guessed into OK/ERROR


def test_record_telemetry_rpc_never_emits_a_span_without_a_session_id(eventbus_project: Path) -> None:
    """Missing/empty session_id is a silent no-op — `spans.trace_id` is
    REQUIRED non-empty, and synthesizing a fake one would misrepresent
    unrelated dispatches as belonging to the same trace (server.py's
    `_emit_dispatch_span_from_telemetry` docstring)."""
    state = DaemonState(eventbus_project)
    result = handle_request(
        state,
        "record_telemetry",
        {"table": "dispatch_telemetry", "row": {"persona": "scout", "marker": "DONE"}},
    )
    assert result["accepted"] is True
    assert state.event_bus.span_count == 0


def test_record_telemetry_rpc_ignores_non_dispatch_telemetry_tables(eventbus_project: Path) -> None:
    state = DaemonState(eventbus_project)
    handle_request(
        state,
        "record_telemetry",
        {
            "table": "agent_activity",
            "row": {"agent": "pipeline-async", "started": "now", "session_id": "sess-x"},
        },
    )
    assert state.event_bus.span_count == 0


def test_record_telemetry_rpc_swallows_a_span_validation_failure_never_fails_the_telemetry_write(
    eventbus_project: Path,
) -> None:
    """A `dispatch_telemetry` row that trips `spans.SpanValidationError` (here:
    a non-numeric `tokens`) must never fail the RPC nor drop the telemetry
    write it rode in on — only the best-effort span bridge is skipped
    (`contextlib.suppress(Exception)` around the bridge call in
    `handle_request`'s `record_telemetry` branch)."""
    state = DaemonState(eventbus_project)
    result = handle_request(
        state,
        "record_telemetry",
        {
            "table": "dispatch_telemetry",
            "row": {"session_id": "sess-bad", "persona": "scout", "tokens": "not-a-number"},
        },
    )
    assert result["accepted"] is True
    assert state.telemetry.pending_count() == 1  # the telemetry write itself still landed
    assert state.event_bus.span_count == 0  # span bridge silently skipped, never raised


# ── TASK-094 — extended span kinds (session/workflow/phase/tool_call) ──────
# `observability-taxonomy.json` levels: session_trace > workflow > phase >
# dispatch > leg > gate_fire > tool_call.


@pytest.mark.parametrize("kind", ["session", "workflow", "phase", "tool_call"])
def test_validate_span_accepts_every_task_094_extended_kind(kind: str) -> None:
    clean = spans.validate_span({"trace_id": "t1", "span_id": "s1", "name": f"{kind}:x", "kind": kind})
    assert clean["kind"] == kind


def test_validate_span_still_rejects_an_unknown_kind() -> None:
    with pytest.raises(spans.SpanValidationError):
        spans.validate_span({"trace_id": "t1", "span_id": "s1", "name": "x", "kind": "not-a-real-kind"})


def test_span_store_persists_a_span_of_every_task_094_extended_kind(tmp_path: Path) -> None:
    store = spans.SpanStore(tmp_path / "spans.duckdb")
    try:
        for kind in ("session", "workflow", "phase", "tool_call"):
            store.record({"trace_id": "t1", "span_id": f"s-{kind}", "name": kind, "kind": kind})
        rows = store.query_trace("t1")
        assert {row["kind"] for row in rows} == {"session", "workflow", "phase", "tool_call"}
    finally:
        store.close()


# ── TASK-094 — first-class workflow_id/phase_id/task_id span keys ─────────
# core-move #3 (observability-taxonomy.json): real top-level columns, not
# just JSON-buried `attributes` fields, and apply to EVERY kind.


def test_validate_span_extracts_first_class_keys_for_any_kind() -> None:
    clean = spans.validate_span(
        {
            "trace_id": "t1", "span_id": "s1", "name": "phase:implement", "kind": "phase",
            "workflow_id": "wf-1", "phase_id": "ph-1", "task_id": "TASK-094",
        }
    )
    assert clean["workflow_id"] == "wf-1"
    assert clean["phase_id"] == "ph-1"
    assert clean["task_id"] == "TASK-094"


def test_validate_span_first_class_keys_default_to_none_when_absent() -> None:
    clean = spans.validate_span({"trace_id": "t1", "span_id": "s1", "name": "leg:x", "kind": "leg"})
    assert clean["workflow_id"] is None
    assert clean["phase_id"] is None
    assert clean["task_id"] is None


@pytest.mark.parametrize("field", ["workflow_id", "phase_id", "task_id"])
def test_validate_span_rejects_non_string_first_class_key(field: str) -> None:
    with pytest.raises(spans.SpanValidationError):
        spans.validate_span({"trace_id": "t1", "span_id": "s1", "name": "x", "kind": "leg", field: 42})


def test_span_store_persists_first_class_keys_as_queryable_columns_not_only_json(tmp_path: Path) -> None:
    store = spans.SpanStore(tmp_path / "spans.duckdb")
    try:
        store.record(
            {
                "trace_id": "t1", "span_id": "d1", "name": "dispatch:hermes", "kind": "dispatch",
                "workflow_id": "wf-1", "phase_id": "ph-1", "task_id": "TASK-094",
            }
        )
        # queryable straight off the native column, no json_extract needed —
        # the exact "first-class join key" acceptance criterion.
        row = store._conn.execute(
            "SELECT workflow_id, phase_id, task_id FROM spans WHERE span_id = 'd1'"
        ).fetchone()
        assert row == ("wf-1", "ph-1", "TASK-094")
        rows = store.query_trace("t1")
        assert rows[0]["workflow_id"] == "wf-1"
        assert rows[0]["phase_id"] == "ph-1"
        assert rows[0]["task_id"] == "TASK-094"
    finally:
        store.close()


def test_span_store_additive_migration_backfills_first_class_columns_on_a_pre_task_094_file(
    tmp_path: Path,
) -> None:
    """A `spans.duckdb` file created before TASK-094 (no workflow_id/phase_id/
    task_id columns) must gain them — additively, with existing rows intact —
    the first time `SpanStore` opens it post-upgrade."""
    db_path = tmp_path / "spans.duckdb"
    pre_existing_ddl = """
    CREATE TABLE spans (
        trace_id VARCHAR NOT NULL,
        span_id VARCHAR NOT NULL,
        parent_span_id VARCHAR,
        name VARCHAR NOT NULL,
        kind VARCHAR NOT NULL,
        status VARCHAR NOT NULL,
        start_time VARCHAR NOT NULL,
        end_time VARCHAR,
        duration_ms DOUBLE,
        tokens DOUBLE,
        attributes VARCHAR NOT NULL,
        recorded_at VARCHAR NOT NULL,
        PRIMARY KEY (trace_id, span_id)
    )
    """
    conn = duckdb.connect(str(db_path))
    conn.execute(pre_existing_ddl)
    conn.execute(
        "INSERT INTO spans VALUES ('t1', 'pre1', NULL, 'pre-existing', 'dispatch', 'OK', "
        "'2026-01-01T00:00:00Z', NULL, 1.0, 5.0, '{}', '2026-01-01T00:00:00Z')"
    )
    conn.close()

    store = spans.SpanStore(db_path)
    try:
        rows = store.query_trace("t1")
        assert len(rows) == 1
        assert rows[0]["span_id"] == "pre1"
        assert rows[0]["workflow_id"] is None  # pre-existing row, column backfilled NULL, never dropped

        store.record({"trace_id": "t1", "span_id": "post1", "name": "x", "kind": "leg", "workflow_id": "wf-new"})
        rows = store.query_trace("t1")
        assert len(rows) == 2
        by_id = {row["span_id"]: row["workflow_id"] for row in rows}
        assert by_id["pre1"] is None
        assert by_id["post1"] == "wf-new"
    finally:
        store.close()


# ── TASK-094 — uncollapsed harness usage block (split tokens) ─────────────


def test_validate_span_accepts_split_harness_token_fields_on_dispatch_attrs() -> None:
    clean = spans.validate_span(
        {
            "trace_id": "t1", "span_id": "s1", "name": "dispatch:x", "kind": "dispatch",
            "attributes": {
                "tokens_in": 100, "tokens_out": 50, "tokens_cache_read": 10,
                "tokens_cache_creation": 5, "token_source": "exact",
            },
        }
    )
    decoded = json.loads(clean["attributes"])
    assert decoded["tokens_in"] == 100
    assert decoded["tokens_out"] == 50
    assert decoded["tokens_cache_read"] == 10
    assert decoded["tokens_cache_creation"] == 5
    assert decoded["token_source"] == "exact"


@pytest.mark.parametrize(
    "field", ["tokens_in", "tokens_out", "tokens_cache_read", "tokens_cache_creation"]
)
def test_validate_span_rejects_non_numeric_split_token_field(field: str) -> None:
    with pytest.raises(spans.SpanValidationError):
        spans.validate_span(
            {
                "trace_id": "t1", "span_id": "s1", "name": "dispatch:x", "kind": "dispatch",
                "attributes": {field: "not-a-number"},
            }
        )


def test_validate_span_does_not_extend_the_token_source_enum() -> None:
    """DEC-092 — split harness counts reuse the existing 'exact' value; the
    enum itself must stay exactly {'exact', 'approx'}."""
    assert spans.VALID_TOKEN_SOURCES == frozenset({"exact", "approx"})
    with pytest.raises(spans.SpanValidationError):
        spans.validate_span(
            {
                "trace_id": "t1", "span_id": "s1", "name": "dispatch:x", "kind": "dispatch",
                "attributes": {"token_source": "harness_exact"},
            }
        )


# ── TASK-094 — dispatch level: phase_id, error_class, revise_reasons ──────


def test_validate_span_accepts_dispatch_phase_id_error_class_revise_reasons() -> None:
    clean = spans.validate_span(
        {
            "trace_id": "t1", "span_id": "s1", "name": "dispatch:x", "kind": "dispatch",
            "attributes": {
                "phase_id": "ph-1",
                "error_class": "timeout",
                "revise_reasons": ["missing test coverage", "wrong verdict tier"],
            },
        }
    )
    decoded = json.loads(clean["attributes"])
    assert decoded["phase_id"] == "ph-1"
    assert decoded["error_class"] == "timeout"
    assert decoded["revise_reasons"] == ["missing test coverage", "wrong verdict tier"]


def test_validate_span_rejects_non_list_revise_reasons_on_dispatch() -> None:
    with pytest.raises(spans.SpanValidationError):
        spans.validate_span(
            {
                "trace_id": "t1", "span_id": "s1", "name": "dispatch:x", "kind": "dispatch",
                "attributes": {"revise_reasons": "not-a-list"},
            }
        )


def test_validate_span_rejects_revise_reasons_with_a_non_string_item_on_dispatch() -> None:
    with pytest.raises(spans.SpanValidationError):
        spans.validate_span(
            {
                "trace_id": "t1", "span_id": "s1", "name": "dispatch:x", "kind": "dispatch",
                "attributes": {"revise_reasons": ["ok", 42]},
            }
        )


def test_validate_span_rejects_non_string_error_class_on_dispatch() -> None:
    with pytest.raises(spans.SpanValidationError):
        spans.validate_span(
            {
                "trace_id": "t1", "span_id": "s1", "name": "dispatch:x", "kind": "dispatch",
                "attributes": {"error_class": 42},
            }
        )


# ── TASK-094 — gate_fire level: rpc_miss, rpc_latency_ms, revise_reasons ──


def test_validate_span_accepts_gate_fire_rpc_attributes() -> None:
    clean = spans.validate_span(
        {
            "trace_id": "t1", "span_id": "g1", "name": "gate:broker-gate", "kind": "gate",
            "attributes": {
                "rpc_miss": True, "rpc_latency_ms": 12.5, "revise_reasons": ["stale brief"],
            },
        }
    )
    decoded = json.loads(clean["attributes"])
    assert decoded["rpc_miss"] is True
    assert decoded["rpc_latency_ms"] == 12.5
    assert decoded["revise_reasons"] == ["stale brief"]


def test_validate_span_accepts_gate_fire_rpc_miss_false() -> None:
    clean = spans.validate_span(
        {
            "trace_id": "t1", "span_id": "g1", "name": "gate:x", "kind": "gate",
            "attributes": {"rpc_miss": False},
        }
    )
    assert json.loads(clean["attributes"])["rpc_miss"] is False


def test_validate_span_rejects_non_bool_rpc_miss_on_gate() -> None:
    with pytest.raises(spans.SpanValidationError):
        spans.validate_span(
            {
                "trace_id": "t1", "span_id": "g1", "name": "gate:x", "kind": "gate",
                "attributes": {"rpc_miss": "yes"},
            }
        )


def test_validate_span_rejects_non_numeric_rpc_latency_ms_on_gate() -> None:
    with pytest.raises(spans.SpanValidationError):
        spans.validate_span(
            {
                "trace_id": "t1", "span_id": "g1", "name": "gate:x", "kind": "gate",
                "attributes": {"rpc_latency_ms": "slow"},
            }
        )


def test_validate_span_rejects_non_list_revise_reasons_on_gate() -> None:
    with pytest.raises(spans.SpanValidationError):
        spans.validate_span(
            {
                "trace_id": "t1", "span_id": "g1", "name": "gate:x", "kind": "gate",
                "attributes": {"revise_reasons": "not-a-list"},
            }
        )


def test_validate_span_gate_schema_leaves_unrelated_attributes_unvalidated() -> None:
    """Only the TASK-094 gate_fire fields (`rpc_miss`/`rpc_latency_ms`/
    `revise_reasons`) are shape-checked — every pre-existing free-form gate
    attribute (`gate_name`, `verdict`, ...) stays unvalidated, unchanged from
    pre-TASK-094 behavior."""
    clean = spans.validate_span(
        {
            "trace_id": "t1", "span_id": "g1", "name": "gate:x", "kind": "gate",
            "attributes": {"gate_name": 42, "verdict": ["weird", "shape"]},
        }
    )
    decoded = json.loads(clean["attributes"])
    assert decoded["gate_name"] == 42
    assert decoded["verdict"] == ["weird", "shape"]


# ── TASK-094 — tool_call level: schema + writer support ────────────────────


def test_validate_span_accepts_a_well_formed_tool_call_span() -> None:
    clean = spans.validate_span(
        {
            "trace_id": "t1", "span_id": "tc1", "parent_span_id": "d1", "name": "tool_call:Read",
            "kind": "tool_call", "duration_ms": 12.0, "status": "OK",
            "attributes": {
                "tool_name": "Read", "tool_status": "OK", "error_class": None,
                "rpc_miss": False, "consecutive_read_count": 3,
            },
        }
    )
    decoded = json.loads(clean["attributes"])
    assert decoded["tool_name"] == "Read"
    assert decoded["tool_status"] == "OK"
    assert decoded["rpc_miss"] is False
    assert decoded["consecutive_read_count"] == 3


def test_validate_span_rejects_non_string_tool_name() -> None:
    with pytest.raises(spans.SpanValidationError):
        spans.validate_span(
            {
                "trace_id": "t1", "span_id": "tc1", "name": "tool_call:x", "kind": "tool_call",
                "attributes": {"tool_name": 42},
            }
        )


def test_validate_span_rejects_unknown_tool_status() -> None:
    with pytest.raises(spans.SpanValidationError):
        spans.validate_span(
            {
                "trace_id": "t1", "span_id": "tc1", "name": "tool_call:x", "kind": "tool_call",
                "attributes": {"tool_status": "MAYBE"},
            }
        )


def test_validate_span_rejects_non_bool_rpc_miss_on_tool_call() -> None:
    with pytest.raises(spans.SpanValidationError):
        spans.validate_span(
            {
                "trace_id": "t1", "span_id": "tc1", "name": "tool_call:x", "kind": "tool_call",
                "attributes": {"rpc_miss": 1},
            }
        )


def test_validate_span_rejects_non_numeric_consecutive_read_count() -> None:
    with pytest.raises(spans.SpanValidationError):
        spans.validate_span(
            {
                "trace_id": "t1", "span_id": "tc1", "name": "tool_call:x", "kind": "tool_call",
                "attributes": {"consecutive_read_count": "many"},
            }
        )


def test_validate_span_tool_call_attributes_all_optional() -> None:
    clean = spans.validate_span({"trace_id": "t1", "span_id": "tc1", "name": "tool_call:x", "kind": "tool_call"})
    assert clean["attributes"] == "{}"


def test_span_store_persists_a_tool_call_span_chained_to_its_parent_dispatch(tmp_path: Path) -> None:
    store = spans.SpanStore(tmp_path / "spans.duckdb")
    try:
        store.record({"trace_id": "t1", "span_id": "d1", "name": "dispatch:x", "kind": "dispatch"})
        store.record(
            {
                "trace_id": "t1", "span_id": "tc1", "parent_span_id": "d1", "name": "tool_call:Read",
                "kind": "tool_call", "attributes": {"tool_name": "Read", "tool_status": "OK"},
            }
        )
        rows = store.query_trace("t1")
        by_id = {row["span_id"]: row for row in rows}
        assert by_id["tc1"]["parent_span_id"] == "d1"
        assert by_id["tc1"]["kind"] == "tool_call"
    finally:
        store.close()


def test_handle_span_emit_writes_a_tool_call_span_via_the_rpc_surface(eventbus_project: Path) -> None:
    state = event_bus.EventBusState(eventbus_project)
    result = event_bus.handle_span_emit(
        state,
        {
            "span": {
                "trace_id": "t1", "span_id": "tc1", "parent_span_id": "d1", "name": "tool_call:Bash",
                "kind": "tool_call", "attributes": {"tool_name": "Bash", "tool_status": "ERROR", "rpc_miss": True},
            }
        },
    )
    assert result["accepted"] is True
    assert state.span_count == 1


# ── TASK-094 — record_telemetry bridge propagates the new dispatch fields ─


def test_record_telemetry_rpc_bridge_propagates_workflow_phase_and_split_tokens(eventbus_project: Path) -> None:
    state = DaemonState(eventbus_project)
    handle_request(
        state,
        "record_telemetry",
        {
            "table": "dispatch_telemetry",
            "row": {
                "session_id": "sess-wf",
                "dispatch_id": "disp-wf",
                "persona": "pipeline-async",
                "marker": "DONE",
                "workflow_id": "wf-1",
                "phase_id": "ph-1",
                "task_id": "TASK-094",
                "tokens_in": 200,
                "tokens_out": 80,
                "tokens_cache_read": 30,
                "tokens_cache_creation": 4,
                "token_source": "exact",
                "error_class": None,
                "revise_reasons": None,
            },
        },
    )
    rows = state.event_bus.span_store.query_trace("sess-wf")
    assert len(rows) == 1
    row = rows[0]
    assert row["workflow_id"] == "wf-1"
    assert row["phase_id"] == "ph-1"
    assert row["task_id"] == "TASK-094"
    attrs = json.loads(row["attributes"])
    assert attrs["tokens_in"] == 200
    assert attrs["tokens_out"] == 80
    assert attrs["tokens_cache_read"] == 30
    assert attrs["tokens_cache_creation"] == 4
    assert attrs["token_source"] == "exact"
