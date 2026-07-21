"""Tests for broker.capability_token (F1-02, FDEC-4).

Covers: TASKS.md F1-02 acceptance — (1) this file green; (2) PASS mints exactly
one token per approved node, FAIL mints none, zero change to gate PASS/FAIL
semantics; (3) verify_token fails CLOSED on tamper/expiry/alg-downgrade/
unknown-kid/jti-denylist/unknown-schema_version, each tested individually.

Every test isolates KEY_PATH/DENYLIST_PATH into tmp_path via monkeypatch (the
`test_write_state_atomic.py` pattern) so no test ever touches the real repo's
`.memory/files/broker_token_key.json` or `token_denylist.jsonl`.
"""
from __future__ import annotations

import copy
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import broker.capability_token as token_mod
from broker.capability_token import (
    VerifyResult,
    canonical_claims,
    deny_jti,
    is_jti_denylisted,
    load_or_create_signing_key,
    mint_token,
    mint_tokens_for_plan,
    verify_token,
)
from broker.plan_validation.plan_doc import load_plan_as_dag_doc
from broker.plan_validation.score import score_file

FIXTURES = Path(__file__).parent / "fixtures" / "plan_validation"
FIXTURE_SKILL_MAP = FIXTURES / "skill_map_fixture.md"


@pytest.fixture(autouse=True)
def _isolated_token_paths(tmp_path: Path, monkeypatch) -> None:
    """Every test gets its own key file + deny-list — never the real repo's."""
    monkeypatch.setattr(token_mod, "KEY_PATH", tmp_path / "broker_token_key.json")
    monkeypatch.setattr(token_mod, "DENYLIST_PATH", tmp_path / "token_denylist.jsonl")


def _mint(**overrides) -> dict:
    kwargs = dict(
        plan_id="PLAN-1",
        task_id="A1",
        persona="hermes",
        write_scope=["docs/**"],
        tier="T1",
    )
    kwargs.update(overrides)
    return mint_token(**kwargs)


# ---------------------------------------------------------------------------
# mint_token — claim shape + signature
# ---------------------------------------------------------------------------


def test_mint_token_carries_every_required_claim() -> None:
    token = _mint()
    required = {
        "schema_version", "plan_id", "task_id", "persona", "write_scope",
        "tier", "issued_at", "expires_at", "jti", "kid", "alg", "sig",
    }
    assert required <= set(token)
    assert token["schema_version"] == 1
    assert token["alg"] == "HS256"


def test_mint_token_jti_is_unique_per_mint() -> None:
    t1 = _mint()
    t2 = _mint()
    assert t1["jti"] != t2["jti"]


def test_mint_token_default_ttl_is_four_hours() -> None:
    token = _mint()
    issued = datetime.fromisoformat(token["issued_at"])
    expires = datetime.fromisoformat(token["expires_at"])
    assert expires - issued == timedelta(hours=4)


def test_mint_token_ttl_env_override(monkeypatch) -> None:
    monkeypatch.setenv("NEXUS_TOKEN_TTL_SECONDS", "60")
    token = _mint()
    issued = datetime.fromisoformat(token["issued_at"])
    expires = datetime.fromisoformat(token["expires_at"])
    assert expires - issued == timedelta(seconds=60)


def test_mint_token_reuses_same_key_across_mints() -> None:
    """Second mint must not silently rotate — a fresh key per call would make
    every already-issued token independently unverifiable."""
    t1 = _mint()
    t2 = _mint()
    assert t1["kid"] == t2["kid"]


def test_load_or_create_signing_key_persists_0600(tmp_path: Path) -> None:
    key_path = tmp_path / "key.json"
    kid1, key1 = load_or_create_signing_key(key_path)
    kid2, key2 = load_or_create_signing_key(key_path)
    assert (kid1, key1) == (kid2, key2)
    assert key_path.exists()
    assert oct(key_path.stat().st_mode)[-3:] == "600"


def test_canonical_claims_excludes_sig() -> None:
    token = _mint()
    payload = canonical_claims(token)
    assert b'"sig"' not in payload


# ---------------------------------------------------------------------------
# DEC-096 — capability_token.allowed_personas (closed set)
# ---------------------------------------------------------------------------


def test_mint_omitted_roster_is_degenerate_single_element_set() -> None:
    # Single-persona dispatch: no allowed_personas arg → degenerate [persona].
    token = _mint(persona="hermes")
    assert token["allowed_personas"] == ["hermes"]


def test_mint_carries_explicit_closed_roster() -> None:
    token = _mint(
        persona="forge-wire",
        allowed_personas=["forge-wire", "pipeline-async", "quill-py"],
    )
    assert token["allowed_personas"] == ["forge-wire", "pipeline-async", "quill-py"]


def test_mint_allowed_personas_is_signed_and_verifies() -> None:
    token = _mint(allowed_personas=["hermes", "atlas"])
    # Tampering with the set breaks the signature (it is a signed claim).
    assert verify_token(token) == VerifyResult(True, "ok")
    tampered = copy.deepcopy(token)
    tampered["allowed_personas"] = ["hermes", "atlas", "forge-ui"]
    assert verify_token(tampered).ok is False


def test_mint_normalizes_and_dedupes_roster() -> None:
    token = _mint(allowed_personas=["Hermes", " atlas ", "hermes"])
    assert token["allowed_personas"] == ["hermes", "atlas"]


