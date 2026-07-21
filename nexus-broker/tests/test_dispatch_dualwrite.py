"""F3-03 dispatch_telemetry dual-write (DEC-097 Option B) — the daemon
`record_telemetry` RPC seam (`broker.daemon.server`).

Arms the dual-write for the ONE genuinely daemon-resident hot-table write seam:
a `dispatch_telemetry` RPC row is written to `project.db` as today AND mirrored
as a `dispatch.completed` event (event_version=1) on the single-writer event log,
BOTH stamped from ONE `recorded_at` so the parity clock's (dispatch_id,
session_id, recorded_at) key lines up across both stores.

Acceptance surface:
  - SINGLE-TIMESTAMP: the flushed project.db row and the mirrored event carry
    the identical `recorded_at` (design §5.2 trap (a));
  - FAIL-OPEN: an event-log append failure never blocks or fails the primary
    telemetry write (project.db stays authoritative — plans/07 §1 constraint 1);
  - event_version=1 ONLY (the produce-time cited-verdict invariant is not yet
    live — TASK-073);
  - a non-dispatch telemetry table (agent_activity) is NEVER mirrored to the log.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from broker.daemon import event_store
from broker.daemon.server import DaemonState, handle_request

# Mirrors `.memory/schema.sql`'s dispatch_telemetry (incl. the recorded_at column
# the dual-write stamps) so the primary flush lands against a real table.
_DISPATCH_DDL = """
CREATE TABLE dispatch_telemetry (
    id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, dispatch_id TEXT,
    persona TEXT NOT NULL, model TEXT, task_id TEXT, marker TEXT, tokens INTEGER,
    token_source TEXT NOT NULL DEFAULT 'exact', tool_uses INTEGER, duration_ms INTEGER,
    run_context TEXT DEFAULT 'local', recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)
"""
_AGENT_ACTIVITY_DDL = """
CREATE TABLE agent_activity (
    id INTEGER PRIMARY KEY AUTOINCREMENT, agent TEXT, task TEXT, started TEXT,
    elapsed TEXT, status TEXT, current_action TEXT, session_id TEXT, updated_at TEXT)
"""


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    proj = tmp_path / "proj"
    (proj / ".memory").mkdir(parents=True)
    conn = sqlite3.connect(proj / ".memory" / "project.db")
    try:
        conn.execute(_DISPATCH_DDL)
        conn.execute(_AGENT_ACTIVITY_DDL)
        conn.commit()
    finally:
        conn.close()
    return proj


def _dispatch_row(**over: object) -> dict[str, object]:
    row: dict[str, object] = {
        "session_id": "s1", "dispatch_id": "d1", "persona": "pipeline-data",
        "marker": "DONE", "tokens": 100,
    }
    row.update(over)
    return row


def test_dualwrite_stamps_both_stores_from_one_timestamp(project: Path) -> None:
    """Trap (a): the flushed project.db row and the mirrored event carry the
    IDENTICAL recorded_at — one stamp, both stores."""
    state = DaemonState(project)
    try:
        result = handle_request(state, "record_telemetry",
                                {"table": "dispatch_telemetry", "row": _dispatch_row()})
        assert result["accepted"] is True
        assert handle_request(state, "flush_telemetry", {})["flushed"] == 1

        conn = sqlite3.connect(state.db_path)
        try:
            db_rows = conn.execute(
                "SELECT dispatch_id, session_id, persona, recorded_at FROM dispatch_telemetry"
            ).fetchall()
        finally:
            conn.close()
        assert len(db_rows) == 1
        db_dispatch_id, db_session_id, db_persona, db_recorded_at = db_rows[0]

        events = state.event_store.read_events()
        assert len(events) == 1
        ev = events[0]
        assert ev.event_type == "dispatch.completed"
        assert ev.aggregate_id == db_dispatch_id == "d1"
        assert ev.session_id == db_session_id == "s1"
        assert ev.payload["persona"] == db_persona == "pipeline-data"
        # THE single-timestamp assertion: identical recorded_at across both stores.
        assert ev.recorded_at == db_recorded_at
    finally:
        state.close_event_store()


def test_dualwrite_event_is_version_1_only(project: Path) -> None:
    """The mirrored dispatch event is stamped event_version=1 — never >=2, so a
    generated dual-write row can never trip the fold's post-invariant refusal."""
    state = DaemonState(project)
    try:
        handle_request(state, "record_telemetry",
                       {"table": "dispatch_telemetry", "row": _dispatch_row()})
        events = state.event_store.read_events()
        assert [e.event_version for e in events] == [1]
    finally:
        state.close_event_store()


def test_dualwrite_is_fail_open_event_store_error_never_blocks_primary(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FAIL-OPEN: an EventStore.append that raises must never fail the RPC nor
    drop the primary project.db telemetry write it rode in on."""
    def _boom(self: object, event: dict[str, object], *, recorded_at: str | None = None) -> None:
        raise RuntimeError("event log unavailable")

    monkeypatch.setattr(event_store.EventStore, "append", _boom)
    state = DaemonState(project)
    try:
        result = handle_request(state, "record_telemetry",
                                {"table": "dispatch_telemetry", "row": _dispatch_row()})
        assert result["accepted"] is True
        assert state.telemetry.pending_count() == 1  # primary write buffered despite the event-log failure
        assert state.event_store.event_count() == 0  # nothing landed in the log — append raised

        assert handle_request(state, "flush_telemetry", {})["flushed"] == 1
        conn = sqlite3.connect(state.db_path)
        try:
            rows = conn.execute("SELECT persona, tokens FROM dispatch_telemetry").fetchall()
        finally:
            conn.close()
        assert rows == [("pipeline-data", 100)]  # primary write is durable, source of truth intact
    finally:
        state.close_event_store()


def test_non_dispatch_table_is_never_mirrored_to_the_event_log(project: Path) -> None:
    """Only dispatch_telemetry is armed (DEC-097 Option B): agent_activity writes
    the primary row but appends NO event."""
    state = DaemonState(project)
    try:
        handle_request(state, "record_telemetry",
                       {"table": "agent_activity",
                        "row": {"agent": "pipeline-data", "started": "now", "status": "active"}})
        assert state.telemetry.pending_count() == 1
        assert state.event_store.event_count() == 0  # no dual-write for a non-dispatch table
    finally:
        state.close_event_store()


def test_dualwrite_preserves_caller_supplied_recorded_at(project: Path) -> None:
    """A caller that already stamped recorded_at (a replay/backfill) has it
    honored VERBATIM in both stores — the seam stamps only when absent."""
    state = DaemonState(project)
    try:
        stamp = "2026-07-17T09:08:07Z"
        handle_request(state, "record_telemetry",
                       {"table": "dispatch_telemetry", "row": _dispatch_row(recorded_at=stamp)})
        assert handle_request(state, "flush_telemetry", {})["flushed"] == 1

        conn = sqlite3.connect(state.db_path)
        try:
            db_recorded_at = conn.execute(
                "SELECT recorded_at FROM dispatch_telemetry"
            ).fetchone()[0]
        finally:
            conn.close()
        assert db_recorded_at == stamp
        assert state.event_store.read_events()[0].recorded_at == stamp
    finally:
        state.close_event_store()
