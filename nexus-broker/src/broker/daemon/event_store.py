"""broker.daemon.event_store — F3-02 event-sourced single store: an
append-only DuckDB event log plus deterministic-replay projections, written
ONLY by the daemon (ADR-001 Tier 2, single writer). Implements the F3-01
lens-PASSed design EXACTLY — every construct below cites its key in
`nexus-foundation/plans/artifacts/event-store-model.json`.

Model keys realised here:
  - `event_log`            → `event_log` table (DDL, single_writer, append_only, ordering)
  - `event_types[]`        → `EVENT_TYPES` (derived ONLY from real writers; no invented events)
  - `projections[]`        → `fold_*` folds + `proj_*` DDL (tasks, sessions, validation_log,
                             dispatch_telemetry, skill_load_events)
  - `replay`               → `project` / `replay` + the canonical-hash functions
  - `dec_040_wal_scar`     → the single-writer discipline documented on `EventStore`

Single-writer discipline (model `event_log.single_writer`, `dec_040_wal_scar`;
same guarantee `broker.daemon.spans.SpanStore` carries): the daemon is the
SOLE process that appends. `EventStore.append` is the ONE write path and it is
reachable only through the one daemon-held read-write DuckDB connection — this
module exposes NO second writer (no module-level append, no second write
connection). DuckDB itself refuses a second process's connection to a
write-held file (`duckdb.IOException`), so an accidental second writer fails
LOUD, never silently corrupting the log. The DEC-040 WAL-ballooning failure
class (concurrent writers) is therefore eliminated by construction, not by
policy — and because every projection is a pure replay of the immutable log,
even a damaged projection is rebuildable (`rebuild_projections`).

FUTURE-STATE boundary (model `meta.constraints.C-07`): F3-02 builds the log +
folds + the replay-determinism test; NO consumer is switched (readers still
hit `project.db`) — dual-write / parity / cutover is F3-03. Nothing in
`.memory/schema.sql` or `broker.daemon.spans._DDL` is touched here.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb

# ── event_log (model `event_log`) ──────────────────────────────────────────

EVENT_LOG_TABLE = "event_log"

# Verbatim from model `event_log.ddl`: INSERT-only, `seq` the sole total-order
# replay key, `event_id` the UNIQUE idempotency key. No UPDATE, no DELETE ever.
_EVENT_LOG_DDL = f"""
CREATE TABLE IF NOT EXISTS {EVENT_LOG_TABLE} (
    seq            BIGINT       NOT NULL,
    event_id       VARCHAR      NOT NULL,
    event_type     VARCHAR      NOT NULL,
    event_version  INTEGER      NOT NULL DEFAULT 1,
    aggregate_type VARCHAR      NOT NULL,
    aggregate_id   VARCHAR      NOT NULL,
    session_id     VARCHAR,
    occurred_at    VARCHAR      NOT NULL,
    recorded_at    VARCHAR      NOT NULL,
    payload        VARCHAR      NOT NULL,
    PRIMARY KEY (seq),
    UNIQUE (event_id)
)
"""

_EVENT_LOG_COLUMNS = (
    "seq",
    "event_id",
    "event_type",
    "event_version",
    "aggregate_type",
    "aggregate_id",
    "session_id",
    "occurred_at",
    "recorded_at",
    "payload",
)

_EVENT_INSERT = f"""
INSERT INTO {EVENT_LOG_TABLE}
    (seq, event_id, event_type, event_version, aggregate_type, aggregate_id,
     session_id, occurred_at, recorded_at, payload)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

# model `event_types[]` — every entry's `name` → `aggregate_type`, derived ONLY
# from a real `source_writer` (no invented events; an unknown event_type is
# rejected at the append boundary). Kept in the model's declared order.
EVENT_TYPES: dict[str, str] = {
    "task.created": "task",
    "task.updated": "task",
    "task.stalled": "task",
    "task.archived": "task",
    "task.id_repaired": "task",
    "session.started": "session",
    "session.ended": "session",
    "session.reset": "session",
    "session.message_counted": "session",
    "lens.verdict.recorded": "validation",
    "dispatch.completed": "dispatch",
    "skill.loaded": "skill",
    "span.emitted": "span",
}

