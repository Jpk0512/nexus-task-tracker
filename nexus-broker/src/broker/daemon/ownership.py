"""Live active-Workflow-to-touched-file OWNERSHIP TRACKING as daemon state —
plans/13 N21 (item 3.2, daemon half). Registered on dispatch, updated on
writes the telemetry path already observes, expired on Workflow completion —
so a gate check can ask "which Workflow touched this file" instead of
re-deriving attribution from a whole-repo git diff.

This is the STRUCTURAL fix for `project.db` NATIVE-14 ("lens-gate.sh
false-blocks DONE — attributes whole-repo dirty tree to the finishing
agent") and NATIVE-4-2 (its lean tactical twin) — not new scope (plan 13
§2.B). N22 (hermes, `.claude/hooks/lens-gate.sh`) is the consuming half;
this node builds only the daemon-side store.

Cache-only, same posture as `ready_set.py` (plan 13 §2.A row 3.4) and
`broker.daemon.client`/`fallback.py` (plans/07 §1 constraint 2): a daemon
restart loses all ownership warmth — `owners_of()` returns an empty
(unattributed) result for anything it does not currently hold, NEVER a
stale or wrong answer. `project.db`/git stay the actual source of truth;
this store is purely a faster, more-precise fast-path a gate MAY consult.

Kept as an INDEPENDENT method-dispatch surface, not folded into
`broker.daemon.server.handle_request` — this node's write scope excludes
server.py, matching N12's `ready_set.py` precedent exactly (a future node
may wire `handle_ownership_request` into the primary daemon's dispatch
table without changing its shape here).
"""
from __future__ import annotations

import threading
import time
from typing import Any


class UnknownWorkflow(KeyError):
    """Raised when a touch is recorded against a workflow_id that was never
    `register()`-ed (or was already `complete()`-ed) — a write attributed to
    a Workflow the daemon never saw dispatch is a caller bug, surfaced
    rather than silently dropped. `handle_ownership_request` turns this into
    a normal RPC error response, never a server crash — the same pattern
    `ready_set.UnknownRun` establishes for the sibling 3.4-thin store."""


class OwnershipRegistry:
    """One daemon-wide store: active Workflow -> the set of files it has
    touched, plus the reverse index that answers the gate-check query in
    O(1). Thread-safe — the same client-loop concurrency model every other
    daemon store in this package uses (`TelemetryStore`, `ReadySetRegistry`)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # workflow_id -> {"files": set[str], "registered_at": float}
        self._workflows: dict[str, dict[str, Any]] = {}
        # file_path -> set[workflow_id] currently claiming it — the O(1)
        # side of "which Workflow touched this file".
        self._file_index: dict[str, set[str]] = {}

    def register(self, workflow_id: str) -> None:
        """Called on dispatch. Idempotent — re-registering an already-active
        workflow_id is a no-op against its existing touched-file set (a
        retry or redelivery of the dispatch event must never wipe already-
        recorded touches; dispatch redelivery is the normal case, not an
        edge case, per every other daemon actor in this package)."""
        with self._lock:
            if workflow_id not in self._workflows:
                self._workflows[workflow_id] = {"files": set(), "registered_at": time.monotonic()}

    def record_touch(self, workflow_id: str, file_path: str) -> None:
        """Called for each write the telemetry path already observes — this
        call sits ALONGSIDE `TelemetryStore.record`'s `agent_activity`/
        `dispatch_telemetry` batching, not instead of it; ownership is a
        separate cache with a separate lifecycle. Raises `UnknownWorkflow`
        if `workflow_id` was never registered."""
        with self._lock:
            entry = self._workflows.get(workflow_id)
            if entry is None:
                raise UnknownWorkflow(workflow_id)
            entry["files"].add(file_path)
            self._file_index.setdefault(file_path, set()).add(workflow_id)

    def complete(self, workflow_id: str) -> int:
        """Called on Workflow completion — expires the workflow's ownership
        state entirely (registration + every touched-file claim). Returns
        the count of files released. Idempotent: completing an unknown or
        already-expired workflow_id is a no-op returning 0 — a redelivered
        completion event must never raise."""
        with self._lock:
            entry = self._workflows.pop(workflow_id, None)
            if entry is None:
                return 0
            for file_path in entry["files"]:
                owners = self._file_index.get(file_path)
                if owners is not None:
                    owners.discard(workflow_id)
                    if not owners:
                        del self._file_index[file_path]
            return len(entry["files"])

    def owners_of(self, file_path: str) -> list[str]:
        """The core gate-check query: which active Workflow(s) touched this
        file? Returns an empty list — NEVER raises — for a file the daemon
        holds no record of. That empty-list-not-error behavior IS the
        "cache-only, restart loses warmth never correctness" contract: a
        cold/fresh registry (post-restart, or simply never told about a
        file) answers every query as unattributed rather than fabricating
        or guessing an owner."""
        with self._lock:
            return sorted(self._file_index.get(file_path, ()))

    def is_active(self, workflow_id: str) -> bool:
        with self._lock:
            return workflow_id in self._workflows

    def snapshot(self) -> dict[str, Any]:
        """Diagnostic dump — active workflow count + per-workflow touched
        files, raw counts only, no invented estimates (the same discipline
        `DaemonState`'s 2.8 budget counters already use)."""
        with self._lock:
            return {
                "active_workflow_count": len(self._workflows),
                "workflows": {wid: sorted(entry["files"]) for wid, entry in self._workflows.items()},
                "tracked_file_count": len(self._file_index),
            }


_OWNERSHIP_METHODS = frozenset(
    {
        "ownership_register",
        "ownership_record_touch",
        "ownership_complete",
        "ownership_owners_of",
        "ownership_snapshot",
    }
)


def handle_ownership_request(registry: OwnershipRegistry, method: str, params: dict[str, Any]) -> Any:
    """Pure dispatch — same shape as `broker.daemon.server.handle_request`
    and `ready_set.handle_ready_set_request`. Exactly the 5 register/touch/
    complete/query/snapshot methods above; anything else is rejected — the
    acceptance boundary that no capability beyond ownership register/touch/
    complete/query is introduced by this node."""
    if method not in _OWNERSHIP_METHODS:
        raise ValueError(f"unknown ownership method: {method!r}")
    if method == "ownership_register":
        registry.register(params["workflow_id"])
        return {"registered": True}
    if method == "ownership_record_touch":
        registry.record_touch(params["workflow_id"], params["file_path"])
        return {"recorded": True}
    if method == "ownership_complete":
        released = registry.complete(params["workflow_id"])
        return {"released": released}
    if method == "ownership_owners_of":
        return {"owners": registry.owners_of(params["file_path"])}
    return registry.snapshot()
