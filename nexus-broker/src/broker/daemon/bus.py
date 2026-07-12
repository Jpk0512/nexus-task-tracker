"""In-process pub-sub event bus for session events — plans/08-daemon-
capability-catalog.md §3.3 (node N23, Phase B, post reversibility-gate).

Publishes session-lifecycle events (dispatch started/completed, gate
denied, Lens verdict recorded, skill-load observed — the five kinds this
node's brief names, the last one tying directly to N20's
`SkillLoadRecorder` observed-event shape) to any number of live
subscribers, each holding a bounded, per-subscriber `asyncio.Queue`.

Push-only over CACHE state (plans/07 §1 constraint 1, the same posture
`telemetry_store.py` already documents): this module holds no row
`project.db` doesn't already have, or will get through the existing 1.5
write-through path. Losing the bus — a daemon restart, a slow/dead
subscriber getting reaped, a queue overflow — loses only IN-FLIGHT
NOTIFICATIONS, never data; nothing here is a durable store and nothing
here is on any path that writes `project.db`. A subscriber that
reconnects after a gap has simply missed the events published during the
gap, exactly like a missed push notification — it can always re-derive
full state from `project.db` (or the daemon's own `query_registry`/
`schema_snapshot` cold-read methods), which is what makes this safe to
lose. `EventBus` takes no `db_path`/`project_path` and never imports
`sqlite3` — structurally incapable of writing project state, not just
documented as such.

Backpressure / drop policy (DOCUMENTED per this node's acceptance
criterion): each subscriber's queue is a fixed-size `asyncio.Queue`.
`publish()` NEVER awaits and NEVER blocks the daemon's serving path —
every queue operation it performs is the non-blocking `_nowait` form, so
the asyncio event loop never yields mid-publish regardless of how many
subscribers exist or how slow any one of them is to drain. On overflow
the bus drops the OLDEST queued event to make room for the newest (never
the reverse): a session-event consumer such as the cockpit push path
cares about CURRENT state, not a complete history, so keeping the
freshest N events and discarding stale ones is the correct policy — the
opposite choice (drop-newest, i.e. reject the incoming event and keep the
stale backlog) would leave a slow consumer staring at an increasingly
outdated view, which is worse for this consumer shape. A full audit trail
is not this bus's job — `project.db` (`dispatch_telemetry`,
`validation_log`, `skill_load_events`, ...) is already the durable,
complete record; the bus is a best-effort freshness signal layered on top
of it. Every drop increments both a per-subscriber and a bus-wide
counter so a consumer (or the reversibility-gate instrument, per 2.8's
precedent) can observe backpressure instead of silently losing events.

Subscriber lifecycle: `subscribe()` / `unsubscribe()` are explicit.
`mark_dead()` is for a transport layer (e.g. a future `server.py` socket-
write loop) to report a write failure immediately, without waiting for
the passive sweep. `reap_dead()` is a synchronous, non-blocking sweep a
background loop can run on an interval (mirroring the existing
`_idle_watchdog`/`_flush_loop` shape already in `server.py`) that removes
any subscriber marked dead OR idle past a TTL (no successful `touch()`/
`receive()` observed) — the passive half of lifecycle cleanup for a
consumer that vanished without a clean `unsubscribe()` (crashed,
network-dropped, etc.). Neither path ever touches a live, healthy
subscriber's queue, so a dead or slow subscriber can never stall
`publish()` or any other subscriber's delivery.

No live producer wires into this module yet, and no `subscribe`/
`unsubscribe`/`events` RPC method is added to `server.py`'s JSON-RPC
dispatch table in this node — that socket-transport wiring, plus the real
call-sites that publish an actual dispatch-started/gate-denied/Lens-
verdict/skill-load event, are future, cross-release scope, exactly the
posture `skill_load_recorder.py` documents for its own future producer.
This module ships the full pub-sub bus capability and is proven against
simulated publishers/subscribers in the meantime.
"""
from __future__ import annotations

import asyncio
import itertools
import threading
import time
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

# The five event kinds this node's brief names, verbatim.
EVENT_KIND_DISPATCH_STARTED = "dispatch_started"
EVENT_KIND_DISPATCH_COMPLETED = "dispatch_completed"
EVENT_KIND_GATE_DENIED = "gate_denied"
EVENT_KIND_LENS_VERDICT_RECORDED = "lens_verdict_recorded"
EVENT_KIND_SKILL_LOAD_OBSERVED = "skill_load_observed"

EVENT_KINDS: frozenset[str] = frozenset(
    {
        EVENT_KIND_DISPATCH_STARTED,
        EVENT_KIND_DISPATCH_COMPLETED,
        EVENT_KIND_GATE_DENIED,
        EVENT_KIND_LENS_VERDICT_RECORDED,
        EVENT_KIND_SKILL_LOAD_OBSERVED,
    }
)

