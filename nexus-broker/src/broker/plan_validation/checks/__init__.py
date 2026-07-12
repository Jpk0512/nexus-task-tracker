"""Deterministic, single-purpose plan-gate checks (R3-T05, node N11).

Each module here is ONE focused check, disjoint from `plan_validation/score.py`
(N08 core) and `plan_validation/probes/**` (N09 model-judged probes — never
imported from this package). `score.py` wires each check's verdict into the
default (non-opt-in) gate output.
"""
from __future__ import annotations

from broker.plan_validation.checks.invocability import check_invocability

__all__ = ["check_invocability"]
