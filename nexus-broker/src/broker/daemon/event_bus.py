"""Daemon-resident event-bus core — F2-02 (`nexus-foundation/plans/artifacts/
event-bus-design.md` + `event-taxonomy.json`, wave-2.md §(d)).

Implements the RPC surface the design doc's §2 defines on top of the existing
`_daemon_rpc.py` unix-socket transport: `event.emit` (tranche-A advisory),
`event.verify` (tranche-B deny-capable), `health.ping`, `governance.reload`,
and `span.emit` (F2-05 — single-writer DuckDB append via `spans.SpanStore`,
see that module's docstring; upgraded from the F2-02 forward-stub, notepad
gotcha #331). This is NOT
`bus.py` (the in-process pub-sub bus for cockpit-style session-event
subscribers, node N23) — that module's five `dispatch_started`/`gate_denied`/
... kinds have no relationship to the 16-event hook-migration taxonomy this
module hosts. The two are separate daemon capabilities with separate RPC
surfaces and coexist without overlap.

Resident governance state (design doc §4): the taxonomy is read ONCE from the
canonical on-disk `event-taxonomy.json` at construction and held as a
read-through cache; `governance.reload` re-hydrates it from disk on demand.
A full daemon PROCESS restart (picking up new Python code, e.g. this file's
own next edit) is `ensure-daemon.sh` + the daemon's own restart-on-update
lineage's job — out of this module's scope. `governance.reload` only
re-hydrates the DATA this module caches, mirroring the design doc's "Trigger
resident-state re-hydration (or daemon self-detects source mtime and
self-restarts)" semantics for the state layer this module owns.

FAIL POLICY IS A BUS CONTRACT, NOT JUST DOCUMENTATION (C-06, notepad gotcha
#327): `event.emit` only accepts tranche-A events and `event.verify` only
accepts tranche-B events — calling the wrong RPC for an event's tranche is a
hard `ValueError`, never a silent cross-tranche fallthrough. The actual
fail-OPEN vs fail-CLOSED behaviour on a genuinely DEAD/unreachable daemon
cannot be implemented here — a server-side handler only ever runs when the
daemon IS reachable. That miss-policy is the client's job: see
`_daemon_rpc.py`'s `call_advisory` (fail open) / `call_deny_capable` (fail
closed) wrappers, which are structurally distinct on a miss so a caller can
never confuse one policy's miss-shape for the other's.

DEC-085 — bundled taxonomy default: a project without its own
`nexus-foundation/plans/artifacts/event-taxonomy.json` (any non-meta-repo /
target-install tenant) now falls back to the taxonomy shipped alongside this
module at `nexus-broker/src/broker/daemon/data/event-taxonomy.json`
(`taxonomy_path_for`), kept in sync with the canonical source by
`tools/build_snapshot.sh`'s `sync_broker` step. Both files describe the SAME
static 16-event bus schema — it is not project-specific data — so a target
install hydrating the bundled copy is correct, not degraded; this closes the
C-07 gap where `event.emit` on an empty taxonomy raised `ValueError`, which
`_daemon_rpc.call_advisory`'s miss contract turned into a PERMANENTLY lost
advisory banner on every installed project (never the meta-repo, which
always had its own canonical file). Only a tenant with NEITHER file present
(a bare unit-test fixture constructing `EventTaxonomy` directly against a
path that doesn't exist, e.g. `test_taxonomy_empty_when_file_absent`) still
gets an EMPTY resident taxonomy, never a construction-time crash —
`DaemonState.__init__` must stay safe to call for every existing daemon
tenant/fixture, the same not-applicable-tenant posture
`drift_watch.is_meta_repo_tenant` already documents for install-drift.
"""
from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from broker.daemon import advisory_handlers, deny_handlers, docs_watcher
from broker.daemon.spans import SpanStore, spans_db_path_for

TRANCHE_A = "A"
TRANCHE_B = "B"

FAIL_POLICY_ADVISORY = "advisory-fail-open"
FAIL_POLICY_DENY_CAPABLE = "deny-capable-fail-closed"

_TAXONOMY_RELATIVE_PATH = Path("nexus-foundation") / "plans" / "artifacts" / "event-taxonomy.json"

# DEC-085 — the broker-bundled default, sibling to this module, kept in sync
# with the canonical source by tools/build_snapshot.sh's sync_broker step.
_BUNDLED_TAXONOMY_PATH = Path(__file__).resolve().parent / "data" / "event-taxonomy.json"

_NO_TAXONOMY_DIGEST = "no-taxonomy"


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def taxonomy_path_for(project_path: Path) -> Path:
    """Prefer the project's own canonical on-disk taxonomy (this meta-repo's
    `nexus-foundation/plans/artifacts/event-taxonomy.json`); fall back to the
    broker-bundled default (DEC-085) for every other tenant — see this
    module's docstring.
    """
    project_taxonomy = project_path / _TAXONOMY_RELATIVE_PATH
    if project_taxonomy.is_file():
        return project_taxonomy
    return _BUNDLED_TAXONOMY_PATH