# model `projections.validation_log.replay_fn_sketch` line 1 + the F3-01
# `historical_backfill_and_sequencing` block: the `event_version` the daemon
# stamps once TASK-073's produce-time cited-verdict derive is live. Replay
# HARD-refuses an uncited PASS ONLY at or above this version — pre-invariant
# (version 1) rows replay faithfully. TASK-073 is status=todo as of the design
# (155/216 live PASS rows are legitimately evidence_backed=FALSE), so this
# module ships NO blanket uncited-PASS assert — exactly the sequencing
# constraint the design pins.
CITED_VERDICT_MIN_VERSION = 2

_VALID_VERDICTS = ("PASS", "PARTIAL", "FAIL")

# Canonical-hash NULL sentinel (model `replay.determinism_requirements` item 5:
# "NULLs rendered as a single sentinel"). Never a legitimate stored value.
_NULL_SENTINEL = "\x00__NULL__"


class EventValidationError(ValueError):
    """Malformed event at the append boundary — always raised, never a silent
    drop, so a caller sees a typed reason. A `ValueError` subclass so existing
    generic-exception boundaries keep working (mirrors
    `spans.SpanValidationError`)."""


class ReplayInvariantError(Exception):
    """A genuine invariant violation surfaced during replay (model
    `projections.validation_log.replay_fn_sketch`): a verdict outside the
    closed enum, or a POST-invariant uncited PASS
    (`event_version >= CITED_VERDICT_MIN_VERSION`). Pre-invariant historical
    rows NEVER raise — the fold is TOTAL (model
    `determinism_notes` + `historical_backfill_and_sequencing`)."""


def events_db_path_for(project_path: Path) -> Path:
    """`.memory/events.duckdb` — the ADR-001 Tier 2 file (model
    `event_log.store`), sibling of `spans.duckdb`, same "daemon writes it at
    runtime, nobody hand-edits it" posture."""
    return project_path / ".memory" / "events.duckdb"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: UP017


def _require_str(source: dict[str, Any], field: str) -> str:
    value = source.get(field)
    if not isinstance(value, str) or not value:
        raise EventValidationError(f"event.{field} must be a non-empty str")
    return value


def _optional_str(source: dict[str, Any], field: str) -> str | None:
    value = source.get(field)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise EventValidationError(f"event.{field} must be a non-empty str or None")
    return value


@dataclass
class Event:
    """One immutable row of `event_log`, payload parsed back to a dict. `seq`
    is the sole replay ordering key; `recorded_at` is append-time (human/debug
    only, NEVER an ordering or `apply()` input) — model `event_log.ordering`."""

    seq: int
    event_id: str
    event_type: str
    event_version: int
    aggregate_type: str
    aggregate_id: str
    session_id: str | None
    occurred_at: str
    recorded_at: str
    payload: dict[str, Any]


def validate_event(event: Any) -> dict[str, Any]:
    """Shape-validate one event at the append boundary and return the
    normalised envelope (payload still a dict; the store canonical-JSON-encodes
    it). Enforces model `event_types[]`: `event_type` MUST be a known type (no
    invented events) and `aggregate_type`, when supplied, MUST match the
    registry. Raises `EventValidationError` on any violation — never a silent
    coercion or drop."""
    if not isinstance(event, dict):
        raise EventValidationError("event must be a dict")

    event_type = _require_str(event, "event_type")
    if event_type not in EVENT_TYPES:
        raise EventValidationError(
            f"unknown event_type {event_type!r} — model event_types[] admits no invented events"
        )
    expected_aggregate = EVENT_TYPES[event_type]
    aggregate_type = event.get("aggregate_type", expected_aggregate)
    if aggregate_type != expected_aggregate:
        raise EventValidationError(
            f"event.aggregate_type {aggregate_type!r} != {expected_aggregate!r} for {event_type!r}"
        )

    event_id = _require_str(event, "event_id")
    aggregate_id = _require_str(event, "aggregate_id")

    event_version = event.get("event_version", 1)
    if isinstance(event_version, bool) or not isinstance(event_version, int):
        raise EventValidationError("event.event_version must be an int")

    session_id = _optional_str(event, "session_id")
    occurred_at = _optional_str(event, "occurred_at") or _now_iso()

    payload = event.get("payload", {})
    if not isinstance(payload, dict):
        raise EventValidationError("event.payload must be a dict")

    return {
        "event_id": event_id,
        "event_type": event_type,
        "event_version": event_version,
        "aggregate_type": aggregate_type,
        "aggregate_id": aggregate_id,
        "session_id": session_id,
        "occurred_at": occurred_at,
        "payload": payload,
    }