def test_mint_rejects_empty_roster() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        _mint(allowed_personas=[])


def test_mint_rejects_wildcard_sentinel() -> None:
    for sentinel in ("*", "all", "ANY", "wildcard"):
        with pytest.raises(ValueError, match="wildcard"):
            _mint(allowed_personas=[sentinel])


# ---------------------------------------------------------------------------
# verify_token — the happy path
# ---------------------------------------------------------------------------


def test_freshly_minted_token_verifies_ok() -> None:
    token = _mint()
    result = verify_token(token)
    assert result == VerifyResult(True, "ok")


# ---------------------------------------------------------------------------
# mint_tokens_for_plan — PASS mints one per node, FAIL mints none,
# zero change to gate PASS/FAIL semantics.
# ---------------------------------------------------------------------------


def test_pass_mints_exactly_one_token_per_approved_node() -> None:
    doc = load_plan_as_dag_doc(FIXTURES / "good_plan.md")
    result = score_file(FIXTURES / "good_plan.md", skill_map_path=FIXTURE_SKILL_MAP)
    assert result["overall_pass"] is True  # sanity: this fixture is a PASS plan

    tokens = mint_tokens_for_plan(doc, result, plan_id="PLAN-GOOD")

    assert len(tokens) == len(doc["nodes"]) == 2
    minted_task_ids = {t["task_id"] for t in tokens}
    assert minted_task_ids == {"A1", "A2"}
    for t in tokens:
        assert verify_token(t) == VerifyResult(True, "ok")


def test_fail_mints_no_tokens() -> None:
    doc = load_plan_as_dag_doc(FIXTURES / "bad_write_collision.md")
    result = score_file(FIXTURES / "bad_write_collision.md", skill_map_path=FIXTURE_SKILL_MAP)
    assert result["overall_pass"] is False  # sanity: this fixture is a FAIL plan

    tokens = mint_tokens_for_plan(doc, result, plan_id="PLAN-BAD")

    assert tokens == []


def test_mint_tokens_for_plan_never_touches_gate_verdict() -> None:
    """Zero change to gate PASS/FAIL semantics: calling mint_tokens_for_plan
    must not mutate the score_result dict the gate already produced."""
    doc = load_plan_as_dag_doc(FIXTURES / "good_plan.md")
    result = score_file(FIXTURES / "good_plan.md", skill_map_path=FIXTURE_SKILL_MAP)
    before = copy.deepcopy(result)

    mint_tokens_for_plan(doc, result, plan_id="PLAN-GOOD")

    assert result == before


# ---------------------------------------------------------------------------
# verify_token — fail-CLOSED matrix (design doc §6), each condition tested.
# ---------------------------------------------------------------------------


def test_verify_fails_closed_on_tampered_claim() -> None:
    token = _mint()
    tampered = dict(token)
    tampered["write_scope"] = ["app/**"]  # widened after mint — sig no longer matches
    result = verify_token(tampered)
    assert result.ok is False
    assert result.reason == "tampered"


def test_verify_fails_closed_on_tampered_signature_directly() -> None:
    token = _mint()
    tampered = dict(token)
    tampered["sig"] = "not-the-real-signature"
    result = verify_token(tampered)
    assert result.ok is False
    assert result.reason == "tampered"


def test_verify_fails_closed_on_expiry() -> None:
    issued = datetime.now(UTC) - timedelta(hours=5)
    token = _mint(issued_at=issued, ttl_seconds=60)  # expired 4h ago
    result = verify_token(token)
    assert result.ok is False
    assert result.reason == "expired"


def test_verify_allows_small_clock_skew_within_tolerance() -> None:
    """The 60s skew tolerance is the ONLY grace — just inside it still verifies."""
    issued = datetime.now(UTC) - timedelta(seconds=61)
    token = _mint(issued_at=issued, ttl_seconds=60)  # expires_at ~1s ago
    result = verify_token(token, skew_seconds=60)
    assert result.ok is True


def test_verify_fails_closed_on_alg_downgrade() -> None:
    token = _mint()
    downgraded = dict(token)
    downgraded["alg"] = "none"
    result = verify_token(downgraded)
    assert result.ok is False
    assert result.reason == "alg-downgrade"


def test_verify_fails_closed_on_unknown_kid() -> None:
    token = _mint()
    forged = dict(token)
    forged["kid"] = "kid-that-was-never-issued"
    result = verify_token(forged)
    assert result.ok is False
    assert result.reason == "unknown-kid"


def test_verify_fails_closed_on_jti_denylist() -> None:
    token = _mint()
    deny_jti(token["jti"], reason="node retracted after PASS")
    assert is_jti_denylisted(token["jti"]) is True

    result = verify_token(token)
    assert result.ok is False
    assert result.reason == "jti-denylisted"


def test_verify_fails_closed_on_unknown_schema_version() -> None:
    token = _mint()
    future = dict(token)
    future["schema_version"] = 2
    result = verify_token(future)
    assert result.ok is False
    assert result.reason == "unknown-schema-version"


def test_verify_fails_closed_on_absent_token() -> None:
    assert verify_token(None).ok is False
    assert verify_token({}).ok is False


def test_verify_fails_closed_on_missing_sig() -> None:
    token = _mint()
    del token["sig"]
    result = verify_token(token)
    assert result.ok is False
    assert result.reason == "absent"
