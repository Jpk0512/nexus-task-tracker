"""Daemon pilot server body — Unix-domain-socket JSON-RPC, spawn-on-demand,
idle-shutdown, per-project namespaced (R4-T06, plans/13 N11).

Generalizes the already-deployed `broker/vault/http.py` lifecycle pattern
(health route, graceful signal handling, warm in-process state) from HTTP to
a hand-rolled asyncio Unix-domain-socket transport per plans/07 §2 Option C's
stdlib-only path — no new dependency added.

Wire format: newline-delimited JSON. Request `{"id", "method", "params"}`,
response `{"id", "result"}` or `{"id", "error"}`.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import os
import signal
import socket
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from broker.daemon import drift_watch, event_bus, event_store, paths, pidfile
from broker.daemon.ownership import OwnershipRegistry, handle_ownership_request
from broker.daemon.registry_query import query_registry
from broker.daemon.registry_scan import filter_registry, scan_registry
from broker.daemon.schema_scan import scan_schema
from broker.daemon.session_digest import SessionDigestCache
from broker.daemon.telemetry_store import TelemetryStore

LOG = logging.getLogger("nexus-daemon")


class _RegistryCache:
    """1.1 warm skills/agents cache — refreshed on file-mtime change or TTL."""

    def __init__(self, project_path: Path, ttl_s: float = 30.0) -> None:
        self.project_path = project_path
        self.ttl_s = ttl_s
        self._entries: list[dict[str, Any]] | None = None
        self._loaded_at = 0.0
        self._mtime_key: tuple[tuple[str, float], ...] | None = None

    def _current_mtime_key(self) -> tuple[tuple[str, float], ...]:
        stamps: list[tuple[str, float]] = []
        for base in (
            self.project_path / ".claude" / "agents",
            self.project_path / ".claude" / "skills",
        ):
            if base.is_dir():
                for p in sorted(base.rglob("*.md")):
                    with contextlib.suppress(OSError):
                        stamps.append((str(p), p.stat().st_mtime))
        return tuple(stamps)

    def get(self) -> list[dict[str, Any]]:
        now = time.monotonic()
        key = self._current_mtime_key()
        stale = (now - self._loaded_at) > self.ttl_s
        if self._entries is None or key != self._mtime_key or stale:
            self._entries = scan_registry(self.project_path)
            self._mtime_key = key
            self._loaded_at = now
        return self._entries


class _SchemaCache:
    """1.3 schema-snapshot cache — refreshed on project.db mtime change or TTL."""

    def __init__(self, db_path: Path, ttl_s: float = 30.0) -> None:
        self.db_path = db_path
        self.ttl_s = ttl_s
        self._shape: dict[str, list[str]] | None = None
        self._loaded_at = 0.0
        self._mtime: float | None = None

    def get(self) -> dict[str, list[str]]:
        now = time.monotonic()
        mtime = self.db_path.stat().st_mtime if self.db_path.is_file() else None
        stale = (now - self._loaded_at) > self.ttl_s
        if self._shape is None or mtime != self._mtime or stale:
            self._shape = scan_schema(self.db_path)
            self._mtime = mtime
            self._loaded_at = now
        return self._shape


class DaemonState:
    """All warm in-process state for one project's daemon instance."""

    def __init__(self, project_path: Path) -> None:
        self.project_path = project_path
        self.db_path = project_path / ".memory" / "project.db"
        self.registry_cache = _RegistryCache(project_path)
        self.schema_cache = _SchemaCache(self.db_path)
        # F2-02 — event-bus resident state (event-bus-design.md §2/§4): the
        # 16-event taxonomy + event.emit/event.verify/health.ping/
        # governance.reload/span.emit RPC surface. Empty taxonomy, never a
        # construction failure, on a tenant without nexus-foundation/.
        self.event_bus = event_bus.EventBusState(project_path)
        # R5-T04 (N57) — the 2.9 session-digest substrate (`session_digest.py`,
        # built by N47 but never wired into this dispatch table until now) and
        # the FULL-scope registry query (`registry_query.py`, same gap). Both
        # were the daemon RPC methods `session_digest`/`registry_query_full`
        # fell through as "unknown method" on (feedback id=135) -- wiring them
        # here is what lets `get_session_digest()`/`registry_query_full()` in
        # `broker.jit.context_expansion` actually answer "daemon" instead of
        # always degrading to their direct-read fallback.
        self.session_digest_cache = SessionDigestCache(self.db_path)
        self.telemetry = TelemetryStore()
        self.ownership = OwnershipRegistry()
        self.started_at = time.monotonic()
        self.last_activity = time.monotonic()
        # TASK-105 — the daemon-source digest as it existed when THIS process
        # started; `ensure` compares it against the current on-disk digest and
        # gracefully restarts a stale resident (the auto-refresh path).
        self.source_version = pidfile.source_version()
        # 2.8 budget-summary counters — raw counts only, no invented estimates.
        self.registry_queries_served = 0
        self.registry_bytes_served = 0
        self.schema_queries_served = 0
        self.session_digest_queries_served = 0
        self.registry_query_full_queries_served = 0
        # 2.6 install-drift background loop (N31) — None on a non-meta-repo
        # tenant; see start_drift_watch(). Advisory only, never authoritative.
        self.drift_watcher: drift_watch.DriftWatcher | None = None
        self.drift_report: drift_watch.DriftReport | None = None
        # F3-03 dual-write (DEC-097 Option B) — the single-writer event log,
        # lazily opened on the FIRST dispatch_telemetry dual-write so the many
        # daemon tests that never touch it pay no DuckDB-connect cost and leave
        # no events.duckdb behind (same lazy rationale as `event_bus.span_store`).
        self._event_store: event_store.EventStore | None = None

    def touch(self) -> None:
        self.last_activity = time.monotonic()

    @property
    def event_store(self) -> event_store.EventStore:
        """THE single daemon-resident event-log writer (ADR-001 Tier 2). One
        open read-write DuckDB connection per daemon process, held for its
        lifetime; a reader (F3-03 parity) opens the file read-only only AFTER
        the daemon releases it (`close_event_store`)."""
        if self._event_store is None:
            self._event_store = event_store.EventStore(
                event_store.events_db_path_for(self.project_path)
            )
        return self._event_store

    def close_event_store(self) -> None:
        """Release the event-log DuckDB write connection (daemon shutdown; and
        tests that read the file back from a second connection — DuckDB permits
        one open writer per file). Lazy: a daemon that never dual-wrote holds no
        connection to close."""
        if self._event_store is not None:
            self._event_store.close()
            self._event_store = None


