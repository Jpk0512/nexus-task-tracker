"""broker.observability.metrics — plan-gate accuracy/quality panel
definitions (`nexus-redesign/design/W0-observability-metrics.md`, R1-T07),
graduated from "defined" to "emitting real numbers" over `validation_log`
(R5-T06 / N58, N58's acceptance criterion #1: "obs report renders plan-gate
accuracy ... panels from real DB rows").

Every function here is read-only over an already-open `sqlite3.Connection`
(row_factory left to the caller — `report.py` sets `sqlite3.Row`), tolerant
of a missing table or an empty window, and MUST NEVER raise on bad/missing
data — the same "INFO only, never FAIL" posture
`.memory/health.py::check_dispatch_telemetry_kpi` established for this
exact table family. A malformed row degrades that one row out of the
aggregate; it never aborts the whole report.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from broker.observability._util import parse_ts, table_exists

DEFAULT_WINDOW = 200


def plan_gate_accuracy(conn: sqlite3.Connection, window: int = DEFAULT_WINDOW) -> dict[str, Any]:
    """Metric #3: accuracy = 1 - reject_rate, reject_rate = revise_count /
    total_gated_plans, over the most recent `window` `validation_log` rows.
    `revise_reason` non-null is the REVISE-occurred proxy (W0 doc's
    documented gap: this is a post-Lens implementer-REVISE proxy, not a
    pre-execution plan-validation-gate reject — the real gate is W2 scope).
    """
    if not table_exists(conn, "validation_log"):
        return {"available": False, "reason": "validation_log table not present"}
    rows = conn.execute(
        "SELECT revise_reason FROM validation_log ORDER BY validated_at DESC LIMIT ?",
        (window,),
    ).fetchall()
    total = len(rows)
    if total == 0:
        return {"available": False, "reason": "validation_log is empty"}
    revise_count = sum(1 for r in rows if r["revise_reason"])
    reject_rate = revise_count / total
    return {
        "available": True,
        "window": total,
        "revise_count": revise_count,
        "reject_rate": round(reject_rate, 4),
        "accuracy": round(1 - reject_rate, 4),
    }


def lens_fail_rate(conn: sqlite3.Connection, window: int = DEFAULT_WINDOW) -> dict[str, Any]:
    """Metric #4: fraction of Lens-validated dispatches with verdict=FAIL,
    over the most recent `window` rows."""
    if not table_exists(conn, "validation_log"):
        return {"available": False, "reason": "validation_log table not present"}
    rows = conn.execute(
        "SELECT verdict FROM validation_log ORDER BY validated_at DESC LIMIT ?",
        (window,),
    ).fetchall()
    total = len(rows)
    if total == 0:
        return {"available": False, "reason": "validation_log is empty"}
    fail_count = sum(1 for r in rows if r["verdict"] == "FAIL")
    return {
        "available": True,
        "window": total,
        "fail_count": fail_count,
        "fail_rate": round(fail_count / total, 4),
    }


def revise_loop_summary(conn: sqlite3.Connection, window: int = DEFAULT_WINDOW) -> dict[str, Any]:
    """Metric #5: REVISE-loop count per (target_agent, task_or_brief_hash)
    group, minus 1 for the terminal PASS row itself (a single-row group is
    zero loops, never negative) — aggregated mean/max across the most
    recent `window` rows.
    """
    if not table_exists(conn, "validation_log"):
        return {"available": False, "reason": "validation_log table not present"}
    rows = conn.execute(
        """SELECT target_agent, task_or_brief_hash, COUNT(*) AS n
           FROM (SELECT * FROM validation_log ORDER BY validated_at DESC LIMIT ?)
           GROUP BY target_agent, task_or_brief_hash""",
        (window,),
    ).fetchall()
    if not rows:
        return {"available": False, "reason": "validation_log is empty"}
    loops = [max(row["n"] - 1, 0) for row in rows]
    return {
        "available": True,
        "groups": len(loops),
        "revise_loop_mean": round(sum(loops) / len(loops), 4),
        "revise_loop_max": max(loops),
    }


def dispatch_latency(conn: sqlite3.Connection, window: int = DEFAULT_WINDOW) -> dict[str, Any]:
    """Metric #1: dispatch_started_at -> validated_at wall-clock latency,
    p50/p90 in seconds. Rows missing either timestamp are excluded — the
    documented gap for non-Lens-gated T0/T1 dispatches (NATIVE-42
    territory, not this doc's job to backfill).
    """
    if not table_exists(conn, "validation_log"):
        return {"available": False, "reason": "validation_log table not present"}
    cols = {row[1] for row in conn.execute("PRAGMA table_info(validation_log)")}
    if "dispatch_started_at" not in cols:
        return {"available": False, "reason": "dispatch_started_at column not present"}
    rows = conn.execute(
        """SELECT dispatch_started_at, validated_at FROM validation_log
           WHERE dispatch_started_at IS NOT NULL AND validated_at IS NOT NULL
           ORDER BY validated_at DESC LIMIT ?""",
        (window,),
    ).fetchall()
    latencies: list[float] = []
    for row in rows:
        start = parse_ts(row["dispatch_started_at"])
        end = parse_ts(row["validated_at"])
        if start is not None and end is not None and end >= start:
            latencies.append((end - start).total_seconds())
    if not latencies:
        return {"available": False, "reason": "no rows carry both timestamps"}
    latencies.sort()
    return {
        "available": True,
        "n": len(latencies),
        "p50_s": round(_percentile(latencies, 0.50), 3),
        "p90_s": round(_percentile(latencies, 0.90), 3),
    }


def _percentile(sorted_values: list[float], pct: float) -> float:
    if len(sorted_values) == 1:
        return sorted_values[0]
    idx = pct * (len(sorted_values) - 1)
    lo, hi = int(idx), min(int(idx) + 1, len(sorted_values) - 1)
    frac = idx - lo
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * frac


def plan_gate_report(conn: sqlite3.Connection, window: int = DEFAULT_WINDOW) -> dict[str, Any]:
    """The composed plan-gate accuracy panel — N58's acceptance criterion
    #1 ('obs report renders plan-gate accuracy ... panels from real DB
    rows'). All four W0-doc plan-gate-family metrics (#1, #3, #4, #5); cost
    (#2) lives in `cost.py`, sourced differently per the doc's own split.
    """
    return {
        "accuracy": plan_gate_accuracy(conn, window),
        "lens_fail_rate": lens_fail_rate(conn, window),
        "revise_loops": revise_loop_summary(conn, window),
        "dispatch_latency": dispatch_latency(conn, window),
    }
