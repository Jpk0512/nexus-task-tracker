"""broker.observability — R5-T06 observability graduation (N58, plans/15).

Graduates the R1-T07 metric definitions (`nexus-redesign/design/
W0-observability-metrics.md`) from "named, not yet emitting" to real panels
rendered off `project.db` rows: plan-gate accuracy, Lens-FAIL rate,
REVISE-loop count, dispatch latency (`metrics.py`), and cost
(`cost.py`, `dispatch_telemetry` + router-decision token capture).

Also wires the three R4-T09 RE-STAGE daemon capabilities — `bus`,
`skill_load_recorder`, `tracing` — as REAL inputs to this surface
(`live_feed.py`): bus events feed the obs report, `skill_load_recorder`
feeds a skills-actually-loaded panel, tracing feeds per-dispatch span
summaries. None of `broker.daemon.{bus,skill_load_recorder,tracing}` is
edited by this node (outside its write_scope) — the wiring lives entirely
in this new package, calling those modules' existing public APIs.

`eval_job.py` graduates the R1-T06 B4 eval suite from a one-shot manual CLI
invocation to a repeatable job with recorded runs (its own run ledger, never
touching `research/_meta/eval-history.jsonl` — outside this node's
write_scope).

`report.py` composes all of the above into one JSON report and exposes a
CLI (`python -m broker.observability.report --project-path P`) so
`.memory/health.py` can shell out to it the same subprocess-not-import way
`check_broker_mcp_boots` already does.

Deliberately no eager `from broker.observability.report import build_report`
here: `report.py` is itself the `-m`-runnable CLI entry point, and eagerly
importing it at package-init time makes `python -m broker.observability.report`
re-execute it under a second module identity (`runpy`'s documented
"found in sys.modules ... prior to execution" `RuntimeWarning`). Callers
import `broker.observability.report.build_report` directly.
"""
from __future__ import annotations