# Tranche 2 (nexus-redesign/audits/daemon-hook-plan-2026-07-12.md §C) — the
# record-only hook appenders this RPC serves. Two shapes, keyed by
# params["sink"]:
#   - a name in _EVENT_JSONL_SINKS: append params["row"] as one JSON line to
#     `<project_path>/.memory/files/<sink>.jsonl` — byte-identical to what
#     completion-capture.py / dispatch-capture.py already write inline
#     today, just executed daemon-side instead of by the short-lived hook
#     process. Allow-listed (never a caller-supplied path) for the same
#     injection-safety reason telemetry_store.ALLOWED_TABLES is a constant.
#   - "nexus_feedback": the row needs log.py's own validation + open-session
#     lookup + nexus_version stamp (cmd_feedback_add in .memory/log.py) —
#     duplicating that logic here would be a second, driftable copy of real
#     business logic, so instead this fire-and-forget-spawns the SAME
#     `log.py feedback add` subprocess feedback-capture.py's inline fallback
#     already runs, just off the hook's own critical path (see
#     _spawn_feedback_add).
#
# Tranche 3 additions:
#   - "reflection_snapshot": reflection-capture.sh's own pure JSONL append
#     (session_id/file_path/action_type/one_line_summary/captured_at) — same
#     shape as the Tranche 2 JSONL sinks, just added to the allow-list.
#   - "task_mirror": task-db-mirror.sh's cross-session-continuity mirror.
#     Deliberately NOT fire-and-forget like nexus_feedback — see
#     _run_task_mirror's docstring for why this one sink blocks until the
#     write is confirmed durable.
_EVENT_JSONL_SINKS: tuple[str, ...] = ("completion_events", "router_dispatches", "reflection_snapshot")

