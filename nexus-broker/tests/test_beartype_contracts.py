"""NEX-002 proof: beartype's claw (activated in conftest.py, see
_beartype_activation.py) catches a real broker.* type violation at the call
boundary — not merely at import/collection time.

`mint_token`'s `plan_id: str` param is called with an int. Without the claw
activation this sails straight into signature construction / signing and
either blows up much further downstream with an opaque error or, worse,
silently serializes a malformed capability token. With beartype active the
violation is caught immediately, at the exact call site."""
from __future__ import annotations

import pytest
from beartype.roar import BeartypeCallHintParamViolation

from broker.capability_token import mint_token


def test_mint_token_wrong_plan_id_type_raises_beartype_violation():
    with pytest.raises(BeartypeCallHintParamViolation):
        mint_token(
            plan_id=12345,  # str expected — deliberately wrong, proves the contract
            task_id="task-1",
            persona="hermes",
            write_scope=None,
            tier="T1",
        )


def test_mint_token_correct_types_still_succeeds():
    """Sanity companion: beartype's claw enforces the contract without
    blocking a legitimately-typed call — the roar above is a real violation,
    not activation noise."""
    token = mint_token(
        plan_id="plan-1",
        task_id="task-1",
        persona="hermes",
        write_scope=["docs/**"],
        tier="T1",
    )
    assert token["plan_id"] == "plan-1"
