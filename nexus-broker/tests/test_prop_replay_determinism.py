"""F3-05 property suite 3/5 — event-replay DETERMINISM INVARIANT.

Builds on the F3-02 event store. The INVARIANT (model
`replay.determinism_requirements`):

    ∀ generated event sequences appended to a REAL EventStore:
        replay(log) twice → byte-identical projection hashes.

A projection is a PURE reduce over the log ordered by `seq`; replaying the same
immutable log must yield the same rows and therefore the same
`whole_store_hash`. Any non-determinism (a stray now()/random/dict-order leak
into a fold or the canonical hash) would break this for SOME generated stream —
that is what the property hunts, not a single example.

Real data layer (tdd-core: no mocking the DB): each example drives a REAL
`EventStore` backed by a real DuckDB file in a per-example temp dir — never a
mocked connection returning canned rows. A fresh dir per example keeps examples
independent (a function-scoped `tmp_path` fixture would be SHARED across all
`@given` examples and accumulate state, so the temp dir is created inside the
test body instead).

Regression corpus: explicit `@example([])` (empty log) pinned inline — the
degenerate stream that a shrinker converges to and the one most likely to expose
an empty-projection hashing bug.
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
def test_replay_twice_yields_identical_projection_hashes(events: list[dict]) -> None:
    """INVARIANT: two replays of the same appended log hash identically."""
    with tempfile.TemporaryDirectory() as tmp:
        store = EventStore(Path(tmp) / ".memory" / "events.duckdb")
        try:
            for event in events:
                store.append(event)

            first = _store_hash(store)
            second = _store_hash(store)
            assert first == second

            # A full rebuild-from-log is likewise deterministic (projections are
            # disposable caches replayed identically each time).
            rebuild_a = hash_projections(store.rebuild_projections())
            rebuild_b = hash_projections(store.rebuild_projections())
            assert rebuild_a == rebuild_b
            assert whole_store_hash(rebuild_a) == first
        finally:
            store.close()
