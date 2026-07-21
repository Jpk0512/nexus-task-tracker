"""F3-02 event-sourced store — append-only DuckDB event log + deterministic
projection replays, implementing `nexus-foundation/plans/artifacts/
event-store-model.json` (F3-01, atlas, lens PASS). ADR-001 Tier 2, single
writer (DEC-040 scar eliminated by construction).

Acceptance surface:
  - the event log is append-only, daemon-single-writer (a second process fails
    loud — DuckDB IOException), and idempotent on `event_id`;
  - the four brief-named hot projections (tasks, sessions, validation_log,
    dispatch_telemetry) plus the skill_load_events sibling fold deterministically
    from the log;
  - the cited-verdict invariant survives as a TOTAL fold with a first-class
    `evidence_backed` column and a version-scoped refusal (pre-invariant uncited
    PASS replays faithfully; a POST-invariant one fails loud) — the exact
    sequencing constraint (TASK-073 still todo);
  - THE replay-determinism contract: `replay(events)` twice → identical
    per-projection AND whole-store hashes, with a NON-VACUOUS negative proof
    (an impure re-stamping apply makes the two hashes differ);
  - projections are queryable off the materialised `proj_*` tables.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from broker.daemon import event_store
from broker.daemon.event_store import Event, EventStore

FIXED_TS = "2026-07-17T00:00:00Z"


def ev(
    seq: int,
    event_type: str,
    payload: dict,
    *,
    event_version: int = 1,
    recorded_at: str = FIXED_TS,
    aggregate_id: str = "agg",
    session_id: str | None = None,
) -> Event:
    """Build one in-memory `Event` (no DB) for the pure-fold tests."""
    return Event(
        seq=seq,
        event_id=f"evt-{seq}",
        event_type=event_type,
        event_version=event_version,
        aggregate_type=event_store.EVENT_TYPES[event_type],
        aggregate_id=aggregate_id,
        session_id=session_id,
        occurred_at=FIXED_TS,
        recorded_at=recorded_at,
        payload=payload,
    )


def _model_json() -> dict | None:
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "nexus-foundation" / "plans" / "artifacts" / "event-store-model.json"
        if candidate.is_file():
            return json.loads(candidate.read_text())
    return None


# ── event_log: append boundary + idempotency (model `event_log`) ───────────


def test_append_assigns_monotonic_seq_and_reads_back_ordered(tmp_path: Path) -> None:
    store = EventStore(event_store.events_db_path_for(tmp_path))
    try:
        a = store.append({"event_type": "task.created", "event_id": "e1", "aggregate_id": "TASK-1",
                          "payload": {"id": "TASK-1", "title": "one"}})
        b = store.append({"event_type": "task.created", "event_id": "e2", "aggregate_id": "TASK-2",
                          "payload": {"id": "TASK-2", "title": "two"}})
        assert (a.seq, b.seq) == (0, 1)
        assert store.event_count() == 2
        events = store.read_events()
        assert [e.seq for e in events] == [0, 1]
        assert [e.event_id for e in events] == ["e1", "e2"]
    finally:
        store.close()


def test_append_rejects_unknown_event_type_no_invented_events(tmp_path: Path) -> None:
    store = EventStore(event_store.events_db_path_for(tmp_path))
    try:
        with pytest.raises(event_store.EventValidationError):
            store.append({"event_type": "task.teleported", "event_id": "e1",
                          "aggregate_id": "TASK-1", "payload": {}})
        assert store.event_count() == 0
    finally:
        store.close()


def test_append_rejects_mismatched_aggregate_type(tmp_path: Path) -> None:
    store = EventStore(event_store.events_db_path_for(tmp_path))
    try:
        with pytest.raises(event_store.EventValidationError):
            store.append({"event_type": "task.created", "aggregate_type": "session",
                          "event_id": "e1", "aggregate_id": "TASK-1", "payload": {"id": "TASK-1"}})
    finally:
        store.close()


def test_append_is_idempotent_on_duplicate_event_id(tmp_path: Path) -> None:
    """model `replay.idempotency`: event_id UNIQUE dedupes a re-delivered
    event — a duplicate is a no-op returning the already-stored row, never a
    second append."""
    store = EventStore(event_store.events_db_path_for(tmp_path))
    try:
        first = store.append({"event_type": "task.created", "event_id": "dup", "aggregate_id": "TASK-1",
                              "payload": {"id": "TASK-1", "title": "one"}})
        again = store.append({"event_type": "task.created", "event_id": "dup", "aggregate_id": "TASK-1",
                              "payload": {"id": "TASK-1", "title": "one"}})
        assert first.seq == again.seq == 0
        assert store.event_count() == 1
    finally:
        store.close()


def test_seq_is_durable_across_reopen(tmp_path: Path) -> None:
    """seq is MAX(seq)+1 at boot (model `event_log.ordering`) — a reopened
    store continues the gap-free counter, never restarts at 0."""
    db = event_store.events_db_path_for(tmp_path)
    store = EventStore(db)
    store.append({"event_type": "task.created", "event_id": "e1", "aggregate_id": "TASK-1",
                  "payload": {"id": "TASK-1", "title": "one"}})
    store.close()
    reopened = EventStore(db)
    try:
        nxt = reopened.append({"event_type": "task.created", "event_id": "e2", "aggregate_id": "TASK-2",
                               "payload": {"id": "TASK-2", "title": "two"}})
        assert nxt.seq == 1
    finally:
        reopened.close()


def test_append_defaults_recorded_at_to_now(tmp_path: Path) -> None:
    """No override → append stamps `recorded_at` itself (append-time now), in
    the module's ISO-8601 `...Z` shape."""
    store = EventStore(event_store.events_db_path_for(tmp_path))
    try:
        e = store.append({"event_type": "dispatch.completed", "event_id": "e1",
                          "aggregate_id": "d1", "payload": {"dispatch_id": "d1", "persona": "atlas"}})
        assert e.recorded_at.endswith("Z") and "T" in e.recorded_at
    finally:
        store.close()