def _event_from_row(row: tuple) -> Event:
    record = dict(zip(_EVENT_LOG_COLUMNS, row, strict=True))
    return Event(
        seq=int(record["seq"]),
        event_id=record["event_id"],
        event_type=record["event_type"],
        event_version=int(record["event_version"]),
        aggregate_type=record["aggregate_type"],
        aggregate_id=record["aggregate_id"],
        session_id=record["session_id"],
        occurred_at=record["occurred_at"],
        recorded_at=record["recorded_at"],
        payload=json.loads(record["payload"]),
    )


# ── projection folds (model `projections[]`) ───────────────────────────────
#
# Every fold is a PURE reduce over events ALREADY ordered by `seq` (model
# `replay.determinism_requirements` items 1-2): no now(), no random, no I/O —
# every projected value comes from the event payload or the stored event row.
# AUTOINCREMENT ids become replay-order surrogates (item 3); events are applied
# with absolute values so a re-delivered event is idempotent (item 4).

_TASKS_COLUMNS = (
    "id", "feature_id", "title", "description", "status", "priority",
    "assigned_to", "worktree", "acceptance_criteria", "created_at", "updated_at",
    "completed_at", "notes", "subtasks_json", "estimated_minutes", "stall_count",
    "last_persona",
)


def _task_row_from_created(p: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": p["id"],
        "feature_id": p.get("feature_id"),
        "title": p.get("title", ""),
        "description": p.get("description"),
        "status": p.get("status", "todo"),
        "priority": p.get("priority", "medium"),
        "assigned_to": p.get("assigned_to"),
        "worktree": p.get("worktree"),
        "acceptance_criteria": p.get("acceptance_criteria"),
        "created_at": p.get("created_at", ""),
        "updated_at": p.get("updated_at", p.get("created_at", "")),
        "completed_at": p.get("completed_at"),
        "notes": p.get("notes"),
        "subtasks_json": p.get("subtasks_json"),
        "estimated_minutes": p.get("estimated_minutes"),
        "stall_count": p.get("stall_count", 0),
        "last_persona": p.get("last_persona"),
    }


def fold_tasks(events: list[Event]) -> list[dict[str, Any]]:
    """model `projections.tasks.replay_fn_sketch`. INSERT-OR-REPLACE-shaped
    create; sparse update patches only the changed columns; stalled/archived
    carry absolute values; id_repaired re-keys the row (audit trail lives in
    the log). Final order = ORDER BY id for the deterministic hash."""
    state: dict[str, dict[str, Any]] = {}
    for e in events:
        if e.aggregate_type != "task":
            continue
        p = e.payload
        if e.event_type == "task.created":
            state[p["id"]] = _task_row_from_created(p)
        elif e.event_type == "task.updated":
            row = state.get(p["id"])
            if row is None:
                continue
            row.update(p.get("changed_fields", {}))
            row["updated_at"] = p.get("updated_at", row["updated_at"])
            if p.get("completed_at") is not None:
                row["completed_at"] = p["completed_at"]
        elif e.event_type == "task.stalled":
            row = state.get(p["id"])
            if row is None:
                continue
            row["stall_count"] = p["stall_count"]
            row["last_persona"] = p["last_persona"]
            row["updated_at"] = p.get("updated_at", row["updated_at"])
        elif e.event_type == "task.archived":
            row = state.get(p["id"])
            if row is None:
                continue
            row["status"] = "archived"
            row["notes"] = p.get("notes")
            row["updated_at"] = p.get("updated_at", row["updated_at"])
        elif e.event_type == "task.id_repaired":
            orphan, canonical = p["orphan_id"], p["canonical_id"]
            if orphan in state:
                row = state.pop(orphan)
                row["id"] = canonical
                state[canonical] = row
    return [state[key] for key in sorted(state)]


_SESSIONS_COLUMNS = (
    "id", "started_at", "ended_at", "summary", "last_step", "next_step",
    "branch", "context_json", "user_message_count", "last_reset_at",
    "tokens_total", "duration_ms",
)


def _session_row(id_: str, started_at: str, branch: str) -> dict[str, Any]:
    return {
        "id": id_,
        "started_at": started_at,
        "ended_at": None,
        "summary": None,
        "last_step": None,
        "next_step": None,
        "branch": branch,
        "context_json": None,
        "user_message_count": 0,
        "last_reset_at": None,
        "tokens_total": None,
        "duration_ms": None,
    }