_TASK_MIRROR_SINK = "task_mirror"
_TASK_MIRROR_TIMEOUT_S = 10.0

# TASK-093 stage 1 — F2-05's `span.emit` (broker.daemon.spans/event_bus) shipped
# fully tested but called by NOTHING in live traffic: every real dispatch-
# telemetry write either bypasses the daemon entirely (`completion-capture.py`,
# `broker.conductor.dag.record_dispatch_telemetry` both shell out to
# `log.py dispatch record` directly) or, when it DOES reach this daemon via
# `record_telemetry` (`fallback.py`'s documented future-hook-integration path),
# landed only in `TelemetryStore` -> `project.db`, never in `spans.duckdb`.
# `_emit_dispatch_span_from_telemetry` below is the broker-src-only bridge:
# whenever a `dispatch_telemetry` row reaches THIS RPC, it also durably
# materializes a matching `dispatch`-kind span on the SAME single-writer
# connection (ADR-001) — closing the "built but never called" gap at the one
# point stage 1's write-surface (`nexus-broker/src/**` only, never
# `.claude/hooks/**`) can reach. See `wtcs.py`'s module docstring for the full
# root-cause chain and `spans.py`'s `_DISPATCH_STR_ATTRS`/`_DISPATCH_NUMERIC_
# ATTRS` for the attribute schema this synthesizes into.
_DISPATCH_TELEMETRY_TABLE = "dispatch_telemetry"

# `dispatch_telemetry.marker` is a free-form terminal-marker string (DONE|
# REVISE|BLOCKED|... — `.memory/schema.sql`); a span's `status` is the fixed
# `spans.VALID_STATUSES` set. Only DONE/BLOCKED map onto a real OK/ERROR
# outcome; every other marker (REVISE, an unrecognized/future one, or none at
# all) is UNSET — never guessed into a false OK or ERROR.
_MARKER_TO_SPAN_STATUS: dict[str, str] = {"DONE": "OK", "BLOCKED": "ERROR"}


