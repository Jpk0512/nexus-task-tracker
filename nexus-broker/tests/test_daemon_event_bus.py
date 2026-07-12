"""Tests for the in-process pub-sub event bus — plans/08 §3.3 (node N23).

Covers exactly this node's acceptance criteria: a subscriber receives
pushed events for all five event kinds; unsubscribed/dead clients are
reaped without stalling publishers; bounded-queue overflow follows the
documented drop-oldest policy and increments a drop counter without ever
blocking the daemon's serving path; and the bus is push-only over cache
state (structurally incapable of touching `project.db`).

No live producer/transport wires into `bus.py` yet (see its module
docstring) — every test here drives the bus directly via `publish()`/
`subscribe()`, the same fixture-tested posture
`test_daemon_skill_load_recorder.py` uses for its own not-yet-wired
producer.
"""
from __future__ import annotations

import asyncio
import contextlib
import inspect
import time

import pytest

from broker.daemon import bus as bus_module
from broker.daemon.bus import (
    EVENT_KIND_DISPATCH_COMPLETED,
    EVENT_KIND_DISPATCH_STARTED,
    EVENT_KIND_GATE_DENIED,
    EVENT_KIND_LENS_VERDICT_RECORDED,
    EVENT_KIND_SKILL_LOAD_OBSERVED,
    EVENT_KINDS,
    EventBus,
    reap_loop,
)

# ── event-kind surface ──────────────────────────────────────────────────


def test_event_kinds_constant_has_exactly_five_named_kinds() -> None:
    assert EVENT_KINDS == {
        "dispatch_started",
        "dispatch_completed",
        "gate_denied",
        "lens_verdict_recorded",
        "skill_load_observed",
    }
    assert len(EVENT_KINDS) == 5


def test_publish_rejects_unknown_kind() -> None:
    bus = EventBus()
    with pytest.raises(ValueError, match="unknown event kind"):
        bus.publish("not_a_real_kind", {})


def test_subscribe_rejects_unknown_kind_filter() -> None:
    bus = EventBus()
    with pytest.raises(ValueError, match="unknown event kind"):
        bus.subscribe(kinds=["not_a_real_kind"])


# ── AC-1: subscriber receives pushed events for all five event kinds ──────


async def test_publish_delivers_to_subscriber_for_all_five_kinds() -> None:
    bus = EventBus()
    sub = bus.subscribe()  # kinds=None == every kind

    published = []
    for kind in sorted(EVENT_KINDS):
        published.append(bus.publish(kind, {"kind_was": kind}))

    received_kinds = set()
    for _ in range(5):
        event = await bus.receive(sub.id, timeout=1.0)
        assert event.payload == {"kind_was": event.kind}
        received_kinds.add(event.kind)

    assert received_kinds == EVENT_KINDS
    assert sub.delivered_count == 5


async def test_subscriber_kind_filter_narrows_delivery() -> None:
    bus = EventBus()
    sub = bus.subscribe(kinds=[EVENT_KIND_GATE_DENIED])

    for kind in sorted(EVENT_KINDS):
        bus.publish(kind, {})

    assert sub.queue.qsize() == 1
    event = await bus.receive(sub.id, timeout=1.0)
    assert event.kind == EVENT_KIND_GATE_DENIED


# ── subscriber lifecycle: subscribe / unsubscribe ──────────────────────────


def test_subscribe_duplicate_subscriber_id_raises() -> None:
    bus = EventBus()
    bus.subscribe(subscriber_id="cockpit-1")
    with pytest.raises(ValueError, match="already registered"):
        bus.subscribe(subscriber_id="cockpit-1")


def test_unsubscribe_stops_further_delivery_and_is_idempotent() -> None:
    bus = EventBus()
    sub = bus.subscribe()

    assert bus.unsubscribe(sub.id) is True
    assert bus.unsubscribe(sub.id) is False  # already gone — must not raise

    # Publishing after the only subscriber left must not raise either.
    bus.publish(EVENT_KIND_DISPATCH_STARTED, {"x": 1})
    assert bus.subscriber_count() == 0


# ── subscriber lifecycle: dead-subscriber detection + reaping ─────────────


def test_mark_dead_stops_delivery_immediately_without_reap() -> None:
    bus = EventBus()
    sub = bus.subscribe()

    assert bus.mark_dead(sub.id, reason="socket write failed") is True
    bus.publish(EVENT_KIND_GATE_DENIED, {"x": 1})

    # Excluded from fan-out the instant it's marked dead...
    assert sub.queue.qsize() == 0
    # ...but not removed from the roster until reap_dead() actually sweeps.
    assert bus.subscriber_count() == 1

    reaped = bus.reap_dead()
    assert reaped == [sub.id]
    assert bus.subscriber_count() == 0


def test_mark_dead_unknown_subscriber_returns_false() -> None:
    bus = EventBus()
    assert bus.mark_dead("nonexistent") is False


