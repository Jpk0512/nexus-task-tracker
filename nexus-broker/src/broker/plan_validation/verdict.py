"""Shared `Verdict` result type for every plan-gate check.

Extracted so `plan_validation/checks/*.py` (e.g. R3-T05/N11's invocability check) and
`plan_validation/score.py` (N08) share ONE nominal type — a dict literal combining
verdicts from both modules must type-check as `dict[str, Verdict]`, not collapse to
`dict[str, object]`. This module has no dependency on either, so both may import it
with no cycle.
"""
from __future__ import annotations

from typing import Any


class Verdict:
    """One pass/fail check result with the offending node ids on fail."""

    __slots__ = ("passed", "offending_node_ids", "details")

    def __init__(self, passed: bool, offending_node_ids: list[str], details: list[str]) -> None:
        self.passed = passed
        self.offending_node_ids = offending_node_ids
        self.details = details

    def to_dict(self) -> dict[str, Any]:
        return {
            "pass": self.passed,
            "offending_node_ids": self.offending_node_ids,
            "details": self.details,
        }
