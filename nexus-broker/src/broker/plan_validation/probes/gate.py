"""Opt-in gating for the three probe modules (R3-T04, node N09).

SPEED (C2, binding): probes run ONLY when the plan's max risk_tier is T2, or
any node carries irreversible:true, or the caller explicitly asks for them
(`force=True`). This module's own top-level imports are stdlib + typing only
— `run_probes` imports `citation` / `stub_mutation` / `diversity` LAZILY,
inside the branch that fires only when the opt-in condition holds. Calling
`run_probes` on a T0/T1, non-irreversible plan therefore touches zero probe
internals — the property the acceptance-criteria "opt-in flag test" checks.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

_PROBE_RISK_TIER = "T2"


def gate_requires_probes(doc: dict[str, Any]) -> bool:
    """True if any node's risk_tier == 'T2' or irreversible is True."""
    for node in doc.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        if node.get("risk_tier") == _PROBE_RISK_TIER:
            return True
        if node.get("irreversible") is True:
            return True
    return False


def run_probes(
    doc: dict[str, Any],
    *,
    force: bool = False,
    repo_root: str | Path | None = None,
    diversity_sample: dict[str, Any] | None = None,
    planner_model: str | None = None,
    sampler_model: str | None = None,
) -> dict[str, Any] | None:
    """Run the opt-in probes; return their combined verdict dict, or None if
    the opt-in gate doesn't fire and `force` wasn't passed — in that case NO
    probe submodule is imported (proves probes are opt-in, not on the default
    path).

    `citation_coverage` and `stub_mutation` are fully self-contained
    (deterministic, no injected data required) and always run once the gate
    fires. `k2_diversity` only runs when `diversity_sample` (the second plan,
    produced by a model other than the plan's own planner) is supplied — its
    absence is not itself a probe failure, just an omitted check.
    """
    if not force and not gate_requires_probes(doc):
        return None

    from broker.plan_validation.probes import citation, stub_mutation

    result: dict[str, Any] = {
        "citation_coverage": citation.check_citation_coverage(doc, repo_root).to_dict(),
        "stub_mutation": stub_mutation.check_stub_mutation(doc).to_dict(),
    }

    if diversity_sample is not None:
        from broker.plan_validation.probes import diversity

        result["k2_diversity"] = diversity.check_k2_diversity(
            doc,
            diversity_sample,
            planner_model=planner_model,
            sampler_model=sampler_model,
        ).to_dict()

    result["overall_pass"] = all(v["pass"] for v in result.values())
    return result
