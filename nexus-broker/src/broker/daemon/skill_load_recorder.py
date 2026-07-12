"""Event-sourced skill-load recorder — plans/08-daemon-capability-catalog.md
§3.6 (node N20, Phase B, post reversibility-gate).

Records Skill-tool-invocation events the daemon itself OBSERVES, as opposed
to a persona's self-reported `skills_loaded: [...]` claim in its return
envelope — DATA, unverifiable and gameable (spec §7: "The Skill tool
invocation is an event the harness observes; that observed event is the
only trustworthy signal."). This module is the daemon-side write path for
that observed event, targeting the R2-T15 `skill_load_events` table
(`.memory/schema.sql`) AS-IS — no schema change ships with this node.

It does not reimplement batching, flushing, or WAL hardening: it rides the
already-shipped 1.5 write-through path (`telemetry_store.TelemetryStore`),
which `test_telemetry_write_through_survives_kill_minus_9`
(`tests/test_daemon_pilot.py`) already proves durable under `kill -9` for
exactly this table. `project.db` stays authoritative; a daemon crash before
a flush cycle loses only the still-pending observed events, never a
previously flushed row and never the database itself.

No live producer wires into this module yet — the hook/session-event source
that calls `record_observed` on a real Skill-tool invocation is future,
cross-release scope (plans/08 §3.6 names the table as existing independent
of the daemon; the daemon-side observer plugging into a live event source is
not this node's charter). This module ships the recorder capability and is
proven against a fixture DB in the meantime.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from broker.daemon.telemetry_store import TelemetryStore

TABLE = "skill_load_events"


class SkillLoadRecorder:
    """Observed-event front door onto `TelemetryStore`'s skill_load_events lane.

    One instance wraps one daemon's `TelemetryStore` (the same instance
    `DaemonState.telemetry` already holds) — this class adds no state of its
    own beyond a served-count, and defers all buffering/flushing to the
    store it wraps.
    """

    def __init__(self, telemetry: TelemetryStore) -> None:
        self._telemetry = telemetry
        self.events_recorded = 0

    def record_observed(
        self,
        dispatch_id: str,
        skill_id: str,
        byte_len: int | None = None,
    ) -> dict[str, Any]:
        """Record one observed skill-load event onto the pending batch.

        Only WHAT was loaded is caller-supplied (`dispatch_id`, `skill_id`,
        optional `byte_len`) — WHEN is always this method's own wall-clock
        read at the moment of the call, never a caller-supplied timestamp.
        Accepting a caller-supplied `ts` would silently reopen the exact
        self-report gap this module exists to close: a caller could claim
        an event happened at a time it did not.

        Raises `ValueError` on a malformed identity (empty/non-string
        `dispatch_id` or `skill_id`) — an "observed" event with no identity
        is not a real observation, it is noise, and must never reach
        `project.db`.
        """
        if not isinstance(dispatch_id, str) or not dispatch_id.strip():
            raise ValueError("dispatch_id must be a non-empty string")
        if not isinstance(skill_id, str) or not skill_id.strip():
            raise ValueError("skill_id must be a non-empty string")

        row: dict[str, Any] = {
            "dispatch_id": dispatch_id,
            "skill_id": skill_id,
            "ts": _observed_at(),
        }
        if byte_len is not None:
            row["byte_len"] = int(byte_len)

        self._telemetry.record(TABLE, row)
        self.events_recorded += 1
        return row


def _observed_at() -> str:
    """ISO-8601 UTC, second precision — matches `skill_load_events.ts`'s documented shape."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
