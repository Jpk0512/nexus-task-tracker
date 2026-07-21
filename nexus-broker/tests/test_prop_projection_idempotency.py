"""F3-05 property suite 4/5 — projection IDEMPOTENCY INVARIANT.

Builds on the F3-02 event store. The INVARIANT (model `replay.idempotency`):

    replaying an already-applied event stream is a NO-OP.

Concretely: after appending a stream, re-delivering the IDENTICAL stream adds no
rows (each event's `event_id` is UNIQUE — a duplicate returns the already-stored
event, never a second log row) and leaves every projection hash unchanged. An
at-least-once producer (F3-03 dual-write) can therefore re-deliver safely.

Real data layer (tdd-core: no mocking the DB): each example drives a REAL
`EventStore` on a real DuckDB file in a per-example temp dir — the dedupe path
under test is the store's actual `event_id`-UNIQUE insert, not a stubbed one.

Regression corpus: explicit `@example([])` (empty stream — re-delivering nothing
is trivially a no-op, the shrinker's fixed point) pinned inline.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from _prop_event_strategies import event_streams
from hypothesis import HealthCheck, example, given, settings

from broker.daemon.event_store import (
    EventStore,
    hash_projections,
    project,
    whole_store_hash,
)

pytestmark = pytest.mark.property


def _store_hash(store: EventStore) -> str:
    return whole_store_hash(hash_projections(project(store.read_events())))


@given(events=event_streams())
@example(events=[])
@settings(
    max_examples=60,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)
def test_redelivering_same_stream_is_a_noop(events: list[dict]) -> None:
    """INVARIANT: a second delivery of the same stream changes nothing."""
    with tempfile.TemporaryDirectory() as tmp:
        store = EventStore(Path(tmp) / ".memory" / "events.duckdb")
        try:
            for event in events:
                store.append(event)

            count_before = store.event_count()
            hash_before = _store_hash(store)

            # Re-deliver the identical stream — every event_id already exists.
            for event in events:
                store.append(event)

            assert store.event_count() == count_before, "dedupe added a log row"
            assert _store_hash(store) == hash_before, "projection changed on re-delivery"
        finally:
            store.close()