def test_reap_dead_removes_idle_subscribers_past_ttl() -> None:
    bus = EventBus()
    sub = bus.subscribe()
    sub.last_activity = time.monotonic() - 100.0  # simulate a vanished consumer

    reaped = bus.reap_dead(idle_timeout_s=10.0)

    assert reaped == [sub.id]
    assert bus.subscriber_count() == 0


async def test_reap_dead_does_not_remove_live_active_subscriber() -> None:
    bus = EventBus()
    sub = bus.subscribe()

    reaped = bus.reap_dead(idle_timeout_s=10.0)
    assert reaped == []
    assert bus.subscriber_count() == 1

    # Prove the subscription is still genuinely functional, not just present.
    bus.publish(EVENT_KIND_GATE_DENIED, {"ok": True})
    event = await bus.receive(sub.id, timeout=1.0)
    assert event.kind == EVENT_KIND_GATE_DENIED


def test_reap_dead_never_touches_a_live_subscribers_queue() -> None:
    bus = EventBus()
    live = bus.subscribe(queue_size=4)
    dead = bus.subscribe(queue_size=4)
    bus.mark_dead(dead.id)

    bus.publish(EVENT_KIND_DISPATCH_STARTED, {"i": 1})  # only `live` should receive it
    depth_before = live.queue.qsize()

    reaped = bus.reap_dead()

    assert reaped == [dead.id]
    assert live.queue.qsize() == depth_before  # untouched by the sweep
    assert bus.subscriber_count() == 1


async def test_reap_loop_background_task_sweeps_idle_subscriber_on_interval() -> None:
    bus = EventBus()
    sub = bus.subscribe()
    sub.last_activity = time.monotonic() - 100.0

    task = asyncio.create_task(reap_loop(bus, interval_s=0.02, idle_timeout_s=1.0))
    try:
        deadline = time.monotonic() + 2.0
        while bus.subscriber_count() > 0 and time.monotonic() < deadline:
            await asyncio.sleep(0.02)
        assert bus.subscriber_count() == 0
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


# ── AC-2: bounded-queue overflow follows the documented drop policy ───────


def test_overflow_drops_oldest_event_keeping_the_newest_n() -> None:
    bus = EventBus()
    sub = bus.subscribe(queue_size=3)

    for i in range(5):
        bus.publish(EVENT_KIND_DISPATCH_STARTED, {"i": i})

    assert sub.queue.qsize() == 3
    remaining = []
    while not sub.queue.empty():
        remaining.append(sub.queue.get_nowait().payload["i"])
    # Drop-oldest: the queue holds the 3 NEWEST events (2, 3, 4) — the two
    # oldest (0, 1) were evicted to make room, never the incoming (newest) one.
    assert remaining == [2, 3, 4]


def test_overflow_increments_per_subscriber_and_bus_wide_drop_counters() -> None:
    bus = EventBus()
    sub = bus.subscribe(queue_size=2)

    for i in range(5):
        bus.publish(EVENT_KIND_DISPATCH_COMPLETED, {"i": i})

    assert sub.dropped_count == 3
    assert bus.dropped_total == 3
    assert bus.published_total == 5


def test_overflow_never_raises_out_of_publish() -> None:
    bus = EventBus()
    bus.subscribe(queue_size=1)
    for i in range(50):
        event = bus.publish(EVENT_KIND_LENS_VERDICT_RECORDED, {"i": i})
        assert event.payload == {"i": i}  # publish() always returns the Event, never raises


def test_queue_size_must_be_bounded() -> None:
    bus = EventBus()
    with pytest.raises(ValueError, match="queue_size"):
        bus.subscribe(queue_size=0)
    with pytest.raises(ValueError, match="default_queue_size"):
        EventBus(default_queue_size=0)


# ── AC: reaping + overflow never stall the publisher, even under load ─────


def test_publish_is_a_plain_function_never_a_coroutine() -> None:
    """Structural proof `publish()` cannot suspend mid-call: it has no
    `await` points to yield the event loop on, regardless of how many
    subscribers exist or how full/dead any of them are.
    """
    assert not inspect.iscoroutinefunction(EventBus.publish)


def test_publish_does_not_stall_with_many_dead_and_full_subscribers() -> None:
    bus = EventBus()
    dead_subs = []
    for _ in range(25):
        s = bus.subscribe(queue_size=1)
        bus.mark_dead(s.id)  # simulates a transport-detected write failure
        dead_subs.append(s)
    live = bus.subscribe(queue_size=100)

    start = time.monotonic()
    for i in range(300):
        bus.publish(EVENT_KIND_DISPATCH_COMPLETED, {"i": i})
    elapsed = time.monotonic() - start

    assert elapsed < 2.0, f"publish() loop took {elapsed:.3f}s — looks blocked"
    # Dead subscribers are excluded from fan-out entirely — no work done, no drops counted against them.
    for s in dead_subs:
        assert s.queue.qsize() == 0
        assert s.dropped_count == 0
    # The live subscriber still got a full, bounded, freshest-N view: exactly
    # the last 100 published (200..299), oldest-still-present at the front.
    assert live.queue.qsize() == 100
    remaining = [live.queue.get_nowait().payload["i"] for _ in range(100)]
    assert remaining == list(range(200, 300))


