"""
Tests for model_tier.py — R3-T13 (node N16): reviewer != worker model-tier map.

Acceptance criteria pinned here (GWT format), from nexus-redesign/plans/09-r3-plan-dag.md N16:
  AC-1: reviewer_model(worker, tier) != worker for EVERY (worker, tier) pair (exhaustive).
  AC-2: T0 maps to no reviewer (None); T1 maps to a light/deterministic-only tier.
  AC-3: Opus appears ONLY in the T2 row(s) — never in T0 or T1.
  AC-4: The map is DATA (a table: REVIEWER_TABLE), not scattered conditionals.
  AC-5: An unrecognized (worker, tier) pair raises KeyError, never a silent guess.

Run with:
  python3 -m pytest .claude/hooks/tests/test_model_tier_rederivation.py -v
"""

from __future__ import annotations

import importlib.util
import itertools
from pathlib import Path

import pytest

HOOKS_DIR: Path = Path(__file__).parent.parent
MODEL_TIER_MODULE: Path = HOOKS_DIR / "model_tier.py"


def _load_model_tier() -> object:
    """Dynamically load model_tier from its file path (mirrors test_router_persona_enum.py)."""
    spec = importlib.util.spec_from_file_location("model_tier", MODEL_TIER_MODULE)
    assert spec is not None, f"Could not create spec for {MODEL_TIER_MODULE}"
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


@pytest.fixture(scope="module")
def mt():
    return _load_model_tier()


# ---------------------------------------------------------------------------
# AC-1: reviewer != worker, exhaustively over every known (worker, tier) pair.
# ---------------------------------------------------------------------------
# Given: every (worker_model, risk_tier) combination in the module's own
#        WORKER_MODELS x RISK_TIERS product
# When:  reviewer_model() is called for each pair
# Then:  the returned reviewer (when not None) never equals the worker, and
#        never shares the worker's model-family identity


def test_reviewer_never_equals_worker_exhaustive(mt) -> None:
    for worker, tier in itertools.product(mt.WORKER_MODELS, mt.RISK_TIERS):
        reviewer = mt.reviewer_model(worker, tier)
        if reviewer is None:
            continue
        assert reviewer != worker, (
            f"reviewer_model({worker!r}, {tier!r}) returned the worker's own name: {reviewer!r}"
        )
        assert not mt.reviewer_is_same_identity(worker, reviewer), (
            f"reviewer_model({worker!r}, {tier!r}) == {reviewer!r} shares worker's "
            f"model-family identity — self-judge violation"
        )


# ---------------------------------------------------------------------------
# AC-2: T0 -> no reviewer; T1 -> a light/deterministic-only reviewer.
# ---------------------------------------------------------------------------
# Given: every worker model
# When:  reviewer_model(worker, "T0") / reviewer_model(worker, "T1") is called
# Then:  T0 returns None; T1 returns the lens-fast (light) reviewer


@pytest.mark.parametrize("worker", ["opus", "sonnet", "haiku"])
def test_t0_has_no_reviewer(mt, worker: str) -> None:
    assert mt.reviewer_model(worker, "T0") is None


@pytest.mark.parametrize("worker", ["opus", "sonnet", "haiku"])
def test_t1_maps_to_light_deterministic_reviewer(mt, worker: str) -> None:
    reviewer = mt.reviewer_model(worker, "T1")
    assert reviewer == mt.LENS_FAST
    assert "opus" not in reviewer, "T1 must never carry a full/opus-tier reviewer"


# ---------------------------------------------------------------------------
# AC-3: Opus appears ONLY in T2 rows — never T0/T1.
# ---------------------------------------------------------------------------
# Given: the full REVIEWER_TABLE
# When:  scanning every row
# Then:  any row whose reviewer mentions "opus" has risk_tier == "T2"


def test_opus_reviewer_only_in_t2_rows(mt) -> None:
    for (_worker, tier), reviewer in mt.REVIEWER_TABLE.items():
        if reviewer is not None and "opus" in reviewer:
            assert tier == "T2", (
                f"Opus reviewer {reviewer!r} found outside T2 (tier={tier!r}) — "
                f"violates 'Opus ONLY in the T2 row'"
            )


def test_t2_reviewer_is_full_rederivation_and_present(mt) -> None:
    for worker in mt.WORKER_MODELS:
        reviewer = mt.reviewer_model(worker, "T2")
        assert reviewer is not None, f"T2 must have a full re-derivation reviewer for {worker!r}"
        assert reviewer in (mt.LENS_FULL_OPUS, mt.LENS_FULL_SONNET)


def test_at_least_one_t2_row_uses_opus(mt) -> None:
    """Opus must appear SOMEWHERE in the T2 row set (not vacuously absent)."""
    t2_reviewers = [
        reviewer
        for (_worker, tier), reviewer in mt.REVIEWER_TABLE.items()
        if tier == "T2"
    ]
    assert any(r is not None and "opus" in r for r in t2_reviewers)


# ---------------------------------------------------------------------------
# AC-4: the map is DATA — REVIEWER_TABLE is a plain dict, not derived from
# runtime branching. This is a structural/regression guard against a future
# edit reintroducing scattered if/elif logic instead of extending the table.
# ---------------------------------------------------------------------------
# Given: the model_tier module
# When:  inspecting REVIEWER_TABLE
# Then:  it is a dict keyed by (worker_model, risk_tier) 2-tuples, with an entry
#        for every WORKER_MODELS x RISK_TIERS pair (fully populated, no gaps)


def test_reviewer_table_is_a_plain_dict(mt) -> None:
    assert isinstance(mt.REVIEWER_TABLE, dict)
    for key in mt.REVIEWER_TABLE:
        assert isinstance(key, tuple) and len(key) == 2


def test_reviewer_table_fully_populated_no_gaps(mt) -> None:
    expected_keys = set(itertools.product(mt.WORKER_MODELS, mt.RISK_TIERS))
    assert set(mt.REVIEWER_TABLE.keys()) == expected_keys


# ---------------------------------------------------------------------------
# AC-5: unrecognized pairs raise KeyError, never a silent guessed reviewer.
# ---------------------------------------------------------------------------
# Given: a worker_model or risk_tier not present in the table
# When:  reviewer_model() is called
# Then:  KeyError is raised (not a default/None-return swallow)


def test_unknown_worker_model_raises_keyerror(mt) -> None:
    with pytest.raises(KeyError):
        mt.reviewer_model("gpt-4", "T1")


def test_unknown_risk_tier_raises_keyerror(mt) -> None:
    with pytest.raises(KeyError):
        mt.reviewer_model("opus", "T3")


# ---------------------------------------------------------------------------
# Structural guard: a corrupted table row (reviewer aliased to worker's own
# family) must fail loudly via ValueError, proving the self-judge check is wired
# into reviewer_model(), not just available as a helper.
# ---------------------------------------------------------------------------


def test_self_judge_guard_fires_on_corrupted_table(mt, monkeypatch) -> None:
    monkeypatch.setitem(mt.REVIEWER_TABLE, ("opus", "T1"), "lens-opus")
    with pytest.raises(ValueError):
        mt.reviewer_model("opus", "T1")
