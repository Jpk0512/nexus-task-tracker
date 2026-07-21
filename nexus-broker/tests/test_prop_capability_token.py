"""F3-05 property suite 1/5 — capability-token verify/tamper INVARIANTS.

Targets INVARIANTS of `broker.capability_token`, not examples:

  * TAMPER-EVIDENCE (design doc §6 fail-closed matrix): a token minted by
    `mint_token` verifies; ANY single-character mutation of its base64 `sig`,
    and ANY change to a signed claim value, breaks `verify_token` (ok=False).
    The HMAC covers `canonical_claims` (every field but `sig`), so the property
    is universally quantified over the tampered position/claim, not spot-checked.
  * CLOSED-SET SEMANTICS ∀ ROSTERS (DEC-096): for any roster, the signed
    `allowed_personas` claim reads back (via the live membership function
    `broker.daemon.deny_handlers._token_allowed_personas`) as EXACTLY the minted
    set; every member is authorized; a non-member is not; and the set cannot be
    WIDENED after mint without invalidating the signature — a tamper-proof
    closed set, never a bypass.

Regression corpus: EXPLICIT `@example(...)` decorators (checked into source,
version-controlled, reviewable) rather than the `.hypothesis/` example DB — the
DB is a machine cache that is not reliably committed, so shrunk edge cases are
pinned inline where a reviewer can see them.

Real data layer: real minted tokens signed by a real per-module key file (a
temp path, never the repo's `.memory/files/broker_token_key.json`). No mock of
the signing/verify boundary — the property exists to validate THAT boundary.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from hypothesis import assume, example, given
from hypothesis import strategies as st

from broker.capability_token import mint_token, verify_token
from broker.daemon.deny_handlers import _token_allowed_personas

pytestmark = pytest.mark.property

# base64url alphabet — the character set of a token `sig` (secrets → HMAC →
# urlsafe_b64encode, '=' stripped). A single-char tamper replaces one sig
# character with another drawn from this same set.
_B64URL = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"

_WILDCARDS = frozenset({"*", "all", "any", "everyone", "wildcard"})

# Already-normalized persona names (lowercase, no surrounding whitespace, never a
# wildcard sentinel) so `_normalize_allowed_personas` / `_token_allowed_personas`
# are the identity on them and the minted-set == generated-set equality is exact.
_persona = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz-", min_size=1, max_size=14
).filter(lambda s: s not in _WILDCARDS)


@pytest.fixture(scope="module")
def key_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """One temp signing key for the whole module — never the real repo key."""
    return tmp_path_factory.mktemp("f3_05_token_key") / "broker_token_key.json"


def _mint(key_path: Path, **overrides: object) -> dict:
    kwargs: dict = dict(
        plan_id="PLAN-F3-05",
        task_id="A1",
        persona="hermes",
        write_scope=["docs/**"],
        tier="T1",
        key_path=key_path,
    )
    kwargs.update(overrides)
    return mint_token(**kwargs)


@given(idx=st.integers(min_value=0, max_value=10_000), repl=st.sampled_from(_B64URL))
@example(idx=0, repl="A")
@example(idx=9999, repl="_")
def test_any_single_char_sig_tamper_fails_verification(
    key_path: Path, idx: int, repl: str
) -> None:
    """INVARIANT: flip ONE character of the signature → verification fails.

    A minted token verifies; replacing sig[i] (any i) with a DIFFERENT base64url
    character yields a token that `verify_token` rejects fail-closed. Keeps the
    token a well-formed dict, so this is a true single-'byte' tamper, not a
    structural corruption."""
    token = _mint(key_path)
    assert verify_token(token, key_path=key_path).ok, "baseline mint must verify"

    sig = token["sig"]
    i = idx % len(sig)
    replacement = repl if repl != sig[i] else ("A" if sig[i] != "A" else "B")
    tampered = {**token, "sig": sig[:i] + replacement + sig[i + 1 :]}

    assert tampered["sig"] != sig, "test must actually mutate a byte"
    result = verify_token(tampered, key_path=key_path)
    assert result.ok is False
    assert result.reason == "tampered"


@given(claim=st.sampled_from(["plan_id", "task_id", "persona", "tier"]))
@example(claim="persona")
@example(claim="plan_id")
def test_any_signed_claim_value_tamper_fails_verification(
    key_path: Path, claim: str
) -> None:
    """INVARIANT: mutating ANY signed claim value breaks the signature.

    `canonical_claims` signs every field but `sig`; changing a claim's value
    changes those bytes, so the recomputed HMAC no longer matches — fail-closed
    with reason 'tampered'."""
    token = _mint(key_path)
    assert verify_token(token, key_path=key_path).ok

    tampered = {**token, claim: str(token[claim]) + "-tampered"}
    result = verify_token(tampered, key_path=key_path)
    assert result.ok is False
    assert result.reason == "tampered"


@given(
    roster=st.lists(_persona, min_size=1, max_size=6, unique=True),
    outsider=_persona,
)
@example(roster=["hermes", "atlas"], outsider="scout")
@example(roster=["quill-py"], outsider="lens")  # degenerate one-element set
def test_allowed_personas_is_a_tamper_proof_closed_set(
    key_path: Path, roster: list[str], outsider: str
) -> None:
    """INVARIANT (∀ rosters): the signed `allowed_personas` set is EXACTLY the
    minted roster, authorizes exactly its members, and cannot be widened after
    mint without invalidating the signature."""
    normalized = {r.lower().strip() for r in roster}
    assume(outsider not in normalized)  # the outsider must be a true non-member

    token = mint_token(
        plan_id="PLAN-F3-05",
        task_id="W1",
        persona=roster[0],
        write_scope=["docs/**"],
        tier="T1",
        allowed_personas=roster,
        key_path=key_path,
    )
    assert verify_token(token, key_path=key_path).ok

    # Closed set == exactly the minted roster (order/dupes collapse to the set).
    allowed = _token_allowed_personas(token)
    assert allowed == normalized

    # Every member is inside; the outsider is not (closed-set membership).
    for member in normalized:
        assert member in allowed
    assert outsider not in allowed

    # Widening the set post-mint breaks the signature — the set is signed DATA,
    # never a widenable bypass (DEC-096 Option C permanently rejected).
    widened = {**token, "allowed_personas": [*token["allowed_personas"], outsider]}
    assert verify_token(widened, key_path=key_path).ok is False
