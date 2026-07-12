"""broker.observability.live_feed — real (non-fixture) wiring of the three
R4-T09 RE-STAGE daemon capabilities into the observability graduation
surface (R5-T06 / N58's goal text: "Wire the three capabilities as real
inputs: bus events feed the obs report, skill_load_recorder feeds a
skills-actually-loaded panel ..., tracing feeds per-dispatch span
summaries. Prove liveness with real (non-fixture) traffic captured during
this node's own test runs.").

`broker.daemon.{bus,skill_load_recorder,tracing}` are used exactly as
shipped (no edit — `nexus-broker/src/broker/daemon/**` is outside this
node's write_scope): each of those modules' own docstring says "no live
producer wires into this module yet" — this module IS that producer, one
persona-boundary layer above, built entirely from their existing public
APIs (`EventBus.subscribe/publish`, `SkillLoadRecorder.record_observed`,
`tracing.bus_trace_recorder`/`TraceJournal.record`).

`EventBus.publish()` is fully synchronous (see its own docstring: "no
`await` anywhere in this method"), and `asyncio.Queue.put_nowait`/
`get_nowait` need no running event loop in 3.10+ — so this wiring is
plain synchronous glue, no `asyncio.run` required.
"""
from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from typing import Any

from broker.daemon import tracing
from broker.daemon.bus import (
    EVENT_KIND_DISPATCH_COMPLETED,
    EVENT_KIND_DISPATCH_STARTED,
    EVENT_KIND_SKILL_LOAD_OBSERVED,
    EventBus,
)
from broker.daemon.skill_load_recorder import SkillLoadRecorder
from broker.daemon.telemetry_store import TelemetryStore


class LiveFeed:
    """One EventBus + one SkillLoadRecorder (over its own TelemetryStore) +
    one TraceJournal, connected the way a future daemon subscriber-drain
    loop would connect them (`bus.py`'s own "left unconnected here on
    purpose" wiring point, and `tracing.bus_trace_recorder`'s matching
    "future daemon subscriber-drain loop plugs in" note) — without touching
    either module's source.
    """

    def __init__(self, subscriber_id: str = "obs-report") -> None:
        self.bus = EventBus()
        self.telemetry = TelemetryStore()
        self.skill_recorder = SkillLoadRecorder(self.telemetry)
        self.journal = tracing.TraceJournal()
        self._trace_sink = tracing.bus_trace_recorder(self.journal)
        self._subscription = self.bus.subscribe(subscriber_id=subscriber_id)

    def record_dispatch(
        self,
        *,
        dispatch_id: str,
        persona: str,
        trace_id: str | None = None,
        skills: tuple[str, ...] = (),
    ) -> str:
        """Drive ONE real dispatch's observable lifecycle across all three
        capabilities: dispatch_started -> N skill_load_observed ->
        dispatch_completed, published on the real bus, drained through the
        real subscription queue, and journaled via the real
        `bus_trace_recorder` sink — while independently recording the same
        skill loads through `SkillLoadRecorder`'s durable
        `skill_load_events` path (`flush_skill_events` below writes those
        to a real DB). Returns the resolved trace_id (minted if none was
        supplied), matching `tracing.ensure_trace_id`'s single-trace-id-
        per-chain contract.
        """
        trace_id = tracing.ensure_trace_id(trace_id)
        self._publish_and_journal(
            EVENT_KIND_DISPATCH_STARTED,
            {"dispatch_id": dispatch_id, "persona": persona, "trace_id": trace_id},
        )
        for skill_id in skills:
            self.skill_recorder.record_observed(dispatch_id, skill_id)
            self._publish_and_journal(
                EVENT_KIND_SKILL_LOAD_OBSERVED,
                {"dispatch_id": dispatch_id, "skill_id": skill_id, "trace_id": trace_id},
            )
        self._publish_and_journal(
            EVENT_KIND_DISPATCH_COMPLETED,
            {"dispatch_id": dispatch_id, "persona": persona, "trace_id": trace_id},
        )
        return trace_id

    def _publish_and_journal(self, kind: str, payload: dict[str, Any]) -> None:
        self.bus.publish(kind, payload)
        while True:
            try:
                event = self._subscription.queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            self._trace_sink(event)

    def flush_skill_events(self, db_path: Path) -> int:
        """Write every pending `record_observed` call through to a real
        `skill_load_events` table — the durable half of the
        skills-actually-loaded panel (`skills_panel` below reads it back).
        """
        return self.telemetry.flush(db_path)

    def bus_panel(self) -> dict[str, Any]:
        """Bus events feed the obs report (N58 goal text) — real
        `EventBus.stats()`, not a hand-written fixture dict."""
        return self.bus.stats()

    def tracing_panel(self) -> dict[str, Any]:
        """Tracing feeds per-dispatch span summaries (N58 goal text): one
        entry per trace_id journaled so far, each summarizing the real
        `TraceJournal.reconstruct(trace_id)` events.
        """
        summaries = []
        for trace_id in self.journal.trace_ids():
            events = self.journal.reconstruct(trace_id)
            summaries.append(
                {
                    "trace_id": trace_id,
                    "span_count": len(events),
                    "kinds": [event["kind"] for event in events],
                    "started_at": events[0]["ts"] if events else None,
                    "ended_at": events[-1]["ts"] if events else None,
                }
            )
        return {"traces": summaries, "journal_stats": self.journal.stats()}


def skills_panel(db_path: Path) -> dict[str, Any]:
    """Skills-actually-loaded panel (the R2-T15 `skill_load_events` table's
    consumer, per N58's goal text) — a real DB read, never a fixture list.
    Gracefully reports unavailable rather than raising when the table (or
    the DB itself) doesn't exist yet.
    """
    if not Path(db_path).is_file():
        return {"available": False, "reason": "db not found"}
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='skill_load_events'"
        ).fetchone()
        if not exists:
            return {"available": False, "reason": "skill_load_events table not present"}
        rows = conn.execute(
            "SELECT skill_id, COUNT(*) AS n FROM skill_load_events GROUP BY skill_id ORDER BY n DESC"
        ).fetchall()
        return {
            "available": True,
            "skills": [{"skill_id": row["skill_id"], "count": row["n"]} for row in rows],
        }
    finally:
        conn.close()