DEFAULT_QUEUE_SIZE = 64


def _observed_at() -> str:
    """ISO-8601 UTC, second precision — matches `skill_load_recorder._observed_at`'s
    shape so events from that module and this bus read identically on the wire.
    """
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class Event:
    """One published event. `seq`/`ts` are always the bus's own counter/clock —
    never caller-suppliable — the same "WHAT is caller-supplied, WHEN is not"
    contract `skill_load_recorder.record_observed` uses, for the same reason:
    a caller claiming an event happened at a time it did not would corrupt
    ordering and freshness for every subscriber.
    """

    seq: int
    kind: str
    payload: dict[str, Any]
    ts: str

    def to_dict(self) -> dict[str, Any]:
        return {"seq": self.seq, "kind": self.kind, "payload": self.payload, "ts": self.ts}


@dataclass
class Subscription:
    """One subscriber's lifecycle state + bounded delivery queue."""

    id: str
    queue: asyncio.Queue[Event]
    kinds: frozenset[str] | None  # None == subscribed to every kind
    created_at: float
    last_activity: float
    delivered_count: int = 0
    dropped_count: int = 0
    alive: bool = True
    dead_reason: str = ""

    def wants(self, kind: str) -> bool:
        return self.kinds is None or kind in self.kinds


class EventBus:
    """In-process pub-sub bus. One instance per daemon (a future `server.py`
    wiring holds it on `DaemonState`, same pattern as `state.telemetry`).
    """

    def __init__(self, default_queue_size: int = DEFAULT_QUEUE_SIZE) -> None:
        if default_queue_size < 1:
            raise ValueError(f"default_queue_size must be >= 1, got {default_queue_size!r}")
        self.default_queue_size = default_queue_size
        self._subscribers: dict[str, Subscription] = {}
        self._lock = threading.Lock()
        self._seq_counter = itertools.count(1)
        self.dropped_total = 0
        self.published_total = 0

    # ── subscriber lifecycle ────────────────────────────────────────────

    def subscribe(
        self,
        kinds: Iterable[str] | None = None,
        queue_size: int | None = None,
        subscriber_id: str | None = None,
    ) -> Subscription:
        """Register a new subscriber and return its handle. The caller reads
        pushed events via `EventBus.receive()` (or `subscription.queue.get()`
        directly) and must eventually call `unsubscribe()` — or let
        `reap_dead()` collect it once it goes idle past a TTL.
        """
        kind_set: frozenset[str] | None
        if kinds is not None:
            kind_set = frozenset(kinds)
            unknown = kind_set - EVENT_KINDS
            if unknown:
                raise ValueError(f"unknown event kind(s): {sorted(unknown)!r}")
        else:
            kind_set = None

        sub_id = subscriber_id or uuid.uuid4().hex
        size = queue_size if queue_size is not None else self.default_queue_size
        if size < 1:
            # `asyncio.Queue(maxsize<=0)` means UNBOUNDED — silently accepting
            # that would break the "bounded per-subscriber queue" invariant
            # this module exists to guarantee.
            raise ValueError(f"queue_size must be >= 1, got {size!r}")
        now = time.monotonic()
        subscription = Subscription(
            id=sub_id,
            queue=asyncio.Queue(maxsize=size),
            kinds=kind_set,
            created_at=now,
            last_activity=now,
        )
        with self._lock:
            if sub_id in self._subscribers:
                raise ValueError(f"subscriber_id already registered: {sub_id!r}")
            self._subscribers[sub_id] = subscription
        return subscription

    def unsubscribe(self, subscriber_id: str) -> bool:
        """Explicit lifecycle end. Returns False if already gone — idempotent,
        so an `unsubscribe()` racing a `reap_dead()` sweep never raises.
        """
        with self._lock:
            return self._subscribers.pop(subscriber_id, None) is not None

    def mark_dead(self, subscriber_id: str, reason: str = "") -> bool:
        """A transport layer calls this the moment it observes a subscriber is
        gone (e.g. a socket write raised) — flips `alive=False` immediately so
        `publish()` stops enqueueing to it without waiting for the next
        `reap_dead()` sweep. Returns False if the subscriber is unknown.
        """
        with self._lock:
            sub = self._subscribers.get(subscriber_id)
            if sub is None:
                return False
            sub.alive = False
            sub.dead_reason = reason or "marked-dead"
            return True

    def reap_dead(
        self, idle_timeout_s: float | None = None, now: float | None = None
    ) -> list[str]:
        """Synchronous, non-blocking sweep: removes every subscriber that is
        either explicitly `mark_dead()`-flagged, or (when `idle_timeout_s` is
        given) has gone that long without a successful `touch()`/`receive()`
        — the passive half of lifecycle cleanup for a consumer that vanished
        without calling `unsubscribe()`. Never awaits and never touches a
        live subscriber's queue, so it cannot stall a publisher or a healthy
        consumer. Returns the list of reaped subscriber ids.
        """
        check_at = time.monotonic() if now is None else now
        reaped: list[str] = []
        with self._lock:
            for sub_id, sub in list(self._subscribers.items()):
                idle_expired = (
                    idle_timeout_s is not None and (check_at - sub.last_activity) > idle_timeout_s
                )
                if not sub.alive or idle_expired:
                    del self._subscribers[sub_id]
                    reaped.append(sub_id)
        return reaped

    def touch(self, subscriber_id: str) -> None:
        """Mark a subscriber as recently active — called after a successful
        drain (see `receive()`) so the idle-TTL half of `reap_dead()` never
        collects a consumer that IS still pumping its queue.
        """
        with self._lock:
            sub = self._subscribers.get(subscriber_id)
            if sub is not None:
                sub.last_activity = time.monotonic()

    async def receive(self, subscriber_id: str, timeout: float | None = None) -> Event:
        """Convenience: await the next event for one subscriber, touching its
        liveness timestamp on success. Raises `KeyError` if unknown/reaped,
        `TimeoutError` on timeout (an empty queue for a live subscriber —
        not, by itself, a dead-subscriber signal).
        """
        with self._lock:
            sub = self._subscribers.get(subscriber_id)
        if sub is None:
            raise KeyError(subscriber_id)
        if timeout is None:
            event = await sub.queue.get()
        else:
            event = await asyncio.wait_for(sub.queue.get(), timeout=timeout)
        sub.delivered_count += 1
        self.touch(subscriber_id)
        return event

    # ── publish ──────────────────────────────────────────────────────────

    def publish(self, kind: str, payload: dict[str, Any]) -> Event:
        """Fan the event out to every live, interested subscriber. Fully
        synchronous — no `await` anywhere in this method — so it can never
        block the daemon's serving path (the asyncio event loop never yields
        mid-publish) regardless of how many subscribers exist or how slow
        any one of them is to drain.
        """
        if kind not in EVENT_KINDS:
            raise ValueError(f"unknown event kind: {kind!r}")

        event = Event(
            seq=next(self._seq_counter), kind=kind, payload=dict(payload), ts=_observed_at()
        )
        self.published_total += 1

        with self._lock:
            targets = [s for s in self._subscribers.values() if s.alive and s.wants(kind)]

        for sub in targets:
            self._enqueue(sub, event)
        return event

    def _enqueue(self, sub: Subscription, event: Event) -> None:
        """Non-blocking enqueue with drop-oldest overflow (see module
        docstring for the policy rationale). Every branch here is a
        `_nowait` queue op — never an `await` — so a full or contended
        queue degrades to a counted drop, never a stall.
        """
        try:
            sub.queue.put_nowait(event)
            return
        except asyncio.QueueFull:
            pass
        try:
            sub.queue.get_nowait()  # evict the oldest to make room
            sub.dropped_count += 1
            self.dropped_total += 1
        except asyncio.QueueEmpty:
            pass  # a concurrent consumer already drained one — no drop needed yet
        try:
            sub.queue.put_nowait(event)
        except asyncio.QueueFull:
            # Pathological race (queue refilled between our get_nowait and
            # put_nowait) — count the incoming event as dropped rather than
            # raise out of publish() and break every other subscriber's fan-out.
            sub.dropped_count += 1
            self.dropped_total += 1

    # ── introspection ────────────────────────────────────────────────────

    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)

    def stats(self) -> dict[str, Any]:
        with self._lock:
            per_sub = {
                sub_id: {
                    "delivered": sub.delivered_count,
                    "dropped": sub.dropped_count,
                    "alive": sub.alive,
                    "queue_depth": sub.queue.qsize(),
                }
                for sub_id, sub in self._subscribers.items()
            }
        return {
            "published_total": self.published_total,
            "dropped_total": self.dropped_total,
            "subscriber_count": len(per_sub),
            "subscribers": per_sub,
        }


async def reap_loop(bus: EventBus, interval_s: float, idle_timeout_s: float) -> None:
    """Background sweep task, mirroring `server.py`'s `_idle_watchdog`/
    `_flush_loop` shape — a future `server.py` wiring can
    `asyncio.create_task(reap_loop(state.event_bus, ...))` the same way it
    already does for those two loops. Not started by this module itself —
    no event-loop side effects at import time.
    """
    while True:
        await asyncio.sleep(interval_s)
        bus.reap_dead(idle_timeout_s=idle_timeout_s)