def _emit_dispatch_span_from_telemetry(state: DaemonState, row: dict[str, Any]) -> None:
    """Best-effort: synthesize + durably record one `dispatch`-kind span from
    a `dispatch_telemetry` row, on `state.event_bus.span_store` (the same
    single daemon-resident DuckDB writer connection `span.emit` itself uses —
    ADR-001, one writer per process).

    Silent no-op (never raises, never logs) when `session_id` is missing or
    empty: `dispatch_telemetry.session_id` is nullable (a row recorded before
    a session_id was ever attached) but a span's `trace_id` is REQUIRED
    non-empty (`spans.validate_span`) — with no session_id there is no real
    trace to attach to, and synthesizing a fake one would misrepresent
    unrelated dispatches as belonging to the same trace. `dispatch_id` (also
    nullable) gets a fresh generated `span_id` when absent instead, since a
    span's own id (unlike its trace) has no meaningful existing value to
    inherit.

    ANY other failure — a surprising row shape tripping `spans.
    SpanValidationError`, a closed/unavailable span store — is the caller's
    (`handle_request`'s `record_telemetry` branch) responsibility to swallow:
    this function itself does not catch, so a caller who wants a different
    policy is free to choose one; the current wiring wraps this call in
    `contextlib.suppress(Exception)` because a span-emit failure must never
    fail the `dispatch_telemetry` write it is riding along with.

    TASK-094: `workflow_id`/`phase_id` are read off `row` even though
    `dispatch_telemetry` (`.memory/schema.sql`) does not carry those columns
    today — `TelemetryStore.record`'s own column allow-list already drops
    unknown keys before the SQLite write, so a caller that includes them in
    the RPC row (a future Leg-B hook) costs nothing extra here and the span
    bridge picks them up for free the moment that caller exists. Absent, they
    are `None` — `spans.validate_span` treats an absent first-class key/
    attribute exactly like it always has.
    """
    session_id = row.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        return
    dispatch_id = row.get("dispatch_id")
    span_id = dispatch_id if isinstance(dispatch_id, str) and dispatch_id else f"span-{uuid.uuid4().hex}"
    persona = row.get("persona")
    marker = row.get("marker")
    workflow_id = row.get("workflow_id")
    phase_id = row.get("phase_id")
    task_id = row.get("task_id")
    span = {
        "trace_id": session_id,
        "span_id": span_id,
        "name": f"dispatch:{persona}" if isinstance(persona, str) and persona else "dispatch",
        "kind": "dispatch",
        "status": _MARKER_TO_SPAN_STATUS.get(marker, "UNSET") if isinstance(marker, str) else "UNSET",
        "duration_ms": row.get("duration_ms"),
        "tokens": row.get("tokens"),
        "workflow_id": workflow_id,
        "phase_id": phase_id,
        "task_id": task_id,
        "attributes": {
            "task_id": task_id,
            "workflow_id": workflow_id,
            "phase_id": phase_id,
            "persona": persona,
            "model": row.get("model"),
            "marker": marker,
            "tokens": row.get("tokens"),
            "tokens_in": row.get("tokens_in"),
            "tokens_out": row.get("tokens_out"),
            "tokens_cache_read": row.get("tokens_cache_read"),
            "tokens_cache_creation": row.get("tokens_cache_creation"),
            "token_source": row.get("token_source"),
            "tool_uses": row.get("tool_uses"),
            "error_class": row.get("error_class"),
            "revise_reasons": row.get("revise_reasons"),
        },
    }
    state.event_bus.span_store.record(span)