def test_append_stores_supplied_recorded_at_verbatim(tmp_path: Path) -> None:
    """F3-03 dual-write override (DEC-097): a supplied `recorded_at` is stored
    VERBATIM — the same stamp the primary project.db write carries — and read
    back byte-identical off the event row and the materialised projection, so
    the parity clock's (dispatch_id, session_id, recorded_at) key lines up."""
    store = EventStore(event_store.events_db_path_for(tmp_path))
    try:
        stamp = "2026-07-17T12:34:56Z"
        e = store.append(
            {"event_type": "dispatch.completed", "event_id": "e1", "aggregate_id": "d1",
             "session_id": "s1", "payload": {"dispatch_id": "d1", "session_id": "s1", "persona": "atlas"}},
            recorded_at=stamp,
        )
        assert e.recorded_at == stamp
        assert store.read_events()[0].recorded_at == stamp  # persisted verbatim
        store.rebuild_projections()
        proj = store.query_projection("dispatch_telemetry")
        assert proj[0]["recorded_at"] == stamp  # projection COPIES the event stamp, never re-stamps
    finally:
        store.close()


def test_supplied_recorded_at_does_not_perturb_replay_determinism(tmp_path: Path) -> None:
    """The override is NOT an ordering/apply input: two stores fed the SAME
    events with the SAME supplied stamps replay to identical hashes (model
    `event_log.ordering` — recorded_at is copied, never folded)."""
    def _build(db: Path) -> dict[str, str]:
        store = EventStore(db)
        try:
            store.append(
                {"event_type": "dispatch.completed", "event_id": "e1", "aggregate_id": "d1",
                 "payload": {"dispatch_id": "d1", "persona": "atlas"}},
                recorded_at="2026-07-17T12:00:00Z",
            )
            return event_store.hash_projections(store.rebuild_projections())
        finally:
            store.close()

    assert _build(tmp_path / "a" / "events.duckdb") == _build(tmp_path / "b" / "events.duckdb")


# ── daemon-only single-writer discipline (model `event_log.single_writer`) ──


def test_event_store_is_the_single_open_writer_second_process_fails_loud(tmp_path: Path) -> None:
    """Structural single-writer proof (ADR-001, DEC-040 scar) — DuckDB itself
    refuses a second, genuinely separate process opening the SAME file while
    the daemon holds it, so an accidental second writer fails LOUD. Mirrors the
    spans single-writer proof; must be a real subprocess (in-process DuckDB
    caches one engine per path)."""
    db_path = event_store.events_db_path_for(tmp_path)
    store = EventStore(db_path)
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


def test_module_exposes_no_second_writer_path() -> None:
    """Acceptance: the module API never exposes a second writer. The ONLY write
    path is `EventStore.append` (reachable only through the one daemon-held
    connection) — there is no module-level append/insert function."""
    assert hasattr(EventStore, "append")
    assert not hasattr(event_store, "append")
    assert not hasattr(event_store, "insert_event")


# ── projection folds (model `projections[]`) ───────────────────────────────


