"""Tests for the event-sourced skill-load recorder — plans/08 §3.6 (node N20).

Covers exactly this node's acceptance criteria: observed skill-load events
land as `skill_load_events` rows via the daemon's existing 1.5 batch-flush
path (asserted against a fixture DB), a `kill -9` mid-batch loses at most
the unflushed batch while `project.db` stays uncorrupted (a real subprocess
drill, mirroring `test_telemetry_write_through_survives_kill_minus_9` in
`test_daemon_pilot.py` but exercised through this module's own
`SkillLoadRecorder` front door), and the recorder's identity/timestamp
contract (caller supplies WHAT, never WHEN).
"""
from __future__ import annotations

import calendar
import contextlib
import os
import signal
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

from broker.daemon.skill_load_recorder import SkillLoadRecorder
from broker.daemon.telemetry_store import TelemetryStore

BROKER_ROOT = Path(__file__).resolve().parent.parent  # nexus-broker/

# Exact R2-T15 shape (.memory/schema.sql) — this node ships no schema change,
# so the fixture DB below must match the real table byte-for-byte in columns.
SKILL_LOAD_EVENTS_SQL = """
CREATE TABLE skill_load_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    dispatch_id TEXT NOT NULL,
    skill_id    TEXT NOT NULL,
    ts          TEXT NOT NULL,
    byte_len    INTEGER,
    recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


def _make_fixture_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.executescript(SKILL_LOAD_EVENTS_SQL)
        conn.commit()
    finally:
        conn.close()


@pytest.fixture()
def fixture_db(tmp_path) -> Path:
    db_path = tmp_path / "project.db"
    _make_fixture_db(db_path)
    return db_path


# ── identity/timestamp contract — caller supplies WHAT, never WHEN ─────────


def test_record_observed_buffers_expected_columns() -> None:
    store = TelemetryStore()
    recorder = SkillLoadRecorder(store)

    row = recorder.record_observed("d-1", "agent-protocol", byte_len=1024)

    assert row["dispatch_id"] == "d-1"
    assert row["skill_id"] == "agent-protocol"
    assert row["byte_len"] == 1024
    assert "ts" in row
    assert store.pending_count() == 1
    assert recorder.events_recorded == 1


def test_record_observed_ts_is_own_clock_not_caller_suppliable() -> None:
    """The public signature has no `ts` parameter at all — a caller cannot
    claim an event happened at a time it did not; the recorder's own
    wall-clock read is the only source of `ts`.
    """
    store = TelemetryStore()
    recorder = SkillLoadRecorder(store)

    before = time.time()
    row = recorder.record_observed("d-1", "agent-protocol")
    after = time.time()

    # ISO-8601 UTC, second precision, matching the module's documented shape.
    # calendar.timegm (not time.mktime) interprets the struct as UTC directly
    # — mktime would apply the local zone/DST offset and corrupt the check.
    observed = time.strptime(row["ts"], "%Y-%m-%dT%H:%M:%SZ")
    observed_epoch = calendar.timegm(observed)
    assert before - 2 <= observed_epoch <= after + 2

    with pytest.raises(TypeError):
        recorder.record_observed("d-1", "agent-protocol", ts="2000-01-01T00:00:00Z")  # type: ignore[call-arg]


def test_record_observed_byte_len_omitted_when_not_given() -> None:
    store = TelemetryStore()
    recorder = SkillLoadRecorder(store)

    row = recorder.record_observed("d-1", "agent-protocol")

    assert "byte_len" not in row


@pytest.mark.parametrize(
    "dispatch_id,skill_id",
    [
        ("", "agent-protocol"),
        ("   ", "agent-protocol"),
        ("d-1", ""),
        ("d-1", "   "),
        (None, "agent-protocol"),
        ("d-1", None),
    ],
)
def test_record_observed_rejects_malformed_identity(dispatch_id, skill_id) -> None:
    """An 'observed' event with no real identity is noise, not a signal —
    it must never reach the pending batch, let alone project.db.
    """
    store = TelemetryStore()
    recorder = SkillLoadRecorder(store)

    with pytest.raises(ValueError):
        recorder.record_observed(dispatch_id, skill_id)

    assert store.pending_count() == 0
    assert recorder.events_recorded == 0


# ── AC-1: observed events land as skill_load_events rows via batch flush ───


def test_flush_writes_observed_rows_into_fixture_db(fixture_db) -> None:
    store = TelemetryStore()
    recorder = SkillLoadRecorder(store)

    recorder.record_observed("d-100", "agent-protocol")
    recorder.record_observed("d-100", "deployable-engineering", byte_len=2048)
    recorder.record_observed("d-101", "tdd-core")

    flushed = store.flush(fixture_db)
    assert flushed == 3
    assert store.pending_count() == 0

    conn = sqlite3.connect(fixture_db)
    try:
        rows = conn.execute(
            "SELECT dispatch_id, skill_id, byte_len FROM skill_load_events ORDER BY id"
        ).fetchall()
    finally:
        conn.close()

    assert rows == [
        ("d-100", "agent-protocol", None),
        ("d-100", "deployable-engineering", 2048),
        ("d-101", "tdd-core", None),
    ]


def test_flush_is_n_per_dispatch_not_collapsed(fixture_db) -> None:
    """Three observed loads under ONE dispatch_id must persist as three
    independent rows (event-sourced, N-per-dispatch), never collapsed or
    deduplicated by the recorder/store.
    """
    store = TelemetryStore()
    recorder = SkillLoadRecorder(store)

    for skill in ("agent-protocol", "deployable-engineering", "tdd-core"):
        recorder.record_observed("d-200", skill)
    store.flush(fixture_db)

    conn = sqlite3.connect(fixture_db)
    try:
        count = conn.execute(
            "SELECT COUNT(*) FROM skill_load_events WHERE dispatch_id='d-200'"
        ).fetchone()[0]
    finally:
        conn.close()
    assert count == 3


# ── AC-2: kill -9 mid-batch loses at most the unflushed batch ──────────────

_KILL_DRILL_SCRIPT = """
import time
from pathlib import Path

