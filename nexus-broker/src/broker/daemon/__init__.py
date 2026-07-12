"""R4-T06 daemon pilot (Option C) — Unix-domain-socket JSON-RPC, per-project.

Scope is exactly plans/13-r4-conductor-lane-plan.md §2 Phase A (node N11):
warm skills/agents registry cache, schema-snapshot cache, hook cold-start
avoidance, thin telemetry write-through, instant health check, spawn-on-
demand + idle-shutdown, stale-socket self-heal, the non-MCP half of the
registry query API, and budget summaries. NOT source of truth — each
project's own `.memory/project.db` stays authoritative; this package is a
write-through hot cache + event recorder layered on top, per the R4-T06
charter (plans/07-daemon-architecture-options.md §1).
"""
from __future__ import annotations
