"""Plan-validation gate — deterministic core (R3-T04, node N08).

Wraps `broker.node_contract` (N03) with plan-level checks that node_contract
does not itself make: skills_required rows matching docs/agents/SKILL_MAP.md
for (persona, work_type), and write_scope disjointness across nodes that are
NOT mutually ordered by depends_on (i.e. could run in the same wave).

DETERMINISTIC-ONLY: no model calls, no network I/O, at this node. Anything
model-judged (K=2 diversity, stub-mutation oracle, citation coverage) lives
in N09 (broker/plan_validation/probes/), a separate later leaf, and is
opt-in there — never imported by the default path this package exposes.
"""
from __future__ import annotations

from broker.plan_validation.score import score_file, score_plan

__all__ = ["score_file", "score_plan"]