def _telemetry_recorded_at() -> str:
    """ONE ISO-8601 UTC stamp for a dispatch dual-write — the SAME value is
    written to `project.db` and to the event log so the parity clock's
    (dispatch_id, session_id, recorded_at) key lines up (design §5.2). Format
    matches `event_store._now_iso` exactly."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: UP017


def _dual_write_dispatch_telemetry(state: DaemonState, row: dict[str, Any]) -> None:
    """F3-03 dual-write (DEC-097 Option B) — dispatch_telemetry ONLY.

    Stamp ONE `recorded_at`, write the primary `project.db` telemetry row
    (unchanged source of truth — plans/07 §1 constraint 1) AND append a
    matching `dispatch.completed` event (event_version=1) to the single-writer
    event log carrying the SAME stamp, so the parity clock's (dispatch_id,
    session_id, recorded_at) key lines up across both stores (design §5.2 trap).

    FAIL-OPEN: the primary telemetry write happens FIRST and unconditionally;
    ANY event-log failure (a locked file, a DuckDB error, a surprising row
    shape) is logged LOUDLY and swallowed — the shadow event log must NEVER
    block or fail the write it rides along with. `project.db` stays
    authoritative until the F3-03 cutover gate (design §5.2 C-06)."""
    recorded_at = row.get("recorded_at") or _telemetry_recorded_at()
    stamped = {**row, "recorded_at": recorded_at}
    state.telemetry.record(_DISPATCH_TELEMETRY_TABLE, stamped)
    try:
        dispatch_id = row.get("dispatch_id")
        aggregate_id = (
            dispatch_id if isinstance(dispatch_id, str) and dispatch_id else f"disp-{uuid.uuid4().hex}"
        )
        state.event_store.append(
            {
                "event_type": "dispatch.completed",
                "event_id": f"disp-ev-{uuid.uuid4().hex}",
                "aggregate_id": aggregate_id,
                "event_version": 1,
                "session_id": row.get("session_id") or None,
                "payload": stamped,
            },
            recorded_at=recorded_at,
        )
    except Exception:  # noqa: BLE001 — F3-03 shadow dual-write is fail-open; never fail the primary telemetry write
        LOG.exception("F3-03 dual-write to event log failed; primary telemetry write unaffected")


def _run_task_mirror(project_path: Path, row: dict[str, Any]) -> dict[str, Any]:
    """AWAITED (blocking) `log.py task mirror-native` write.

    Unlike `_spawn_feedback_add`'s fire-and-forget `asyncio.create_task`, this
    runs the subprocess synchronously and returns only once it has actually
    completed. task-db-mirror.sh exists for exactly one reason — cross-
    session continuity of the native, session-scoped task list — so a
    fire-and-forget spawn here could let a session end (and a later session
    start read project.db) before the mirror write actually landed. Blocking
    the single-threaded event loop for the duration of one `log.py` subprocess
    (a rare PostToolUse:TaskCreate|TaskUpdate event, not a hot per-turn path
    like emit_heartbeat) is the deliberate trade for that guarantee.

    Bounded by _TASK_MIRROR_TIMEOUT_S so a stuck subprocess (e.g. a sqlite
    lock) can never hang the daemon forever — a timeout or any launch failure
    is reported as not-accepted, so the hook's own inline fallback runs.
    `_upsert_native_task` (log.py) is upsert-shaped on native id, so a
    duplicate inline retry after a daemon timeout is a harmless re-write, not
    a double-count.
    """
    log_py = project_path / ".memory" / "log.py"
    if not log_py.is_file():
        return {"accepted": False, "sink": _TASK_MIRROR_SINK, "reason": "log.py missing"}
    op = str(row.get("op") or "")
    native_id = str(row.get("native_id") or "")
    if not op or not native_id:
        raise ValueError("task_mirror row requires op + native_id")
    cmd = [sys.executable, str(log_py), "task", "mirror-native", "--op", op, "--native-id", native_id]
    for key, flag in (
        ("subject", "--subject"),
        ("description", "--description"),
        ("status", "--status"),
        ("owner", "--owner"),
    ):
        val = row.get(key)
        if val:
            cmd += [flag, str(val)]
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(project_path),
            capture_output=True,
            text=True,
            timeout=_TASK_MIRROR_TIMEOUT_S,
        )
    except Exception:  # noqa: BLE001 — any launch/timeout failure => not-accepted, fall back
        return {"accepted": False, "sink": _TASK_MIRROR_SINK}
    return {"accepted": proc.returncode == 0, "sink": _TASK_MIRROR_SINK}


def _handle_record_event(state: DaemonState, params: dict[str, Any]) -> dict[str, Any]:
    sink = params.get("sink")
    row = params.get("row")
    if not isinstance(sink, str) or not isinstance(row, dict):
        raise ValueError("record_event requires sink:str + row:dict")
    if sink in _EVENT_JSONL_SINKS:
        path = state.project_path / ".memory" / "files" / f"{sink}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as fh:
            fh.write(json.dumps(row) + "\n")
        return {"accepted": True, "sink": sink}
    if sink == "nexus_feedback":
        _spawn_feedback_add(state.project_path, row)
        return {"accepted": True, "sink": sink, "queued": True}
    if sink == _TASK_MIRROR_SINK:
        return _run_task_mirror(state.project_path, row)
    raise ValueError(f"unknown record_event sink: {sink!r}")


def _spawn_feedback_add(project_path: Path, row: dict[str, Any]) -> None:
    """Fire-and-forget `log.py feedback add` under the daemon's own event loop.

    Not awaited — the caller (a `record_event` RPC handler running inside a
    synchronous `handle_request`, itself called from the async `_client_loop`)
    returns to the hook the instant the child is launched, so a slow-to-start
    `log.py` subprocess is never felt by the hook's own short RPC timeout.
    Any failure to even launch the subprocess (missing log.py, exec error) is
    swallowed — this path is advisory telemetry, never a hard requirement.
    """
    log_py = project_path / ".memory" / "log.py"
    if not log_py.is_file():
        return
    cmd = [
        sys.executable,
        str(log_py),
        "feedback",
        "add",
        "--source",
        str(row.get("source", "")),
        "--severity",
        str(row.get("severity", "")),
        "--category",
        str(row.get("category", "")),
        "--message",
        str(row.get("message", "")),
        "--context-json",
        json.dumps(row.get("context") or {}, default=str),
    ]

    async def _run() -> None:
        with contextlib.suppress(Exception):  # noqa: BLE001 — advisory, never surfaced
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(project_path),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()

    asyncio.create_task(_run())


def _append_heartbeat(project_path: Path, row: dict[str, Any]) -> None:
    """Daemon-side twin of `_heartbeat.py`'s inline JSONL append.

    Every RPC (this one included) already resets `state.last_activity` via
    `handle_request`'s own `state.touch()` call at the top — heartbeat
    traffic landing here is exactly the keep-warm signal Tranche 2's plan
    names as the reason no resident-service fallback is needed. Column set
    is a fixed allow-list (never caller-supplied keys) so a malformed hook
    payload can only ever produce a row with missing/None fields, never an
    arbitrary-shaped one.
    """
    path = project_path / ".memory" / "files" / "hook_heartbeat.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    clean = {
        "ts": row.get("ts"),
        "hook": row.get("hook"),
        "event": row.get("event"),
        "decision": row.get("decision"),
        "latency_ms": row.get("latency_ms"),
    }
    with path.open("a") as fh:
        fh.write(json.dumps(clean, separators=(",", ":")) + "\n")


def handle_request(state: DaemonState, method: str, params: dict[str, Any]) -> Any:
    """Pure dispatch — no I/O beyond what each cache/store already does."""
    state.touch()
    if method == "health":
        return {
            "status": "ok",
            "pid": os.getpid(),
            "project_path": str(state.project_path),
            "uptime_s": time.monotonic() - state.started_at,
            "resident_version": state.source_version,
        }
    if method == "query_registry":
        entries = state.registry_cache.get()
        filtered = filter_registry(entries, params.get("query_context"))
        state.registry_queries_served += 1
        payload = {"entries": filtered}
        state.registry_bytes_served += len(json.dumps(payload))
        return payload
    if method == "schema_snapshot":
        state.schema_queries_served += 1
        return {"tables": state.schema_cache.get()}
    if method == "session_digest":
        state.session_digest_queries_served += 1
        return state.session_digest_cache.get()
    if method == "registry_query_full":
        entries = query_registry(state.project_path, params.get("query_context"))
        state.registry_query_full_queries_served += 1
        return {"entries": entries}
    if method == "record_telemetry":
        table = params["table"]
        row = params["row"]
        if table == _DISPATCH_TELEMETRY_TABLE:
            # F3-03 dual-write: stamp-once primary write + fail-open event-log append.
            _dual_write_dispatch_telemetry(state, row)
            with contextlib.suppress(Exception):  # noqa: BLE001 — advisory bridge, must never fail the telemetry write
                _emit_dispatch_span_from_telemetry(state, row)
        else:
            state.telemetry.record(table, row)
        return {"accepted": True, "pending": state.telemetry.pending_count()}
    if method == "flush_telemetry":
        return {"flushed": state.telemetry.flush(state.db_path)}
    if method == "record_event":
        return _handle_record_event(state, params)
    if method == "emit_heartbeat":
        _append_heartbeat(state.project_path, params)
        return {"accepted": True}
    if method == "budget_summary":
        return {
            "uptime_s": time.monotonic() - state.started_at,
            "registry_queries_served": state.registry_queries_served,
            "registry_bytes_served": state.registry_bytes_served,
            "schema_queries_served": state.schema_queries_served,
            "session_digest_queries_served": state.session_digest_queries_served,
            "registry_query_full_queries_served": state.registry_query_full_queries_served,
            "telemetry_rows_flushed": state.telemetry.rows_flushed,
            "telemetry_flush_count": state.telemetry.flush_count,
            "telemetry_pending": state.telemetry.pending_count(),
        }
    if method.startswith("ownership_"):
        return handle_ownership_request(state.ownership, method, params)
    if method in event_bus.EVENT_BUS_METHODS:
        return event_bus.dispatch(state.event_bus, method, params)
    raise ValueError(f"unknown method: {method!r}")


async def _client_loop(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter, state: DaemonState
) -> None:
    request: dict[str, Any] | None = None
    try:
        while True:
            line = await reader.readline()
            if not line:
                break
            try:
                request = json.loads(line)
                result = handle_request(state, request["method"], request.get("params") or {})
                response: dict[str, Any] = {"id": request.get("id"), "result": result}
            except Exception as exc:  # noqa: BLE001 — must always answer, never crash the server
                req_id = request.get("id") if isinstance(request, dict) else None
                response = {"id": req_id, "error": str(exc)}
            writer.write((json.dumps(response) + "\n").encode("utf-8"))
            await writer.drain()
    finally:
        with contextlib.suppress(OSError):
            writer.close()


async def _idle_watchdog(state: DaemonState, shutdown_event: asyncio.Event) -> None:
    """1.7 idle-shutdown: exit once no request has landed for IDLE_TIMEOUT_S."""
    while True:
        await asyncio.sleep(paths.IDLE_CHECK_INTERVAL_S)
        if time.monotonic() - state.last_activity > paths.IDLE_TIMEOUT_S:
            shutdown_event.set()
            return


def start_idle_watchdog(state: DaemonState, shutdown_event: asyncio.Event) -> asyncio.Task[None] | None:
    """Resident-mode opt-out: NEXUS_DAEMON_IDLE_TIMEOUT_S<=0 means never
    self-exit — a launchd/systemd KeepAlive owns the lifecycle instead (see
    launchd/daemon.plist.template). Returns None (no watchdog task) in that
    case, mirroring `start_drift_watch`'s None-on-not-applicable shape so a
    caller can tell "watching" apart from "resident, opted out" without
    waiting on a tick. Default (env unset, 300.0) is byte-identical to the
    prior always-on-watchdog behavior.
    """
    if paths.IDLE_TIMEOUT_S <= 0:
        return None
    return asyncio.create_task(_idle_watchdog(state, shutdown_event))


async def _flush_loop(state: DaemonState) -> None:
    """1.5 periodic write-through flush, independent of any client call landing."""
    while True:
        await asyncio.sleep(paths.FLUSH_INTERVAL_S)
        with contextlib.suppress(Exception):  # noqa: BLE001 — must not kill the loop
            state.telemetry.flush(state.db_path)


async def _drift_watch_loop(state: DaemonState, interval_s: float) -> None:
    """2.6 periodic install-drift re-check under the daemon's own event loop.

    Mirrors `_flush_loop`'s posture: runs independent of any client call
    landing, and a failed check must never kill the loop or the daemon —
    drift-watch is advisory-only (see drift_watch.py's module docstring);
    `tools/build_snapshot.sh --check` remains the sole authoritative gate.
    """
    assert state.drift_watcher is not None
    while True:
        with contextlib.suppress(Exception):  # noqa: BLE001 — advisory only, must not kill the loop
            state.drift_report = state.drift_watcher.check()
        await asyncio.sleep(interval_s)


def start_drift_watch(state: DaemonState, *, interval_s: float | None = None) -> asyncio.Task[None] | None:
    """N31 — start the 2.6 install-drift background loop for this daemon's
    project, gated meta-repo-tenant-only. Returns None (no watcher, no task)
    on any tenant lacking `nexus-package/` + `tools/build_snapshot.sh` — an
    installed product tenant never pays for or sees drift-watch. Non-None
    return sets `state.drift_watcher` immediately, before the first check has
    even run, so a caller can always tell "watching" apart from "not a
    meta-repo tenant" without waiting on the loop's first tick.
    """
    if not drift_watch.is_meta_repo_tenant(state.project_path):
        return None
    state.drift_watcher = drift_watch.watch_repo(state.project_path, ttl_s=paths.DRIFT_WATCH_TTL_S)
    resolved_interval = interval_s if interval_s is not None else paths.DRIFT_WATCH_INTERVAL_S
    return asyncio.create_task(_drift_watch_loop(state, resolved_interval))


def _already_serving(sock_path: Path) -> bool:
    """Best-effort liveness probe so two racing spawns don't both try to bind."""
    if not sock_path.exists():
        return False
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(0.2)
    try:
        s.connect(str(sock_path))
        return True
    except OSError:
        return False
    finally:
        s.close()


async def serve(project_path: Path) -> None:
    sock_path = paths.socket_path_for(project_path)
    sock_path.parent.mkdir(parents=True, exist_ok=True)

    # TASK-105 single-instance guarantee: the flock is the ONE liveness
    # authority. A socketless zombie still holds it, so a second daemon can
    # never come up beside it and race for the DB write lock.
    lock = pidfile.PidfileLock(project_path)
    if not lock.acquire():
        LOG.info("another daemon instance holds the pidfile lock for %s; exiting", project_path)
        return

    if _already_serving(sock_path):
        LOG.info("daemon already serving %s; exiting", project_path)
        lock.release()
        return

    state = DaemonState(project_path)
    lock.write_owner(pid=os.getpid(), socket_path=sock_path, version=state.source_version)
    # 1.8 stale-socket self-heal (server side) — owner-checked since TASK-105.
    pidfile.owner_checked_unlink(sock_path, lock.path)

    shutdown_event = asyncio.Event()

    async def _handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await _client_loop(reader, writer, state)

    server = await asyncio.start_unix_server(_handle, path=str(sock_path))
    loop = asyncio.get_running_loop()
    with contextlib.suppress(NotImplementedError):
        loop.add_signal_handler(signal.SIGTERM, shutdown_event.set)

    idle_task = start_idle_watchdog(state, shutdown_event)  # None when resident (idle timeout <= 0)
    flush_task = asyncio.create_task(_flush_loop(state))
    drift_task = start_drift_watch(state)  # 2.6 — None on a non-meta-repo tenant
    try:
        async with server:
            await shutdown_event.wait()
    finally:
        if idle_task is not None:
            idle_task.cancel()
        flush_task.cancel()
        if drift_task is not None:
            drift_task.cancel()
        with contextlib.suppress(Exception):  # noqa: BLE001 — best-effort final flush
            state.telemetry.flush(state.db_path)
        with contextlib.suppress(Exception):  # noqa: BLE001 — best-effort close, never blocks shutdown
            state.event_bus.close_span_store()
        with contextlib.suppress(Exception):  # noqa: BLE001 — best-effort close, never blocks shutdown
            state.close_event_store()
        with contextlib.suppress(OSError):
            # TASK-105: never unlink a socket another live daemon owns — the
            # incident's third hole (a TERMed zombie's exit cleanup unlinked
            # the LIVE daemon's socket).
            pidfile.owner_checked_unlink(sock_path, lock.path)
        lock.release()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="R4-T06 daemon pilot server")
    parser.add_argument("--project-path", required=True)
    args = parser.parse_args(argv)
    logging.basicConfig(level=os.environ.get("NEXUS_DAEMON_LOG_LEVEL", "WARNING"))
    asyncio.run(serve(Path(args.project_path).resolve()))


if __name__ == "__main__":
    main()