def test_tasks_projection_folds_full_lifecycle() -> None:
    events = [
        ev(0, "task.created", {"id": "TASK-1", "title": "orig", "status": "todo"}),
        ev(1, "task.updated", {"id": "TASK-1", "changed_fields": {"status": "in_progress"},
                               "updated_at": "2026-07-17T01:00:00Z"}),
        ev(2, "task.stalled", {"id": "TASK-1", "stall_count": 2, "last_persona": "pipeline-data"}),
        ev(3, "task.created", {"id": "TASK-2", "title": "second"}),
        ev(4, "task.archived", {"id": "TASK-2", "status": "archived", "notes": "reaped"}),
        ev(5, "task.id_repaired", {"orphan_id": "TASK-1", "canonical_id": "TASK-9"}),
    ]
    rows = event_store.fold_tasks(events)
    by_id = {r["id"]: r for r in rows}
    assert set(by_id) == {"TASK-9", "TASK-2"}
    assert by_id["TASK-9"]["status"] == "in_progress"
    assert by_id["TASK-9"]["stall_count"] == 2
    assert by_id["TASK-9"]["last_persona"] == "pipeline-data"
    assert by_id["TASK-2"]["status"] == "archived"
    assert by_id["TASK-2"]["notes"] == "reaped"


def test_sessions_projection_folds_lifecycle_and_reset() -> None:
    events = [
        ev(0, "session.started", {"id": "S1", "started_at": FIXED_TS, "branch": "main"}),
        ev(1, "session.message_counted", {"id": "S1", "user_message_count": 3}),
        ev(2, "session.message_counted", {"id": "S1", "user_message_count": 7}),
        ev(3, "session.ended", {"id": "S1", "ended_at": "2026-07-17T02:00:00Z", "summary": "done",
                                "tokens_total": 100, "duration_ms": 5000}),
        ev(4, "session.reset", {"closed_session_id": "S1", "closed_at": "2026-07-17T03:00:00Z",
                                "new_session_id": "S2", "new_started_at": "2026-07-17T03:00:01Z"}),
    ]
    rows = event_store.fold_sessions(events)
    by_id = {r["id"]: r for r in rows}
    assert by_id["S1"]["user_message_count"] == 7  # ABSOLUTE, not += (idempotent by value)
    assert by_id["S1"]["tokens_total"] == 100
    assert by_id["S1"]["last_reset_at"] == "2026-07-17T03:00:00Z"
    assert by_id["S2"]["started_at"] == "2026-07-17T03:00:01Z"
    assert by_id["S2"]["user_message_count"] == 0


def test_dispatch_telemetry_is_strict_one_row_per_dispatch() -> None:
    events = [
        ev(0, "dispatch.completed", {"dispatch_id": "d1", "persona": "pipeline-data", "marker": "DONE"}),
        ev(1, "dispatch.completed", {"dispatch_id": "d2", "persona": "atlas", "marker": "PASS"}),
    ]
    rows = event_store.fold_dispatch_telemetry(events)
    assert len(rows) == 2
    assert [r["id"] for r in rows] == [1, 2]
    assert rows[0]["recorded_at"] == FIXED_TS  # copied from the event row, not re-stamped
    assert rows[0]["token_source"] == "exact"  # enum default, never a new member


def test_skill_load_events_projection_is_n_per_dispatch() -> None:
    events = [
        ev(0, "skill.loaded", {"dispatch_id": "d1", "skill_id": "agent-protocol", "ts": FIXED_TS}),
        ev(1, "skill.loaded", {"dispatch_id": "d1", "skill_id": "deployable-engineering", "ts": FIXED_TS}),
    ]
    rows = event_store.fold_skill_load_events(events)
    assert len(rows) == 2
    assert {r["skill_id"] for r in rows} == {"agent-protocol", "deployable-engineering"}


# ── cited-verdict invariant (model `projections.validation_log`) ───────────


def test_validation_log_copies_derived_verdict_and_evidence_verbatim() -> None:
    events = [
        ev(0, "lens.verdict.recorded", {
            "agent_validated": "lens", "target_agent": "pipeline-data",
            "task_or_brief_hash": "h1", "verdict": "PASS", "claimed_verdict": "PASS",
            "evidence_backed": True, "evidence_summary": "green [rc=0]",
        }, event_version=2),
    ]
    rows = event_store.fold_validation_log(events)
    assert rows[0]["verdict"] == "PASS"
    assert rows[0]["claimed_verdict"] == "PASS"
    assert rows[0]["evidence_backed"] is True
    assert rows[0]["id"] == 1  # surrogate = replay order (replaces AUTOINCREMENT)