def fold_sessions(events: list[Event]) -> list[dict[str, Any]]:
    """model `projections.sessions.replay_fn_sketch`. message_counted carries
    the ABSOLUTE count (idempotent by value); reset is ONE event that closes
    the old session and opens the new so replay can never interleave them."""
    state: dict[str, dict[str, Any]] = {}
    for e in events:
        if e.aggregate_type != "session":
            continue
        p = e.payload
        if e.event_type == "session.started":
            state[p["id"]] = _session_row(p["id"], p.get("started_at", ""), p.get("branch", "main"))
        elif e.event_type == "session.ended":
            row = state.get(p["id"])
            if row is None:
                continue
            for field in ("ended_at", "summary", "next_step", "context_json",
                          "tokens_total", "duration_ms", "last_step"):
                if field in p:
                    row[field] = p[field]
        elif e.event_type == "session.reset":
            closed = state.get(p["closed_session_id"])
            if closed is not None:
                closed["ended_at"] = p.get("closed_at")
                closed["last_reset_at"] = p.get("closed_at")
            state[p["new_session_id"]] = _session_row(
                p["new_session_id"], p.get("new_started_at", ""), p.get("branch", "main")
            )
        elif e.event_type == "session.message_counted":
            row = state.get(p["id"])
            if row is None:
                continue
            row["user_message_count"] = p["user_message_count"]
    return [state[key] for key in sorted(state)]


_VALIDATION_COLUMNS = (
    "id", "session_id", "agent_validated", "target_agent", "task_or_brief_hash",
    "verdict", "evidence_summary", "validated_at", "files_changed_json",
    "revise_reason", "dispatch_started_at", "lens_type", "risk_tier",
    "evidence_backed", "claimed_verdict",
)


def _validation_row(p: dict[str, Any]) -> dict[str, Any]:
    return {
        "session_id": p.get("session_id"),
        "agent_validated": p["agent_validated"],
        "target_agent": p["target_agent"],
        "task_or_brief_hash": p["task_or_brief_hash"],
        "verdict": p["verdict"],
        "evidence_summary": p.get("evidence_summary"),
        "validated_at": p.get("validated_at"),
        "files_changed_json": p.get("files_changed_json"),
        "revise_reason": p.get("revise_reason"),
        "dispatch_started_at": p.get("dispatch_started_at"),
        "lens_type": p.get("lens_type"),
        "risk_tier": p.get("risk_tier"),
        "evidence_backed": bool(p.get("evidence_backed", False)),
        "claimed_verdict": p.get("claimed_verdict"),
    }


def fold_validation_log(events: list[Event]) -> list[dict[str, Any]]:
    """model `projections.validation_log.replay_fn_sketch` + the
    `cited_verdict_invariant`. TOTAL fold: the DERIVED verdict and its cited
    evidence (`evidence_backed`, `claimed_verdict`, `evidence_summary`) are
    copied VERBATIM — never re-derived. Replay NEVER aborts on a legitimate
    historical uncited PASS (pre-invariant); it HARD-refuses ONLY a
    POST-invariant regression, scoped by
    `event_version >= CITED_VERDICT_MIN_VERSION`. Surrogate id = replay order
    (replaces AUTOINCREMENT). `evidence_backed` is a first-class column so
    every uncited PASS stays queryable in the projection itself."""
    rows: list[dict[str, Any]] = []
    for e in events:
        if e.aggregate_type != "validation":
            continue
        p = e.payload
        verdict = p["verdict"]
        if verdict not in _VALID_VERDICTS:
            raise ReplayInvariantError(
                f"validation verdict {verdict!r} outside closed enum at seq={e.seq}"
            )
        uncited_pass = verdict == "PASS" and bool(p.get("evidence_backed", False)) is False
        if uncited_pass and e.event_version >= CITED_VERDICT_MIN_VERSION:
            raise ReplayInvariantError(f"post-invariant uncited PASS at seq={e.seq}")
        rows.append(_validation_row(p))
    return _assign_replay_ids(rows)


