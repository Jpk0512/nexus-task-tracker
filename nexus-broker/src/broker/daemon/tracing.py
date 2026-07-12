"""broker.daemon.tracing — single-trace-ID propagation + one-call
reconstruction journal for multi-phase Workflow chains. plans/08-daemon-
capability-catalog.md §3.8 (node N24, Phase B, post-reversibility-gate
build-out; N13 PASSED with a verified 50.08% wall-clock reduction).

Problem this solves: a multi-phase Workflow spans several personas and gates
across three physically different propagation surfaces — a bash hook
subprocess, the Python broker, and the conductor's DAG dispatch loop. Today a
REVISE loop or a stalled Workflow can only be reconstructed by hand-joining
scattered `dispatch_telemetry` rows. This module assigns one trace ID per
chain and gives every event on that chain — dispatch, gate, verdict — a
single home, reachable by ONE `reconstruct(trace_id)` call.

Daemon-side CACHE posture, identical to the just-landed `bus.py` (N23):
this module holds no row `project.db` doesn't already have (or won't get
through that table's own existing write path — `dispatch_telemetry` via
`broker.conductor.dag.record_dispatch_telemetry`, `validation_log` via the
gate hooks, etc.). Losing the journal — a daemon restart, a crash — loses
only cross-event RECONSTRUCTION convenience, never data; every event this
module journals is independently, durably recorded by its owning surface
already. `TraceJournal` takes no `db_path`/`project_path` and never imports
`sqlite3` — structurally incapable of writing project state, same
"documented AND structural" guarantee `bus.py`'s `EventBus` makes. No
`.memory/schema.sql` change of any kind backs this module — trace IDs live
ENTIRELY in this in-memory journal.

The three propagation surfaces this module is proven against (Phase B's own
sizing note: "only as good as its weakest link — do not shortcut any of the
three"):

  1. Bash hooks (a subprocess boundary). The env var `TRACE_ID_ENV_VAR` is
     the wire format: a caller sets it before shelling out to a hook/gate
     script; that subprocess's own environment carries it forward to
     whatever it shells out to next. `trace_id_from_env()` / `propagate_env()`
     are this surface's read/write halves — proven in this node's tests
     against a REAL bash subprocess (not a Python stand-in). No file under
     `.claude/hooks/**` is touched by this node (write-scope boundary) — the
     live hook-side wiring that reads/sets this env var is future,
     cross-release scope, the same posture `bus.py` documents for its own
     not-yet-wired producer.

  2. The Python broker — direct in-process calls: `TraceJournal.record(...)`.

  3. The conductor — `broker.conductor.dag.run_dag` already exposes a
     `telemetry_sink` extension point (a plain parameter, no dag.py edit
     required). `dag_telemetry_sink()` builds a sink compatible with that
     parameter, turning each dispatched node's `DispatchTelemetry` into a
     journaled `dispatch_completed` event tagged with that node's trace ID —
     proven in this node's tests against the REAL `run_dag` scheduler (a
     genuine multi-node DAG, stubbed dispatch functions only, per this
     repo's existing `test_conductor_dag.py` convention).

A fourth wiring point ties this journal to the N23 event bus without either
module importing the other at module scope: `bus_trace_recorder()` returns a
callback shaped to consume a `bus.Event` (kind/payload/...) and journal it,
extracting the trace ID from the event's own payload — the same "future
subscribe/unsubscribe RPC wiring" gap `bus.py` leaves for its own module,
left open here on purpose rather than hard-coupling two independently-owned
daemon modules.

Untraced-event contract (this node's own acceptance criterion): an event
arriving with no trace ID is NEVER dropped and NEVER blocks the caller — it
is journaled into a separate `untraced()` bucket instead of raising or being
silently discarded, exactly the same "counted, never silently lost" posture
`bus.py` uses for a full subscriber queue.
"""
from __future__ import annotations

import itertools
import os
import threading
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

TRACE_ID_ENV_VAR = "NEXUS_TRACE_ID"


def new_trace_id() -> str:
    """Assign a fresh trace ID for the start of a Workflow chain. Format is
    an implementation detail (opaque token) — callers must never parse it."""
    return f"trace-{uuid.uuid4().hex}"