def test_reap_and_publish_interleave_without_stalling_or_losing_live_subscribers() -> None:
    bus = EventBus()
    subs = [bus.subscribe(queue_size=2) for _ in range(10)]
    for s in subs[:5]:
        bus.mark_dead(s.id)

    start = time.monotonic()
    for i in range(50):
        bus.publish(EVENT_KIND_LENS_VERDICT_RECORDED, {"i": i})
        if i % 10 == 0:
            bus.reap_dead()
    bus.reap_dead()
    elapsed = time.monotonic() - start

    assert elapsed < 2.0
    assert bus.subscriber_count() == 5  # the 5 marked-dead subscribers were swept
    for s in subs[5:]:
        assert s.queue.qsize() == 2  # live ones kept receiving, bounded as designed


# ── AC-3: push-only over cache state — structurally cannot touch project.db ──


def test_event_bus_structurally_cannot_target_a_database() -> None:
    sig = inspect.signature(EventBus.__init__)
    assert "db_path" not in sig.parameters
    assert "project_path" not in sig.parameters

    source = inspect.getsource(bus_module)
    assert "import sqlite3" not in source
    assert "sqlite3." not in source


def test_publish_with_zero_subscribers_has_no_side_effects_and_returns_event() -> None:
    bus = EventBus()
    event = bus.publish(EVENT_KIND_SKILL_LOAD_OBSERVED, {"dispatch_id": "d1", "skill_id": "x"})

    assert event.kind == EVENT_KIND_SKILL_LOAD_OBSERVED
    assert bus.published_total == 1
    assert bus.dropped_total == 0
    assert bus.subscriber_count() == 0  # nothing to lose but the notification itself


# ── event identity: bus owns seq/ts, never caller-suppliable ───────────────


def test_publish_signature_accepts_only_kind_and_payload() -> None:
    """No `ts`/`seq` parameter exists at all — a caller cannot claim an event
    happened at a time, or in an order, it did not.
    """
    sig = inspect.signature(EventBus.publish)
    assert set(sig.parameters) == {"self", "kind", "payload"}


def test_published_events_get_monotonic_bus_owned_sequence_numbers() -> None:
    bus = EventBus()
    e1 = bus.publish(EVENT_KIND_DISPATCH_STARTED, {})
    e2 = bus.publish(EVENT_KIND_DISPATCH_STARTED, {})
    e3 = bus.publish(EVENT_KIND_DISPATCH_COMPLETED, {})

    assert (e1.seq, e2.seq, e3.seq) == (1, 2, 3)
    assert e1.ts and e2.ts and e3.ts


def test_event_to_dict_is_json_ready() -> None:
    bus = EventBus()
    event = bus.publish(EVENT_KIND_GATE_DENIED, {"gate": "plan-validation"})
    as_dict = event.to_dict()

    assert as_dict == {
        "seq": event.seq,
        "kind": EVENT_KIND_GATE_DENIED,
        "payload": {"gate": "plan-validation"},
        "ts": event.ts,
    }


# ── receive() convenience wrapper ───────────────────────────────────────────


async def test_receive_raises_timeout_when_queue_empty_then_delivers_once_published() -> None:
    bus = EventBus()
    sub = bus.subscribe()

    with pytest.raises(TimeoutError):
        await bus.receive(sub.id, timeout=0.05)

    bus.publish(EVENT_KIND_LENS_VERDICT_RECORDED, {"verdict": "PASS"})
    event = await bus.receive(sub.id, timeout=1.0)

    assert event.kind == EVENT_KIND_LENS_VERDICT_RECORDED
    assert sub.delivered_count == 1


async def test_receive_unknown_subscriber_raises_keyerror() -> None:
    bus = EventBus()
    with pytest.raises(KeyError):
        await bus.receive("nonexistent")


async def test_receive_touches_last_activity_preventing_idle_reap() -> None:
    bus = EventBus()
    sub = bus.subscribe()
    sub.last_activity = time.monotonic() - 100.0  # start "idle"

    bus.publish(EVENT_KIND_DISPATCH_COMPLETED, {})
    await bus.receive(sub.id, timeout=1.0)  # should touch() and refresh last_activity

    reaped = bus.reap_dead(idle_timeout_s=10.0)
    assert reaped == []
    assert bus.subscriber_count() == 1


# ── stats/introspection ─────────────────────────────────────────────────────


def test_stats_reports_published_dropped_and_subscriber_counts() -> None:
    bus = EventBus()
    sub = bus.subscribe(queue_size=1)
    bus.publish(EVENT_KIND_DISPATCH_STARTED, {"i": 1})
    bus.publish(EVENT_KIND_DISPATCH_STARTED, {"i": 2})  # overflow -> one drop

    stats = bus.stats()

    assert stats["published_total"] == 2
    assert stats["dropped_total"] == 1
    assert stats["subscriber_count"] == 1
    assert stats["subscribers"][sub.id]["dropped"] == 1
    assert stats["subscribers"][sub.id]["queue_depth"] == 1
    assert stats["subscribers"][sub.id]["alive"] is True