def uncited_pass_count(events: list[Event]) -> int:
    """model `projections.validation_log` metric: count of pre-invariant
    evidence-unbacked PASS rows (materialised faithfully, surfaced as a
    projection metric — never an exception). Also queryable directly off the
    projection via `evidence_backed = FALSE`."""
    return sum(
        1
        for e in events
        if e.aggregate_type == "validation"
        and e.payload.get("verdict") == "PASS"
        and bool(e.payload.get("evidence_backed", False)) is False
    )


_DISPATCH_COLUMNS = (
    "id", "session_id", "dispatch_id", "persona", "model", "task_id", "marker",
    "tokens", "token_source", "tool_uses", "duration_ms", "run_context",
    "independent_subtask_count", "decomposition_considered", "recorded_at",
)


def fold_dispatch_telemetry(events: list[Event]) -> list[dict[str, Any]]:
    """model `projections.dispatch_telemetry.replay_fn_sketch`. STRICT
    one-row-per-dispatch (never collapsed with skill.loaded). `recorded_at` is
    COPIED from the stored `event_log.recorded_at` (append-time), NEVER
    re-stamped with now() — the determinism foot-gun the model calls out."""
    rows: list[dict[str, Any]] = []
    for e in events:
        if e.aggregate_type != "dispatch":
            continue
        p = e.payload
        rows.append(
            {
                "session_id": p.get("session_id"),
                "dispatch_id": p.get("dispatch_id"),
                "persona": p["persona"],
                "model": p.get("model"),
                "task_id": p.get("task_id"),
                "marker": p.get("marker"),
                "tokens": p.get("tokens"),
                "token_source": p.get("token_source", "exact"),
                "tool_uses": p.get("tool_uses"),
                "duration_ms": p.get("duration_ms"),
                "run_context": p.get("run_context", "local"),
                "independent_subtask_count": p.get("independent_subtask_count"),
                "decomposition_considered": p.get("decomposition_considered"),
                "recorded_at": e.recorded_at,
            }
        )
    return _assign_replay_ids(rows)


_SKILL_COLUMNS = ("id", "dispatch_id", "skill_id", "ts", "byte_len")


def fold_skill_load_events(events: list[Event]) -> list[dict[str, Any]]:
    """model `projections.skill_load_events.replay_fn_sketch`. N-per-dispatch;
    kept a SEPARATE projection from dispatch_telemetry (schema.sql §9
    non-relitigation). Included so the dispatch aggregate is complete."""
    rows = [
        {
            "dispatch_id": e.payload["dispatch_id"],
            "skill_id": e.payload["skill_id"],
            "ts": e.payload["ts"],
            "byte_len": e.payload.get("byte_len"),
        }
        for e in events
        if e.aggregate_type == "skill"
    ]
    return _assign_replay_ids(rows)


