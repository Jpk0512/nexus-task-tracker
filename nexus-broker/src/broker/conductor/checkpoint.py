"""broker.conductor.checkpoint — durable per-node checkpoint/resume for the
DAG conductor (crash-resilience fix; RCA `.memory/scout-reports/1783912955/
conductor-rca.md`, failed run `25182409948f4da1b473025fb8eb2f44`: a
verify-matrix run where 1 of 3 synthetic dispatch gates hit a transient
`claude`-binary rc!=0/timeout, and the conductor's binary all-or-nothing
status + NO per-node durability meant that one transient blip lost the
evidence for the whole run).

Modeled on the Workflow tool's own `journal.jsonl` + `resumeFromRunId`
semantics: EACH node's terminal result is journaled to disk the moment it
completes (append-only JSONL, one line per completion) — a resumed run
sharing the same `run_id` treats every node_id already present in the
journal as a same-run CACHE HIT (its cached result is reused, never
re-dispatched); only node_ids with no journal line for that `run_id` are
scheduled. A crash can therefore lose AT MOST the one node that was
mid-dispatch when the process died — every node that finished before the
crash is durable on disk and is never lost or re-run on resume.

This module is duck-typed against `dag.NodeResult` (never imports
`broker.conductor.dag`) so the import stays one-directional: `dag` imports
`checkpoint`, not the other way — no circular import.
"""
from __future__ import annotations

import json
import os
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_write_lock = threading.Lock()


def checkpoint_record(run_id: str, result: Any) -> dict[str, Any]:
    """Build the ONE JSONL line for a completed node: node id, pass/fail,
    output/error, and a completion timestamp — everything `load_checkpoint`
    needs to reconstruct a same-run cache hit without re-dispatching."""
    telemetry = getattr(result, "telemetry", None)
    return {
        "run_id": run_id,
        "node_id": result.node_id,
        "executor": result.executor,
        "ok": result.ok,
        "worker_id": result.worker_id,
        "payload": result.payload,
        "error": result.error,
        "duration_ms": telemetry.duration_ms if telemetry is not None else None,
        "total_cost_usd": telemetry.total_cost_usd if telemetry is not None else 0.0,
        "input_tokens": telemetry.input_tokens if telemetry is not None else None,
        "output_tokens": telemetry.output_tokens if telemetry is not None else None,
        "timestamp": datetime.now(UTC).isoformat(),
    }


def append_node_checkpoint(journal_path: str | Path, record: dict[str, Any]) -> None:
    """Append ONE JSONL line durably — flushed + fsynced before returning —
    the moment a node completes. A crash immediately after this call cannot
    lose the line; that is the entire durability guarantee this feature
    rests on. Thread-safe: `dag.run_dag`'s worker threads all call this
    concurrently off one shared ready-queue."""
    path = Path(journal_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record) + "\n"
    with _write_lock, path.open("a", encoding="utf-8") as fh:
        fh.write(line)
        fh.flush()
        os.fsync(fh.fileno())


def load_checkpoint(journal_path: str | Path, run_id: str) -> dict[str, dict[str, Any]]:
    """Read the journal and return `{node_id: last_record}` for every
    node_id with >=1 completed line under `run_id` — last-write-wins. A
    torn/partial last line (the process died mid-`write()`, vanishingly
    unlikely after the flush+fsync above but not impossible on some
    filesystems) is skipped, never fabricated into a fake result."""
    path = Path(journal_path)
    if not path.exists():
        return {}
    by_node: dict[str, dict[str, Any]] = {}
    with path.open("r", encoding="utf-8") as fh:
        for raw_line in fh:
            raw_line = raw_line.strip()
            if not raw_line:
                continue
            try:
                record = json.loads(raw_line)
            except json.JSONDecodeError:
                continue
            if record.get("run_id") != run_id:
                continue
            node_id = record.get("node_id")
            if node_id:
                by_node[node_id] = record
    return by_node
