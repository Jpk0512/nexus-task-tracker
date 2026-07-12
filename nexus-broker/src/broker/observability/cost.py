"""broker.observability.cost — cost panel (N58's acceptance criterion #1:
"...cost panels from real DB rows"; goal text: "cost metrics (per-dispatch
token/$ from dispatch_telemetry + router cost capture)").

Two REAL capture points, both already emitting today (no schema change
required — `.memory/schema.sql` is this node's do_not_touch):

  1. `dispatch_telemetry.tokens`/`model` (NATIVE-42/R1-T01) — per-dispatch
     token counts for the persona doing the actual work.
  2. `.memory/files/router_decisions.jsonl` `input_tokens`/`output_tokens`
     — the routing classifier's own (separate, much smaller) token spend.

`sessions.tokens_in`/`tokens_out` — the W0 doc's still-open Cluster-A half
of the "tokens in/out" metric — is deliberately NOT built here: that is a
`.memory/schema.sql` change, and schema design belongs to Atlas, never to
this persona's own initiative (this node's do_not_touch list names
`.memory/schema.sql` explicitly). Cost stays scoped to the two capture
points that already exist without a schema change.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from broker.observability._util import parse_ts, table_exists

DEFAULT_WINDOW = 20
ROUTER_WINDOW_HOURS = 24

# Blended $/1K-token rates — APPROXIMATE, for the derived-cost VIEW only
# (W0 doc: "Dollar-cost is a derived view (tokens x model rate), not a
# separate captured metric, to avoid two sources of truth for the same
# underlying number"). `dispatch_telemetry.tokens` is a single blended
# estimate (exact subagent_tokens, or char/4 approx) — not split
# input/output — so these are blended in/out-averaged rates, not a
# provider's one-directional list price.
MODEL_RATES_PER_1K: dict[str, float] = {
    "opus": 0.030,
    "sonnet": 0.006,
    "haiku": 0.0015,
}


def _rate_for_model(model: str | None) -> float | None:
    if not model:
        return None
    lowered = model.lower()
    for key, rate in MODEL_RATES_PER_1K.items():
        if key in lowered:
            return rate
    return None


def dispatch_cost_summary(conn: sqlite3.Connection, window: int = DEFAULT_WINDOW) -> dict[str, Any]:
    """Per-dispatch token/$ over the most recent `window` `dispatch_telemetry`
    rows. `estimated_cost_usd` only sums rows whose `model` resolves to a
    known rate — `priced_dispatches` reports how many of `window` that was,
    so an unpriced-model gap in the data is visible, not silently averaged
    away.
    """
    if not table_exists(conn, "dispatch_telemetry"):
        return {"available": False, "reason": "dispatch_telemetry table not present"}
    rows = conn.execute(
        "SELECT model, tokens FROM dispatch_telemetry ORDER BY recorded_at DESC LIMIT ?",
        (window,),
    ).fetchall()
    if not rows:
        return {"available": False, "reason": "dispatch_telemetry is empty"}
    total_tokens = 0
    total_cost = 0.0
    priced_n = 0
    for row in rows:
        tokens = row["tokens"] or 0
        total_tokens += tokens
        rate = _rate_for_model(row["model"])
        if rate is not None:
            total_cost += (tokens / 1000.0) * rate
            priced_n += 1
    return {
        "available": True,
        "window": len(rows),
        "total_tokens": total_tokens,
        "priced_dispatches": priced_n,
        "estimated_cost_usd": round(total_cost, 4),
    }


def router_cost_summary(
    router_decisions_path: Path, hours: int = ROUTER_WINDOW_HOURS
) -> dict[str, Any]:
    """Router cost capture: sums `input_tokens`/`output_tokens` from
    `router_decisions.jsonl` entries within the past `hours` — the
    routing classifier's own token spend, a separate (much smaller)
    cost stream from the persona dispatches `dispatch_cost_summary` covers.
    """
    if not router_decisions_path.is_file():
        return {"available": False, "reason": "router_decisions.jsonl not found"}
    cutoff = datetime.now(UTC) - timedelta(hours=hours)
    total_in = 0
    total_out = 0
    n = 0
    for line in router_decisions_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        ts = parse_ts(entry.get("timestamp") or entry.get("ts") or "")
        if ts is None or ts < cutoff:
            continue
        total_in += int(entry.get("input_tokens") or 0)
        total_out += int(entry.get("output_tokens") or 0)
        n += 1
    if n == 0:
        return {"available": False, "reason": f"no router decisions in past {hours}h"}
    return {
        "available": True,
        "window_hours": hours,
        "decisions": n,
        "input_tokens": total_in,
        "output_tokens": total_out,
    }


def cost_panel(conn: sqlite3.Connection, router_decisions_path: Path) -> dict[str, Any]:
    """The composed cost panel — N58's acceptance criterion #1."""
    return {
        "dispatch": dispatch_cost_summary(conn),
        "router": router_cost_summary(router_decisions_path),
    }