def ensure_trace_id(inbound: str | None) -> str:
    """Propagation-boundary helper: keep an inbound trace ID if one was
    already assigned upstream, otherwise mint a new one. This is the single
    call every one of the three surfaces makes at its own entry point so a
    chain gets exactly ONE trace ID no matter which surface originates it."""
    return inbound if inbound else new_trace_id()


def _observed_at() -> str:
    """ISO-8601 UTC, second precision — same shape as `bus._observed_at` /
    `skill_load_recorder._observed_at` so events read identically on the wire
    regardless of which module produced them."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── surface 1: bash-hook subprocess-boundary propagation ───────────────────


def trace_id_from_env(env: Mapping[str, str] | None = None) -> str | None:
    """Read half of the subprocess-boundary contract: extract the trace ID a
    parent process set via `TRACE_ID_ENV_VAR`. Defaults to the real process
    environment; a mapping is injectable for tests / non-`os.environ`
    subprocess result capture. Returns None (never raises) when absent —
    absence is a normal, untraced-event case, not an error."""
    source = env if env is not None else os.environ
    value = source.get(TRACE_ID_ENV_VAR)
    return value or None


def propagate_env(trace_id: str, env: Mapping[str, str] | None = None) -> dict[str, str]:
    """Write half of the subprocess-boundary contract: return an environment
    mapping (copy of `env`, or the real process environment if omitted) with
    `TRACE_ID_ENV_VAR` set — pass the result as `subprocess.run(..., env=...)`
    when shelling out to a hook/gate script so the trace ID survives the
    process boundary."""
    if not trace_id:
        raise ValueError("propagate_env requires a non-empty trace_id")
    base = dict(env) if env is not None else dict(os.environ)
    base[TRACE_ID_ENV_VAR] = trace_id
    return base


# ── surface 3: conductor node-dict propagation ──────────────────────────────


def attach_trace_id(node: dict[str, Any], trace_id: str) -> dict[str, Any]:
    """Thread a trace ID onto a node-contract node dict (a plain extra key —
    `broker.node_contract.validate_dag` checks required fields, not a closed
    schema, so this is additive and never trips validation). The companion
    read-side is a caller-supplied `trace_id_for_node` callable passed to
    `dag_telemetry_sink`, kept as a callable rather than a hardcoded key
    lookup so a caller free to store the trace ID elsewhere (e.g. a
    node_id -> trace_id map assembled once per Workflow) isn't forced onto
    this module's own convention."""
    return {**node, "trace_id": trace_id}


def trace_id_from_node(node: dict[str, Any]) -> str | None:
    """Default `trace_id_for_node` reader, paired with `attach_trace_id`."""
    value = node.get("trace_id")
    return value or None


# ── the journal itself ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class TraceEvent:
    """One journaled event. `seq`/`ts` are always the journal's own
    counter/clock — never caller-suppliable — the same "WHAT is
    caller-supplied, WHEN/ORDER is not" contract `bus.Event` uses, for the
    same reason: a caller claiming an event happened at a time/order it did
    not would corrupt reconstruction for every consumer of that trace."""

    seq: int
    trace_id: str | None
    kind: str
    source: str
    node_id: str | None
    payload: dict[str, Any]
    ts: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "trace_id": self.trace_id,
            "kind": self.kind,
            "source": self.source,
            "node_id": self.node_id,
            "payload": self.payload,
            "ts": self.ts,
        }


KNOWN_SOURCES = frozenset({"hook", "broker", "conductor"})