def _assign_replay_ids(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deterministic surrogate key = replay-order position (model
    `replay.determinism_requirements` item 3), replacing SQLite AUTOINCREMENT.
    1-based to mirror the live sequence; a rebuild reproduces identical ids."""
    for index, row in enumerate(rows, start=1):
        row["id"] = index
    return rows


@dataclass(frozen=True)
class _Projection:
    fold: Callable[[list[Event]], list[dict[str, Any]]]
    columns: tuple[str, ...]
    pk: Callable[[dict[str, Any]], Any]


# model `projections[]` — the four brief-named hot tables plus the inseparable
# skill_load_events sibling. `spans` is DEFERRED here by design: its
# `target_schema_ddl` is "identical to broker.daemon.spans._DDL ... do_not_touch
# here — referenced, not redefined", and it is already a live single-writer
# store — F3-02 does not redefine it (model `projections.spans`). PROJECTIONS
# order is the fixed order the whole-store hash concatenates in.
PROJECTIONS: dict[str, _Projection] = {
    "tasks": _Projection(fold_tasks, _TASKS_COLUMNS, lambda r: r["id"]),
    "sessions": _Projection(fold_sessions, _SESSIONS_COLUMNS, lambda r: r["id"]),
    "validation_log": _Projection(fold_validation_log, _VALIDATION_COLUMNS, lambda r: r["id"]),
    "dispatch_telemetry": _Projection(fold_dispatch_telemetry, _DISPATCH_COLUMNS, lambda r: r["id"]),
    "skill_load_events": _Projection(fold_skill_load_events, _SKILL_COLUMNS, lambda r: r["id"]),
}

_PROJECTION_DDLS: tuple[str, ...] = (
    """
CREATE TABLE IF NOT EXISTS proj_tasks (
    id VARCHAR PRIMARY KEY, feature_id VARCHAR, title VARCHAR NOT NULL, description VARCHAR,
    status VARCHAR NOT NULL DEFAULT 'todo', priority VARCHAR NOT NULL DEFAULT 'medium',
    assigned_to VARCHAR, worktree VARCHAR, acceptance_criteria VARCHAR,
    created_at VARCHAR NOT NULL, updated_at VARCHAR NOT NULL, completed_at VARCHAR,
    notes VARCHAR, subtasks_json VARCHAR, estimated_minutes INTEGER,
    stall_count INTEGER DEFAULT 0, last_persona VARCHAR)
""",
    """
CREATE TABLE IF NOT EXISTS proj_sessions (
    id VARCHAR PRIMARY KEY, started_at VARCHAR NOT NULL, ended_at VARCHAR, summary VARCHAR,
    last_step VARCHAR, next_step VARCHAR, branch VARCHAR DEFAULT 'main', context_json VARCHAR,
    user_message_count INTEGER DEFAULT 0, last_reset_at VARCHAR,
    tokens_total INTEGER, duration_ms INTEGER)
""",
    """
CREATE TABLE IF NOT EXISTS proj_validation_log (
    id BIGINT PRIMARY KEY, session_id VARCHAR, agent_validated VARCHAR NOT NULL,
    target_agent VARCHAR NOT NULL, task_or_brief_hash VARCHAR NOT NULL, verdict VARCHAR NOT NULL,
    evidence_summary VARCHAR, validated_at VARCHAR, files_changed_json VARCHAR, revise_reason VARCHAR,
    dispatch_started_at VARCHAR, lens_type VARCHAR, risk_tier VARCHAR,
    evidence_backed BOOLEAN NOT NULL DEFAULT FALSE, claimed_verdict VARCHAR)
""",
    """
CREATE TABLE IF NOT EXISTS proj_dispatch_telemetry (
    id BIGINT PRIMARY KEY, session_id VARCHAR, dispatch_id VARCHAR, persona VARCHAR NOT NULL,
    model VARCHAR, task_id VARCHAR, marker VARCHAR, tokens BIGINT,
    token_source VARCHAR NOT NULL DEFAULT 'exact', tool_uses INTEGER, duration_ms BIGINT,
    run_context VARCHAR DEFAULT 'local', independent_subtask_count INTEGER,
    decomposition_considered INTEGER, recorded_at VARCHAR)
""",
    """
CREATE TABLE IF NOT EXISTS proj_skill_load_events (
    id BIGINT PRIMARY KEY, dispatch_id VARCHAR NOT NULL, skill_id VARCHAR NOT NULL,
    ts VARCHAR NOT NULL, byte_len INTEGER)
""",
)


def project(events: list[Event]) -> dict[str, list[dict[str, Any]]]:
    """Run every projection fold over the log (model `replay.contract`:
    `project = reduce(apply, events_ordered_by_seq, empty_state)`). Pure —
    given the same events it returns byte-identical rows."""
    return {name: spec.fold(events) for name, spec in PROJECTIONS.items()}


def replay(events: list[Event]) -> dict[str, list[dict[str, Any]]]:
    """Alias for `project` — the model's `replay(log)` name. `replay(events)`
    twice yields identical projections (the F3-02 determinism contract)."""
    return project(events)


# ── canonical hashing (model `replay.determinism_requirements` item 5 +
#    `replay.hash_algorithm`) ────────────────────────────────────────────────


def canonical_row(row: dict[str, Any], columns: tuple[str, ...]) -> str:
    """Canonical rendering of one projection row: exactly the declared columns,
    JSON with sorted keys and no whitespace, NULLs as a single sentinel. Two
    replays differ ONLY if the projected state differs — never from
    serialization noise."""
    obj = {c: (_NULL_SENTINEL if row.get(c) is None else row.get(c)) for c in columns}
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def projection_hash(
    rows: list[dict[str, Any]],
    columns: tuple[str, ...],
    pk: Callable[[dict[str, Any]], Any],
) -> str:
    """model `replay.hash_algorithm`:
    `sha256('\\n'.join(canonical_row(r) for r in sorted(rows, key=pk)))`."""
    ordered = sorted(rows, key=pk)
    joined = "\n".join(canonical_row(r, columns) for r in ordered)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def hash_projections(projected: dict[str, list[dict[str, Any]]]) -> dict[str, str]:
    """Per-projection hash for every projection, in `PROJECTIONS` order."""
    return {
        name: projection_hash(projected[name], spec.columns, spec.pk)
        for name, spec in PROJECTIONS.items()
    }


def whole_store_hash(per_projection: dict[str, str]) -> str:
    """model `replay.hash_algorithm`: sha256 over the concatenation of the
    per-projection hashes in the fixed `PROJECTIONS` order."""
    concat = "\n".join(f"{name}={per_projection[name]}" for name in PROJECTIONS)
    return hashlib.sha256(concat.encode("utf-8")).hexdigest()


# ── parity row-diff (F3-03) ─────────────────────────────────────────────────
#
# PROMOTED VERBATIM from `tests/test_prop_parity_diff.py`'s reference
# implementation into this production module — its declared production home per
# the F3-05 promotion note. The F3-05 property suite (`test_prop_parity_diff.py`)
# now imports it from here and pins its invariants (reflexive-empty-on-equal;
# any injected mutation / dropped / added row is always surfaced). This is the
# row-level diff `store_parity.py` uses to compare an event-store projection
# against the live `project.db` hot table during the F3-03 dual-write shadow.


def diff_rows(
    expected: list[dict[str, Any]],
    actual: list[dict[str, Any]],
    *,
    key: Callable[[dict[str, Any]], Any],
) -> list[tuple[str, Any, tuple[str, ...]]]:
    """PURE row-level diff of two projections keyed by `key(row)`.

    Returns a deterministic, sorted list of difference records:
      * ('missing', k, fields) — key k in `expected` but not `actual`.
      * ('extra',   k, fields) — key k in `actual` but not `expected`.
      * ('changed', k, fields) — same key, `fields` = the sorted field names
        whose values differ.
    `diff_rows(x, x, key=…) == []` for any x. No side effects, no I/O — a
    reduce over the two row sets only.
    """
    exp = {key(row): row for row in expected}
    act = {key(row): row for row in actual}
    records: list[tuple[str, Any, tuple[str, ...]]] = []

    for k in exp.keys() - act.keys():
        records.append(("missing", k, tuple(sorted(exp[k]))))
    for k in act.keys() - exp.keys():
        records.append(("extra", k, tuple(sorted(act[k]))))
    for k in exp.keys() & act.keys():
        a, b = exp[k], act[k]
        if a != b:
            fields = tuple(
                sorted(f for f in set(a) | set(b) if a.get(f) != b.get(f))
            )
            records.append(("changed", k, fields))

    return sorted(records, key=lambda r: (r[0], str(r[1])))


class EventStore:
    """Daemon-resident, single-writer DuckDB event log + materialised
    projections (model `event_log`, `projections[]`).

    THE single writer (model `event_log.single_writer`): one instance per
    daemon process, holding the ONE open read-write connection for the
    process's lifetime. `append` is the ONLY write path — there is no
    module-level append and no second write connection anywhere in this module.
    A reader (F3-03 parity, a future dashboard) must open the file read-only
    AFTER the daemon releases it; a second concurrent writer is refused by
    DuckDB itself (`duckdb.IOException`), failing LOUD.
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = duckdb.connect(str(db_path))
        self._conn.execute(_EVENT_LOG_DDL)
        for ddl in _PROJECTION_DDLS:
            self._conn.execute(ddl)
        # Durable monotonic seq (model `event_log.ordering`): MAX(seq)+1 at boot.
        result = self._conn.execute(f"SELECT MAX(seq) FROM {EVENT_LOG_TABLE}").fetchone()
        self._next_seq = int(result[0]) + 1 if result and result[0] is not None else 0
        self.append_count = 0

    def append(self, event: dict[str, Any], *, recorded_at: str | None = None) -> Event:
        """Append ONE event. INSERT-only (model `event_log.append_only`): no
        UPDATE, no DELETE, ever — a state change is a NEW event. `seq` is
        daemon-assigned monotonic (the sole replay key); `event_id` UNIQUE
        dedupes a re-delivered event (model `replay.idempotency`) — a duplicate
        is a no-op returning the already-stored event, never a second row.

        `recorded_at` override (F3-03 dual-write, DEC-097 Option B): the
        append-time stamp defaults to now(); when a caller supplies it — the
        dual-write producer stamps `project.db` AND this event log from ONE
        timestamp so the parity clock's (dispatch_id, session_id, recorded_at)
        key lines up across both stores (design §5.2 trap) — it is stored
        VERBATIM. Replay stays deterministic either way: `recorded_at` is
        COPIED into a projection, NEVER an ordering or `apply()` input (model
        `event_log.ordering`)."""
        validated = validate_event(event)
        existing = self._conn.execute(
            f"SELECT seq FROM {EVENT_LOG_TABLE} WHERE event_id = ?",
            [validated["event_id"]],
        ).fetchone()
        if existing is not None:
            return self._event_at_seq(int(existing[0]))

        seq = self._next_seq
        recorded_at = _now_iso() if recorded_at is None else recorded_at
        self._conn.execute(
            _EVENT_INSERT,
            [
                seq,
                validated["event_id"],
                validated["event_type"],
                validated["event_version"],
                validated["aggregate_type"],
                validated["aggregate_id"],
                validated["session_id"],
                validated["occurred_at"],
                recorded_at,
                json.dumps(validated["payload"], sort_keys=True, separators=(",", ":"), default=str),
            ],
        )
        self._next_seq += 1
        self.append_count += 1
        return Event(
            seq=seq,
            event_id=validated["event_id"],
            event_type=validated["event_type"],
            event_version=validated["event_version"],
            aggregate_type=validated["aggregate_type"],
            aggregate_id=validated["aggregate_id"],
            session_id=validated["session_id"],
            occurred_at=validated["occurred_at"],
            recorded_at=recorded_at,
            payload=validated["payload"],
        )

    def _event_at_seq(self, seq: int) -> Event:
        row = self._conn.execute(
            f"SELECT {', '.join(_EVENT_LOG_COLUMNS)} FROM {EVENT_LOG_TABLE} WHERE seq = ?",
            [seq],
        ).fetchone()
        if row is None:
            raise ReplayInvariantError(f"event_log missing seq={seq}")
        return _event_from_row(row)

    def read_events(self) -> list[Event]:
        """ALL events ordered by `seq` — the sole total-order replay key (model
        `event_log.ordering`; `recorded_at` is NEVER an ordering key)."""
        rows = self._conn.execute(
            f"SELECT {', '.join(_EVENT_LOG_COLUMNS)} FROM {EVENT_LOG_TABLE} ORDER BY seq"
        ).fetchall()
        return [_event_from_row(row) for row in rows]

    def event_count(self) -> int:
        result = self._conn.execute(f"SELECT COUNT(*) FROM {EVENT_LOG_TABLE}").fetchone()
        return int(result[0]) if result else 0

    def rebuild_projections(self) -> dict[str, list[dict[str, Any]]]:
        """Re-fold the immutable log and MATERIALISE every `proj_*` table, so
        projections are queryable off the same connection. Projections are
        disposable caches (model `dec_040_wal_scar`): a full rebuild from the
        log is always safe. Returns the in-memory projection for hashing."""
        events = self.read_events()
        projected = project(events)
        for name, spec in PROJECTIONS.items():
            self._materialise(name, projected[name], spec.columns)
        return projected

    def _materialise(self, name: str, rows: list[dict[str, Any]], columns: tuple[str, ...]) -> None:
        self._conn.execute(f"DELETE FROM proj_{name}")
        if not rows:
            return
        placeholders = ", ".join(["?"] * len(columns))
        statement = f"INSERT INTO proj_{name} ({', '.join(columns)}) VALUES ({placeholders})"
        self._conn.executemany(
            statement,
            [[self._to_db(row.get(column)) for column in columns] for row in rows],
        )

    @staticmethod
    def _to_db(value: Any) -> Any:
        if isinstance(value, (dict, list)):
            return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
        return value

    def query_projection(self, name: str) -> list[dict[str, Any]]:
        """Read a materialised projection back, ordered by its primary key —
        proving the projection is queryable (F3-02 acceptance)."""
        if name not in PROJECTIONS:
            raise KeyError(f"unknown projection {name!r}; known: {sorted(PROJECTIONS)}")
        columns = PROJECTIONS[name].columns
        rows = self._conn.execute(
            f"SELECT {', '.join(columns)} FROM proj_{name} ORDER BY id"
        ).fetchall()
        return [dict(zip(columns, row, strict=True)) for row in rows]

    def close(self) -> None:
        self._conn.close()