from broker.daemon.skill_load_recorder import SkillLoadRecorder
from broker.daemon.telemetry_store import TelemetryStore

db_path = Path(r"{db_path}")
ready_path = Path(r"{ready_path}")

store = TelemetryStore()
recorder = SkillLoadRecorder(store)

recorder.record_observed("d-flushed-1", "agent-protocol")
recorder.record_observed("d-flushed-1", "deployable-engineering", byte_len=512)
recorder.record_observed("d-flushed-2", "tdd-core")
store.flush(db_path)

# Signal the parent only AFTER the durable flush landed — the parent must
# never SIGKILL before this point, or the drill would prove nothing.
ready_path.write_text("ready")

# This batch is deliberately left unflushed when the parent kills us.
recorder.record_observed("d-pending-1", "never-should-land-a")
recorder.record_observed("d-pending-2", "never-should-land-b")

time.sleep(60)
"""


@pytest.mark.slow
def test_kill_minus_9_mid_batch_loses_only_unflushed_rows(tmp_path) -> None:
    db_path = tmp_path / "project.db"
    _make_fixture_db(db_path)
    ready_path = tmp_path / "ready.flag"
    script_path = tmp_path / "kill_drill.py"
    script_path.write_text(
        _KILL_DRILL_SCRIPT.format(db_path=str(db_path), ready_path=str(ready_path))
    )

    proc = subprocess.Popen(
        [sys.executable, str(script_path)],
        cwd=str(BROKER_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline and not ready_path.exists():
            if proc.poll() is not None:
                raise AssertionError(
                    f"drill process exited early (rc={proc.returncode}); "
                    f"stderr={proc.stderr.read().decode('utf-8', 'replace')}"
                )
            time.sleep(0.05)
        assert ready_path.exists(), "drill process never reached the post-flush ready signal"

        os.kill(proc.pid, signal.SIGKILL)

        # `proc` is a DIRECT child of this test process (unlike the daemon's
        # own double-fork-and-orphan-to-init spawn path) — a killed direct
        # child sits as a zombie, still visible to `os.kill(pid, 0)`, until
        # the parent reaps it. `Popen.wait()` is the correct reap-and-block
        # call here, not a kill(pid, 0) poll loop.
        try:
            proc.wait(timeout=15.0)
        except subprocess.TimeoutExpired:
            raise AssertionError("drill process did not die under SIGKILL") from None
    finally:
        if proc.poll() is None:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.wait(timeout=5)

    conn = sqlite3.connect(db_path)
    try:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        rows = conn.execute(
            "SELECT dispatch_id, skill_id, byte_len FROM skill_load_events ORDER BY id"
        ).fetchall()
    finally:
        conn.close()

    assert rows == [
        ("d-flushed-1", "agent-protocol", None),
        ("d-flushed-1", "deployable-engineering", 512),
        ("d-flushed-2", "tdd-core", None),
    ]
    dispatch_ids = {r[0] for r in rows}
    assert "d-pending-1" not in dispatch_ids
    assert "d-pending-2" not in dispatch_ids