@dataclass(frozen=True)
class EventDef:
    name: str
    tranche: str
    fail_policy: str
    payload_sketch: dict[str, Any]
    producing_hook_events: tuple[str, ...]
    consumers: tuple[str, ...]
    note: str = ""


class EventTaxonomy:
    """Resident, read-through cache of `event-taxonomy.json` — hydrated once
    at construction, re-hydrated on demand via `reload()` (the
    `governance.reload` RPC's underlying action).
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._events: dict[str, EventDef] = {}
        self.fail_policy_classes: dict[str, str] = {}
        self.loaded_at = ""
        self.content_digest = _NO_TAXONOMY_DIGEST
        self.load()

    def load(self) -> None:
        if not self.path.is_file():
            self._events = {}
            self.fail_policy_classes = {}
            self.loaded_at = _now_iso()
            self.content_digest = _NO_TAXONOMY_DIGEST
            return
        raw = self.path.read_bytes()
        data = json.loads(raw.decode("utf-8"))
        events: dict[str, EventDef] = {}
        for entry in data.get("events", []):
            name = entry["name"]
            events[name] = EventDef(
                name=name,
                tranche=entry["tranche"],
                fail_policy=entry["fail_policy"],
                payload_sketch=dict(entry.get("payload_sketch") or {}),
                producing_hook_events=tuple(entry.get("producing_hook_events") or ()),
                consumers=tuple(entry.get("consumers") or ()),
                note=entry.get("note", ""),
            )
        self._events = events
        self.fail_policy_classes = dict(data.get("fail_policy_classes") or {})
        self.loaded_at = _now_iso()
        self.content_digest = hashlib.sha256(raw).hexdigest()[:12]

    def reload(self) -> None:
        self.load()

    def get(self, name: str) -> EventDef:
        try:
            return self._events[name]
        except KeyError:
            raise ValueError(f"unknown event: {name!r}") from None

    @property
    def event_count(self) -> int:
        return len(self._events)


class EventBusState:
    """Per-daemon resident event-bus state — one instance held on
    `DaemonState` (see server.py), constructed at daemon start.
    """

    def __init__(self, project_path: Path) -> None:
        self.project_path = project_path
        self.taxonomy = EventTaxonomy(taxonomy_path_for(project_path))
        self.started_at = time.monotonic()
        self.emit_count = 0
        self.verify_count = 0
        self.span_count = 0
        # F2-05 — lazy: only opened on the first span.emit, so the many
        # pre-existing daemon tests that never touch spans never pay a
        # DuckDB-connect cost or leave a spans.duckdb file behind them.
        self._span_store: SpanStore | None = None

    @property
    def span_store(self) -> SpanStore:
        if self._span_store is None:
            self._span_store = SpanStore(spans_db_path_for(self.project_path))
        return self._span_store

    def close_span_store(self) -> None:
        """Release the DuckDB write connection (daemon shutdown path, and
        tests that need to read the file back from a second connection —
        DuckDB permits only one open writer per file, see spans.py)."""
        if self._span_store is not None:
            self._span_store.close()
            self._span_store = None


def _require_name(params: dict[str, Any]) -> str:
    name = params.get("name")
    if not isinstance(name, str) or not name:
        raise ValueError("requires name:str")
    return name


def handle_event_emit(state: EventBusState, params: dict[str, Any]) -> dict[str, Any]:
    """Tranche-A only. A tranche-B event routed here is a caller bug — the
    bus rejects it rather than silently emitting a deny-capable event as a
    fire-and-forget advisory one (constraint 2: fail policy is first-class).

    F2-03: when `params["consumer"]` names a migrated hook (its own
    filename stem — several consumers share one event `name`, e.g. 7 files
    all fire "session.start"; `consumer` disambiguates which one's ported
    logic to run), the resident `advisory_handlers` dispatch computes that
    consumer's `advisory_context` (its stdout/stderr/exit_code, faithfully
    ported from the pre-migration hook body — see advisory_handlers.py). No
    `consumer` (or an unmigrated one) yields an empty `advisory_context`,
    matching the bus's pre-F2-03 stub-emit behaviour exactly.

    F2-07: `doc.written` is a single-consumer event (`docs_watcher.py`, per
    event-taxonomy.json), so it is special-cased by name here rather than
    routed through a shared multi-consumer dispatch table (kept minimal +
    additive to avoid colliding with the F2-03 consumer-dispatch migration
    also touching this function). Every other event name's return shape is
    byte-for-byte unchanged; `watcher_report` is only ever added for
    `doc.written`, alongside the (independent) `advisory_context` key.
    """
    name = _require_name(params)
    event_def = state.taxonomy.get(name)
    if event_def.tranche != TRANCHE_A:
        raise ValueError(
            f"event.emit is for tranche-A advisory events only; {name!r} is "
            f"tranche {event_def.tranche} — use event.verify"
        )
    state.emit_count += 1
    advisory_context: dict[str, Any] = {}
    consumer = params.get("consumer")
    payload = params.get("payload") if isinstance(params.get("payload"), dict) else {}
    if isinstance(consumer, str) and consumer:
        env = params.get("env") if isinstance(params.get("env"), dict) else {}
        advisory_context = advisory_handlers.compute_advisory(state.project_path, consumer, payload, env)
    result: dict[str, Any] = {
        "ok": True,
        "event": name,
        "tranche": TRANCHE_A,
        "fail_policy": event_def.fail_policy,
        "advisory_context": advisory_context,
    }
    if name == "doc.written":
        result["watcher_report"] = docs_watcher.on_doc_written(state.project_path, payload)
    return result


def handle_event_verify(state: EventBusState, params: dict[str, Any]) -> dict[str, Any]:
    """Tranche-B only. F2-04: real per-consumer verdict compute
    (`deny_handlers.compute_verdict` — `broker-gate.py`, `secret-path-guard.sh`,
    ... faithfully ported, see that module's docstring for the exact scope),
    replacing the F2-02 stub-allow (notepad F2-04 #330). Mirrors
    `handle_event_emit`'s F2-03 consumer-dispatch shape exactly: when
    `params["consumer"]` names a migrated tranche-B hook (its own filename
    stem), the resident `deny_handlers` dispatch computes that consumer's
    real allow/deny verdict from real on-disk state. No `consumer` (or an
    unmapped one) yields the same neutral "allow" this handler always
    returned pre-F2-04 — every existing caller that never passes `consumer`
    (F2-02's own RPC-surface tests) is unaffected byte-for-byte.

    SHADOW ONLY (C-06): whatever this returns is NEVER authoritative during
    F2-04 — the retained hook body is the sole authoritative decision-maker
    until cutover (>=2 shadow sessions, zero unexplained divergence). The
    daemon-UNREACHABLE case (fail CLOSED) is never seen here — a server-side
    handler only runs when the daemon IS reachable; see
    `_daemon_rpc.call_deny_capable` for the client-side miss policy.
    """
    name = _require_name(params)
    event_def = state.taxonomy.get(name)
    if event_def.tranche != TRANCHE_B:
        raise ValueError(
            f"event.verify is for tranche-B deny-capable events only; {name!r} is "
            f"tranche {event_def.tranche} — use event.emit"
        )
    state.verify_count += 1

    consumer = params.get("consumer")
    payload = params.get("payload") if isinstance(params.get("payload"), dict) else {}
    if isinstance(consumer, str) and consumer:
        env = params.get("env") if isinstance(params.get("env"), dict) else {}
        verdict = deny_handlers.compute_verdict(state.project_path, consumer, payload, env)
    else:
        verdict = {
            "decision": "allow",
            "reason": "no consumer specified — neutral verdict (F2-02 RPC-surface shape, unchanged)",
            "code": "",
        }

    return {
        "decision": verdict.get("decision", "allow"),
        "reason": verdict.get("reason", ""),
        "code": verdict.get("code", ""),
        "event": name,
        "consumer": consumer if isinstance(consumer, str) else None,
        "tranche": TRANCHE_B,
        "fail_policy": event_def.fail_policy,
    }


def handle_health_ping(state: EventBusState, params: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": "ok",
        "resident_version": state.taxonomy.content_digest,
        "loaded_at": state.taxonomy.loaded_at,
        "event_count": state.taxonomy.event_count,
    }


def handle_governance_reload(state: EventBusState, params: dict[str, Any]) -> dict[str, Any]:
    previous_digest = state.taxonomy.content_digest
    state.taxonomy.reload()
    return {
        "reloaded": True,
        "changed": state.taxonomy.content_digest != previous_digest,
        "resident_version": state.taxonomy.content_digest,
        "loaded_at": state.taxonomy.loaded_at,
        "event_count": state.taxonomy.event_count,
    }


def handle_span_emit(state: EventBusState, params: dict[str, Any]) -> dict[str, Any]:
    """F2-05 — single-writer DuckDB span append (ADR-001 Tier 2, FDEC-8
    OTLP-compatible model). Upgraded from the F2-02 forward-stub (notepad
    #331: "content-shape validation deferred to F2-05 storage"): every span
    now crosses `spans.validate_span` at the write boundary (via
    `SpanStore.record`) before it ever reaches disk. A malformed span raises
    `spans.SpanValidationError` (typed, never a bare `ValueError`, never a
    silent drop) — the daemon itself never crashes on a bad span because
    `server.handle_request`'s caller (`_client_loop`) already turns ANY
    handler exception into an `{"error": ...}` RPC response, the same
    contract every other bus handler here relies on.
    """
    span = params.get("span")
    if not isinstance(span, dict):
        raise ValueError("span.emit requires span:dict")
    recorded = state.span_store.record(span)
    state.span_count += 1
    return {"accepted": True, "trace_id": recorded["trace_id"], "span_id": recorded["span_id"]}


EVENT_BUS_METHODS: dict[str, Callable[[EventBusState, dict[str, Any]], dict[str, Any]]] = {
    "event.emit": handle_event_emit,
    "event.verify": handle_event_verify,
    "health.ping": handle_health_ping,
    "governance.reload": handle_governance_reload,
    "span.emit": handle_span_emit,
}


def dispatch(state: EventBusState, method: str, params: dict[str, Any]) -> dict[str, Any]:
    return EVENT_BUS_METHODS[method](state, params)