class TraceJournal:
    """Daemon-side, in-memory, per-trace-ID event journal.

    Unlike `bus.EventBus.publish` (which rejects an unknown event `kind`),
    `record()` accepts ANY kind string and ANY (or no) trace ID — the
    untraced-event acceptance criterion ("never dropped and never blocking")
    means this journal must not become a second gate a real hook/dispatch/
    gate event can fail. Validation is the bus's job at publish time; this
    module's job is to never lose or stall an event once it arrives.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._seq_counter = itertools.count(1)
        self._traces: dict[str, list[TraceEvent]] = {}
        self._untraced: list[TraceEvent] = []

    def record(
        self,
        trace_id: str | None,
        kind: str,
        payload: dict[str, Any],
        *,
        source: str,
        node_id: str | None = None,
    ) -> TraceEvent:
        """Journal one event. Fully synchronous, lock-bounded only for the
        dict/list append itself (no I/O, no await) — never blocks on a slow
        consumer because this module has no consumer, only a store."""
        event = TraceEvent(
            seq=next(self._seq_counter),
            trace_id=trace_id or None,
            kind=kind,
            source=source,
            node_id=node_id,
            payload=dict(payload),
            ts=_observed_at(),
        )
        with self._lock:
            if event.trace_id:
                self._traces.setdefault(event.trace_id, []).append(event)
            else:
                self._untraced.append(event)
        return event

    def reconstruct(self, trace_id: str) -> list[dict[str, Any]]:
        """THE one-call reconstruction query (this node's core acceptance
        criterion): every dispatch/gate/verdict event correlated to
        `trace_id`, across all three propagation surfaces, ordered by the
        journal's own monotonic `seq` — never the events' own (possibly
        skewed, possibly clock-drifted across a subprocess boundary) `ts`."""
        with self._lock:
            events = list(self._traces.get(trace_id, ()))
        return [e.to_dict() for e in sorted(events, key=lambda e: e.seq)]

    def untraced(self) -> list[dict[str, Any]]:
        """Every event journaled without a trace ID — proof-of-retention for
        the "never dropped" half of the untraced-event acceptance criterion."""
        with self._lock:
            events = list(self._untraced)
        return [e.to_dict() for e in sorted(events, key=lambda e: e.seq)]

    def trace_ids(self) -> list[str]:
        with self._lock:
            return sorted(self._traces.keys())

    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "trace_count": len(self._traces),
                "event_count": sum(len(v) for v in self._traces.values()),
                "untraced_count": len(self._untraced),
            }


# ── surface-3 wiring: broker.conductor.dag's existing telemetry_sink hook ──


def dag_telemetry_sink(
    journal: TraceJournal,
    trace_id_for_node: Callable[[dict[str, Any]], str | None] = trace_id_from_node,
    *,
    source: str = "conductor",
) -> Callable[[dict[str, Any], Any], None]:
    """Build a `telemetry_sink` callable matching `broker.conductor.dag.run_dag`'s
    existing `telemetry_sink: Callable[[dict, DispatchTelemetry], None]`
    parameter — no edit to `dag.py` required, that parameter already exists.
    Wire it in with `run_dag(doc, ..., telemetry_sink=dag_telemetry_sink(journal))`.

    `telemetry` is accepted as `Any` (duck-typed, not imported from
    `broker.conductor.dag`) deliberately: this module must not import the
    conductor to stay usable standalone / avoid an import cycle risk, exactly
    the same decoupling `bus.py` keeps from every future producer module.
    """

    def _sink(node: dict[str, Any], telemetry: Any) -> None:
        trace_id = trace_id_for_node(node)
        journal.record(
            trace_id,
            "dispatch_completed",
            {
                "executor": getattr(telemetry, "executor", None),
                "ok": getattr(telemetry, "ok", None),
                "duration_ms": getattr(telemetry, "duration_ms", None),
                "error": getattr(telemetry, "error", None),
            },
            source=source,
            node_id=getattr(telemetry, "node_id", node.get("node_id")),
        )

    return _sink


# ── bus wiring: N23 EventBus -> this journal, without a hard import ────────


def bus_trace_recorder(
    journal: TraceJournal,
    *,
    trace_key: str = "trace_id",
    source: str = "broker",
) -> Callable[[Any], None]:
    """Build a callback shaped to consume one `bus.Event` (duck-typed on
    `.kind`/`.payload` — no import of `broker.daemon.bus` here, same
    decoupling rationale as `dag_telemetry_sink`) and journal it, reading the
    trace ID out of the event's own payload under `trace_key`. This is the
    wiring point a future daemon subscriber-drain loop plugs in — call this
    for every event a `bus.EventBus` subscription delivers — left
    unconnected here on purpose, matching `bus.py`'s own "no live producer
    wired yet" posture for its RPC transport half."""

    def _consume(event: Any) -> None:
        payload = dict(getattr(event, "payload", {}) or {})
        trace_id = payload.get(trace_key)
        journal.record(
            trace_id,
            getattr(event, "kind", "unknown"),
            payload,
            source=source,
            node_id=payload.get("node_id"),
        )

    return _consume