def test_validation_replay_tolerates_pre_invariant_uncited_pass() -> None:
    """SEQUENCING CONSTRAINT: TASK-073 is still todo — 155/216 live PASS rows
    are evidence_backed=FALSE from the legit no-report path. Replay is a TOTAL
    fold: a PRE-invariant (event_version < CITED_VERDICT_MIN_VERSION) uncited
    PASS replays faithfully, NEVER raises, and stays queryable."""
    events = [
        ev(0, "lens.verdict.recorded", {
            "agent_validated": "lens", "target_agent": "pipeline-data",
            "task_or_brief_hash": "h1", "verdict": "PASS", "evidence_backed": False,
        }, event_version=1),
    ]
    rows = event_store.fold_validation_log(events)  # must NOT raise
    assert rows[0]["verdict"] == "PASS"
    assert rows[0]["evidence_backed"] is False
    assert event_store.uncited_pass_count(events) == 1


def test_validation_replay_refuses_post_invariant_uncited_pass() -> None:
    """The version-scoped refusal: an uncited PASS at
    event_version >= CITED_VERDICT_MIN_VERSION is a real POST-invariant
    regression → fail loud."""
    events = [
        ev(0, "lens.verdict.recorded", {
            "agent_validated": "lens", "target_agent": "pipeline-data",
            "task_or_brief_hash": "h1", "verdict": "PASS", "evidence_backed": False,
        }, event_version=event_store.CITED_VERDICT_MIN_VERSION),
    ]
    with pytest.raises(event_store.ReplayInvariantError):
        event_store.fold_validation_log(events)


def test_validation_replay_refuses_verdict_outside_closed_enum() -> None:
    events = [
        ev(0, "lens.verdict.recorded", {
            "agent_validated": "lens", "target_agent": "x", "task_or_brief_hash": "h1",
            "verdict": "MAYBE",
        }),
    ]
    with pytest.raises(event_store.ReplayInvariantError):
        event_store.fold_validation_log(events)


def test_evidence_backed_is_a_queryable_first_class_column(tmp_path: Path) -> None:
    """First-class column, not prose: a materialised uncited PASS is findable
    by `evidence_backed = FALSE` directly off the projection."""
    store = EventStore(event_store.events_db_path_for(tmp_path))
    try:
        store.append({"event_type": "lens.verdict.recorded", "event_id": "v1", "aggregate_id": "h1",
                      "event_version": 1,
                      "payload": {"agent_validated": "lens", "target_agent": "pipeline-data",
                                  "task_or_brief_hash": "h1", "verdict": "PASS", "evidence_backed": False}})
        store.rebuild_projections()
        rows = store.query_projection("validation_log")
        assert len(rows) == 1
        unbacked = [r for r in rows if r["verdict"] == "PASS" and r["evidence_backed"] is False]
        assert len(unbacked) == 1
    finally:
        store.close()


# ── replay determinism (model `replay`) — the F3-02 core contract ──────────


def _mixed_events() -> list[Event]:
    return [
        ev(0, "task.created", {"id": "TASK-1", "title": "one", "created_at": FIXED_TS}),
        ev(1, "task.updated", {"id": "TASK-1", "changed_fields": {"status": "done"},
                               "updated_at": "2026-07-17T04:00:00Z", "completed_at": "2026-07-17T04:00:00Z"}),
        ev(2, "session.started", {"id": "S1", "started_at": FIXED_TS, "branch": "main"}),
        ev(3, "session.message_counted", {"id": "S1", "user_message_count": 5}),
        ev(4, "dispatch.completed", {"dispatch_id": "d1", "persona": "pipeline-data",
                                     "marker": "DONE", "tokens": 1234}, recorded_at="2026-07-17T05:00:00Z"),
        ev(5, "skill.loaded", {"dispatch_id": "d1", "skill_id": "agent-protocol", "ts": FIXED_TS}),
        ev(6, "lens.verdict.recorded", {"agent_validated": "lens", "target_agent": "pipeline-data",
                                        "task_or_brief_hash": "h1", "verdict": "PASS",
                                        "evidence_backed": True}, event_version=2),
        ev(7, "lens.verdict.recorded", {"agent_validated": "lens", "target_agent": "atlas",
                                        "task_or_brief_hash": "h2", "verdict": "PASS",
                                        "evidence_backed": False}, event_version=1),
    ]


def test_replay_twice_yields_identical_projection_hashes() -> None:
    """THE replay-determinism test (F3-02 acceptance): `replay(events)` twice →
    identical per-projection AND whole-store hashes."""
    events = _mixed_events()
    h1 = event_store.hash_projections(event_store.replay(events))
    h2 = event_store.hash_projections(event_store.replay(events))
    assert h1 == h2
    for name in event_store.PROJECTIONS:
        assert h1[name] == h2[name], name
    assert event_store.whole_store_hash(h1) == event_store.whole_store_hash(h2)


