"""broker.observability.report — composes the plan-gate/cost panels
(`metrics.py`, `cost.py`) with a live-wiring structural probe of the three
RE-STAGE daemon capabilities (`live_feed.py`) into one JSON-serializable
obs report (N58's acceptance criterion #1).

`.memory/health.py::check_observability_report` shells out to this
module's CLI (`uv run python -m broker.observability.report --project-path
P`) rather than importing it directly — health.py runs under the ambient
interpreter (possibly <3.11), not this package's own >=3.12 uv venv; the
same subprocess-not-import convention `check_broker_mcp_boots` already
uses for exactly this reason.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any

from broker.observability import cost, live_feed, metrics

SELFCHECK_DISPATCH_ID = "obs-report-selfcheck-dispatch"
SELFCHECK_SKILL_ID = "agent-protocol"


def build_report(project_path: str | Path) -> dict[str, Any]:
    """The full obs report for one project tree. Never raises on missing/
    partial data — every panel degrades to `{"available": False, ...}`
    (see `metrics.py`/`cost.py`/`live_feed.py`'s own graceful-degrade
    contracts); only a genuinely broken import/exec surfaces as an
    exception, which the CLI wrapper below turns into a non-zero exit
    instead of a stack trace on stdout.
    """
    root = Path(project_path)
    db_path = root / ".memory" / "project.db"
    router_decisions_path = root / ".memory" / "files" / "router_decisions.jsonl"

    report: dict[str, Any] = {"schema": "obs-report/1", "project_path": str(root)}

    if not db_path.is_file():
        report["plan_gate"] = {"available": False, "reason": "project.db not found"}
        report["cost"] = {"available": False, "reason": "project.db not found"}
    else:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        try:
            report["plan_gate"] = metrics.plan_gate_report(conn)
            report["cost"] = cost.cost_panel(conn, router_decisions_path)
        finally:
            conn.close()

    report["skills_actually_loaded"] = live_feed.skills_panel(db_path)
    report["live_feed"] = _live_feed_selfcheck()
    return report


def _live_feed_selfcheck() -> dict[str, Any]:
    """Structural liveness probe: a fresh `LiveFeed` genuinely publishes/
    journals/records through the real bus/tracing/skill_load_recorder
    classes (never a hand-written fixture dict) inside THIS process, and
    reports what came back — proves the wiring is live, independent of
    whatever historical rows (if any) `project.db` happens to hold.
    """
    feed = live_feed.LiveFeed(subscriber_id="obs-report-selfcheck")
    trace_id = feed.record_dispatch(
        dispatch_id=SELFCHECK_DISPATCH_ID,
        persona="pipeline-data",
        skills=(SELFCHECK_SKILL_ID,),
    )
    return {
        "wired": True,
        "probe_trace_id": trace_id,
        "bus": feed.bus_panel(),
        "tracing": feed.tracing_panel(),
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="R5-T06 observability report (N58)")
    parser.add_argument("--project-path", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    report = build_report(args.project_path)
    print(json.dumps(report, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
