"""K=2 diversity probe (R3-T04, node N09).

Structural divergence between two independently-sampled plans for the SAME
task. This module never generates the second sample itself — no model call,
no network anywhere here. The caller supplies `second_doc`, produced by a
model call made elsewhere. The N01 rule (the model producing the second
sample must not be the plan's own planner) is enforced as a hard deterministic
check: passing the same model id as both `planner_model` and `sampler_model`
fails closed, before any divergence math runs.

Semantics (documented design choice, not a hard science): near-zero
divergence between two supposedly-independent samples is itself the failure
signal this probe exists to catch. Genuine independent samples of a
nontrivial planning task essentially never come back structurally identical
— a divergence score under `min_divergence` indicates the "second sample"
was not actually independently generated (a cached echo, a copy-paste, a
sampling harness that isn't re-invoking the model). A HIGH divergence score
is reported but is not itself a failure — that's a human/reviewer signal,
not a deterministic gate.
"""
from __future__ import annotations

from collections import Counter
from typing import Any

from broker.plan_validation.score import Verdict

DEFAULT_MIN_DIVERGENCE = 0.05


def _node_personas(doc: dict[str, Any]) -> Counter:
    return Counter(
        n.get("agent_persona") for n in (doc.get("nodes") or []) if isinstance(n, dict) and n.get("agent_persona")
    )


def _edge_count(doc: dict[str, Any]) -> int:
    return sum(len(n.get("depends_on") or []) for n in (doc.get("nodes") or []) if isinstance(n, dict))


def _multiset_jaccard_distance(a: Counter, b: Counter) -> float:
    if not a and not b:
        return 0.0
    union = sum((a | b).values())
    if not union:
        return 0.0
    intersection = sum((a & b).values())
    return 1.0 - (intersection / union)


def structural_divergence(doc_a: dict[str, Any], doc_b: dict[str, Any]) -> float:
    """Pure, deterministic [0.0, 1.0] structural-divergence score between two
    plan DAG documents. 0.0 == structurally identical on every component
    measured; 1.0 == maximally divergent on all of them."""
    nodes_a = doc_a.get("nodes") or []
    nodes_b = doc_b.get("nodes") or []
    count_a, count_b = len(nodes_a), len(nodes_b)
    node_count_distance = abs(count_a - count_b) / max(count_a, count_b, 1)

    persona_distance = _multiset_jaccard_distance(_node_personas(doc_a), _node_personas(doc_b))

    edges_a, edges_b = _edge_count(doc_a), _edge_count(doc_b)
    edge_count_distance = abs(edges_a - edges_b) / max(edges_a, edges_b, 1)

    return (node_count_distance + persona_distance + edge_count_distance) / 3.0


def check_k2_diversity(
    doc: dict[str, Any],
    second_doc: dict[str, Any],
    *,
    planner_model: str | None = None,
    sampler_model: str | None = None,
    min_divergence: float = DEFAULT_MIN_DIVERGENCE,
) -> Verdict:
    """Verdict.passed is False when the N01 rule is violated (the second
    sample came from the same model id as the planner) OR the computed
    divergence is suspiciously low (< min_divergence) — see module docstring."""
    if planner_model is not None and sampler_model is not None and planner_model == sampler_model:
        return Verdict(
            passed=False,
            offending_node_ids=[],
            details=[
                f"N01 violation: sampler_model == planner_model ({planner_model!r}) — the "
                "diversity sample must come from a model other than the plan's own planner"
            ],
        )

    score = structural_divergence(doc, second_doc)
    if score < min_divergence:
        return Verdict(
            passed=False,
            offending_node_ids=[],
            details=[
                f"structural divergence {score:.4f} is below the minimum {min_divergence} — "
                "the second sample looks non-independent (near-identical to the first)"
            ],
        )
    return Verdict(passed=True, offending_node_ids=[], details=[f"structural divergence: {score:.4f}"])