def test_rebuild_projections_is_deterministic_through_the_store(tmp_path: Path) -> None:
    """Same contract through the real materialisation path: re-folding the
    immutable log twice yields byte-identical `proj_*` tables."""
    store = EventStore(event_store.events_db_path_for(tmp_path))
    try:
        for i, e in enumerate(_mixed_events()):
            store.append({
                "event_type": e.event_type,
                "event_id": f"evt-{i}",
                "aggregate_id": e.aggregate_id,
                "event_version": e.event_version,
                "session_id": e.session_id,
                "payload": e.payload,
            })
        first = event_store.hash_projections(store.rebuild_projections())
        second = event_store.hash_projections(store.rebuild_projections())
        assert first == second
        assert event_store.whole_store_hash(first) == event_store.whole_store_hash(second)
    finally:
        store.close()


def test_replay_determinism_test_is_non_vacuous() -> None:
    """Negative proof (model `replay.replay_determinism_test.negative_test`): a
    NON-pure apply that re-stamps `recorded_at` MUST make the two hashes
    differ — otherwise the determinism test above would be vacuously green. The
    REAL pure fold on the SAME events stays stable, by contrast."""
    events = [ev(0, "dispatch.completed", {"dispatch_id": "d1", "persona": "pipeline-data"})]
    columns = ("id", "persona", "recorded_at")

    def impure(evs: list[Event]) -> list[dict]:
        rows = []
        for e in evs:
            if e.aggregate_type != "dispatch":
                continue
            rows.append({"id": len(rows) + 1, "persona": e.payload["persona"],
                         "recorded_at": str(time.perf_counter_ns())})  # IMPURE: now(), not the payload
        return rows

    impure_h1 = event_store.projection_hash(impure(events), columns, lambda r: r["id"])
    impure_h2 = event_store.projection_hash(impure(events), columns, lambda r: r["id"])
    assert impure_h1 != impure_h2  # a non-pure apply IS detectable → the test is meaningful

    pure_h1 = event_store.projection_hash(
        event_store.fold_dispatch_telemetry(events), event_store._DISPATCH_COLUMNS, lambda r: r["id"]
    )
    pure_h2 = event_store.projection_hash(
        event_store.fold_dispatch_telemetry(events), event_store._DISPATCH_COLUMNS, lambda r: r["id"]
    )
    assert pure_h1 == pure_h2


def test_projections_are_queryable_after_rebuild(tmp_path: Path) -> None:
    store = EventStore(event_store.events_db_path_for(tmp_path))
    try:
        store.append({"event_type": "task.created", "event_id": "e1", "aggregate_id": "TASK-1",
                      "payload": {"id": "TASK-1", "title": "one", "created_at": FIXED_TS}})
        store.append({"event_type": "session.started", "event_id": "e2", "aggregate_id": "S1",
                      "payload": {"id": "S1", "started_at": FIXED_TS, "branch": "main"}})
        store.rebuild_projections()
        assert [r["id"] for r in store.query_projection("tasks")] == ["TASK-1"]
        assert [r["id"] for r in store.query_projection("sessions")] == ["S1"]
        assert store.query_projection("dispatch_telemetry") == []
    finally:
        store.close()


# ── model-json conformance (acceptance 2) ──────────────────────────────────


def test_event_types_match_model_json_exactly() -> None:
    model = _model_json()
    if model is None:
        pytest.skip("event-store-model.json not present in this tree (package twin)")
    model_types = {e["name"]: e["aggregate_type"] for e in model["event_types"]}
    assert set(event_store.EVENT_TYPES) == set(model_types)  # no invented, none missing
    for name, aggregate in model_types.items():
        assert event_store.EVENT_TYPES[name] == aggregate, name


def test_projections_match_model_json() -> None:
    model = _model_json()
    if model is None:
        pytest.skip("event-store-model.json not present in this tree (package twin)")
    model_projections = {p["name"] for p in model["projections"]}
    assert set(event_store.PROJECTIONS) <= model_projections  # no invented projection
    assert {"tasks", "sessions", "validation_log", "dispatch_telemetry"} <= set(event_store.PROJECTIONS)
    sketch = " ".join(
        [p for p in model["projections"] if p["name"] == "validation_log"][0]["replay_fn_sketch"]
    )
    assert "CITED_VERDICT_MIN_VERSION = 2" in sketch
    assert event_store.CITED_VERDICT_MIN_VERSION == 2
