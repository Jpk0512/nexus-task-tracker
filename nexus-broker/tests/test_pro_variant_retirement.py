"""Agreement guard: broker registry and persona-alias-resolver retire the four
`-pro` escalation NAMES (R2-T03 FIX-4) IDENTICALLY — both refuse to treat a
retired `-pro` name as a legal dispatch target, and both redirect it to the
merged base persona.

This mirrors test_base_name_retirement.py's broker<->hook agreement pattern,
but for the four `-pro` names retired when each base/pro pair merged into one
tier-parameterized source:

  forge-ui-pro       -> forge-ui   (tier=pro)
  forge-wire-pro     -> forge-wire (tier=pro)
  pipeline-data-pro  -> pipeline-data  (tier=pro)
  pipeline-async-pro -> pipeline-async (tier=pro)

Unlike the bare base names (forge/pipeline/quill), which need a brief-scope
hint to resolve (there are two legal split targets each), a retired `-pro`
name has exactly ONE legal merge target — so the redirect is unconditional:
no scope hint is needed and there is no unresolvable-deny path for these four
names specifically.

The contract enforced here:

  * Broker side — none of the four retired `-pro` names is in
    `ALLOWED_PERSONAS`; each is a member of `RETIRED_PRO_PERSONAS`.
  * Hook side   — `persona-alias-resolver.sh` never lets a retired `-pro` name
    through as itself: it always emits `additionalContext` redirecting to the
    merged base persona (exit 0) — it never emits a silent pass-through that
    keeps `subagent_type` as the retired `-pro` name, and it never 500s /
    crashes on these inputs.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from broker.registry import ALLOWED_PERSONAS, RETIRED_PRO_PERSONAS

# tests/ -> nexus-broker/ -> repo root
REPO_ROOT = Path(__file__).resolve().parents[2]
ALIAS_RESOLVER = REPO_ROOT / ".claude" / "hooks" / "persona-alias-resolver.sh"

# (retired -pro name, expected merged base target)
_MERGES = [
    ("forge-ui-pro", "forge-ui"),
    ("forge-wire-pro", "forge-wire"),
    ("pipeline-data-pro", "pipeline-data"),
    ("pipeline-async-pro", "pipeline-async"),
]


def _run_alias_resolver(subagent_type: str, brief: str = "") -> tuple[int, dict]:
    """Fire the real alias-resolver hook; return (exit_code, parsed_stdout_json).

    stdout is the hook's last JSON object (a deny object, or an
    additionalContext object). Returns ({},) shape-safe if stdout is empty.
    """
    payload = json.dumps(
        {
            "tool_input": {
                "subagent_type": subagent_type,
                "description": brief,
                "prompt": brief,
            }
        }
    )
    proc = subprocess.run(
        ["bash", str(ALIAS_RESOLVER)],
        input=payload,
        capture_output=True,
        text=True,
        timeout=30,
    )
    out = proc.stdout.strip()
    parsed: dict = {}
    if out:
        parsed = json.loads(out.splitlines()[-1])
    return proc.returncode, parsed


@pytest.mark.parametrize("pro_name", sorted(RETIRED_PRO_PERSONAS))
def test_broker_rejects_retired_pro_name(pro_name: str) -> None:
    """The broker registry does not recognise a retired -pro name."""
    assert pro_name not in ALLOWED_PERSONAS


@pytest.mark.parametrize(("pro_name", "expected_base"), _MERGES)
def test_alias_resolver_redirects_retired_pro_name_unconditionally(
    pro_name: str, expected_base: str
) -> None:
    """A dispatch to a retired -pro name always redirects to its merged base —
    no scope hint required, and never a bare deny or a crash (exit != 0/1)."""
    # Empty brief on purpose: unlike bare base names, resolution here does not
    # depend on brief content — the merge target is unambiguous from the name.
    code, parsed = _run_alias_resolver(pro_name, "")
    assert code == 0, f"expected exit 0 (redirect) for retired '{pro_name}', got {code}"
    ctx = parsed.get("hookSpecificOutput", {}).get("additionalContext", "")
    assert expected_base in ctx, f"expected redirect to '{expected_base}', got: {ctx!r}"
    # The redirect target is itself a valid dispatch persona.
    assert expected_base in ALLOWED_PERSONAS


@pytest.mark.parametrize(("pro_name", "expected_base"), _MERGES)
def test_alias_resolver_never_denies_retired_pro_name(
    pro_name: str, expected_base: str
) -> None:
    """Never a bare deny (exit 2) for these four names — contrast with the bare
    base names (forge/pipeline/quill), which DO have an unresolvable-deny path.
    """
    code, parsed = _run_alias_resolver(pro_name, "do something vague")
    assert code == 0, f"retired '{pro_name}' must never deny, got exit {code}"
    decision = parsed.get("hookSpecificOutput", {}).get("permissionDecision")
    assert decision != "deny", f"retired '{pro_name}' must never be a bare deny: {parsed}"


@pytest.mark.parametrize(("pro_name", "expected_base"), _MERGES)
def test_broker_and_alias_resolver_agree_pro_name_is_not_dispatchable(
    pro_name: str, expected_base: str
) -> None:
    """The load-bearing agreement: for a retired -pro name, the broker (registry
    membership) rejects it AND the alias-resolver (hook decision) redirects it
    away from itself — neither treats it as a legal terminal dispatch target."""
    broker_rejects = pro_name not in ALLOWED_PERSONAS

    code, parsed = _run_alias_resolver(pro_name, "")
    ctx = parsed.get("hookSpecificOutput", {}).get("additionalContext", "")
    alias_redirects_away = code == 0 and expected_base in ctx and pro_name not in ctx.split(
        "maps to"
    )[0]

    assert broker_rejects, f"broker still allows retired '{pro_name}'"
    assert alias_redirects_away, (
        f"alias-resolver did not redirect retired '{pro_name}' to '{expected_base}': {parsed}"
    )


def test_no_500_or_crash_on_any_retired_pro_name() -> None:
    """Explicit acceptance-criteria check: dispatch to any of the 4 retired
    names is a clean deny-or-redirect, never an uncaught crash. A crash would
    show up as a non-{0,2} exit code or empty stdout with no parseable JSON."""
    for pro_name, _ in _MERGES:
        code, parsed = _run_alias_resolver(pro_name, "some brief text")
        assert code in (0, 2), f"'{pro_name}' produced unexpected exit code {code} (possible crash)"
        assert parsed, f"'{pro_name}' produced no parseable hook output (possible crash)"
