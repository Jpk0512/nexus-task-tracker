"""
model_tier.py — R3-T13 (node N16): reviewer != worker model-tier map.

Generalizes RDEC-012's reviewer != planner rule (nexus-redesign/DECISIONS.md) to
EVERY (worker_model, risk_tier) pair, per DEC-066 (Opus planner / Sonnet-5
orchestrator). The rule: whatever model did the work, the reviewer that checks it
is a DIFFERENT model — a same-model judge/worker pair inflates the verification
score (RDEC-004 SELF-JUDGE prohibition).

SPEED (C2): this map is where heavyweight-verification-by-default dies.
  T0 -> no reviewer (deterministic-only work; nothing to re-derive).
  T1 -> lens-fast / deterministic-only reviewer (light).
  T2 -> full re-derivation reviewer (heavy; Opus appears ONLY here).

The map is DATA (REVIEWER_TABLE below), not scattered if/elif conditionals — a
new worker model or tier is a new table row, never a new branch.

.claude/hooks/*.py execute under the SYSTEM python3 (3.9.6), NOT uv/3.12. Keep
this module 3.9-import-safe: no `datetime.UTC`, no def-time `X | None`, no
`match`/`case`.
"""

from __future__ import annotations

from typing import Optional

# Canonical risk tiers (node-contract schema v2, docs/agents/CONTRACT.md).
RISK_TIERS = ("T0", "T1", "T2")

# Canonical worker-model tiers this map knows how to review. Kept small and
# explicit — an unlisted worker model is a KeyError, not a silent default,
# because guessing a reviewer for an unknown worker is exactly the kind of
# scattered-conditional drift this map exists to prevent.
WORKER_MODELS = ("opus", "sonnet", "haiku")

# reviewer tier names, in ascending weight (None = no reviewer dispatched).
NO_REVIEWER: Optional[str] = None  # noqa: UP045 -- def-time `X | None` breaks py3.9 import
LENS_FAST = "lens-fast"  # deterministic-only / light reviewer (T1)
LENS_FULL_OPUS = "lens-opus"  # full re-derivation reviewer (T2) — Opus ONLY here
LENS_FULL_SONNET = "lens-sonnet"  # full re-derivation reviewer (T2), sonnet-tier worker case

# ---------------------------------------------------------------------------
# THE MAP — data, not conditionals.
#
# Keyed (worker_model, risk_tier) -> reviewer_model. Every row is picked so the
# reviewer is NEVER the same model as the worker (RDEC-012 generalized):
#   - T0: no reviewer at all (nothing to re-derive at this tier).
#   - T1: lens-fast (deterministic-only/light) — cheap, always a different
#         model identity than any worker tier.
#   - T2: full re-derivation. Opus workers get a Sonnet-tier full reviewer
#         (mirrors DEC-066: Opus planner / Sonnet-5 orchestrator-as-reviewer);
#         Sonnet/Haiku workers get the Opus full reviewer — Opus therefore
#         appears ONLY in T2 rows, never in T0/T1.
# ---------------------------------------------------------------------------
REVIEWER_TABLE = {
    ("opus", "T0"): NO_REVIEWER,
    ("opus", "T1"): LENS_FAST,
    ("opus", "T2"): LENS_FULL_SONNET,
    ("sonnet", "T0"): NO_REVIEWER,
    ("sonnet", "T1"): LENS_FAST,
    ("sonnet", "T2"): LENS_FULL_OPUS,
    ("haiku", "T0"): NO_REVIEWER,
    ("haiku", "T1"): LENS_FAST,
    ("haiku", "T2"): LENS_FULL_OPUS,
}


def reviewer_model(worker_model, risk_tier):
    # type: (str, str) -> Optional[str]
    """Return the reviewer model for a given (worker_model, risk_tier) pair.

    ALWAYS a different model identity than `worker_model` (generalizes RDEC-012's
    reviewer != planner rule to every tier). Raises KeyError on an unrecognized
    worker_model or risk_tier — an unknown pair must surface loudly, never fall
    back to a guessed reviewer.
    """
    key = (worker_model, risk_tier)
    if key not in REVIEWER_TABLE:
        raise KeyError(
            f"no reviewer-tier row for (worker_model={worker_model!r}, risk_tier={risk_tier!r}); "
            f"known worker_models={WORKER_MODELS!r}, known risk_tiers={RISK_TIERS!r}"
        )
    reviewer = REVIEWER_TABLE[key]
    if reviewer is not None and reviewer_is_same_identity(worker_model, reviewer):
        # Structural guard, not expected to fire — a bad table edit (reviewer
        # aliased back to the worker's own model identity) must fail loudly
        # rather than silently self-judge.
        raise ValueError(
            f"reviewer_model resolved to the same identity as worker_model={worker_model!r} "
            f"for risk_tier={risk_tier!r} — self-judge violation (RDEC-004)"
        )
    return reviewer


def reviewer_is_same_identity(worker_model, reviewer):
    # type: (str, str) -> bool
    """True if `reviewer` names the same underlying model identity as `worker_model`.

    Reviewer tokens carry a model-family substring (e.g. "lens-opus" contains
    "opus"); this catches a table row that accidentally maps a worker back onto
    a reviewer of its own family, even under a different label.
    """
    return worker_model in reviewer.split("-")
