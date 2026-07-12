"""Agreement guard: broker registry and persona-alias-resolver classify the
retired base names (`forge` / `pipeline` / `quill`) IDENTICALLY — both refuse to
treat a bare base name as a legal dispatch target.

This closes P4-06 / re-audit OPEN-3, where the two PreToolUse:Task gates
disagreed: the registry approved a bare `forge` while the alias-resolver denied
it. The contract enforced here:

  * Broker side  — a bare base name is NOT in `ALLOWED_PERSONAS`, so
    `nexus_validate_brief` rejects it.
  * Hook side    — `persona-alias-resolver.sh` never lets a bare base name
    through as itself: with no scope hints it DENIES (exit 2); with scope hints
    it emits `additionalContext` redirecting to the split persona (exit 0) — it
    never emits a silent pass-through that keeps `subagent_type` as the bare
    base name.

Both surfaces therefore agree the base name is retired.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from broker.registry import ALLOWED_PERSONAS, RETIRED_BASE_PERSONAS

# tests/ -> nexus-broker/ -> repo root
REPO_ROOT = Path(__file__).resolve().parents[2]
ALIAS_RESOLVER = REPO_ROOT / ".claude" / "hooks" / "persona-alias-resolver.sh"


def _run_alias_resolver(subagent_type: str, brief: str) -> tuple[int, dict]:
    """Fire the real alias-resolver hook; return (exit_code, parsed_stdout_json).

    stdout is the hook's last JSON object (a deny object, or an
    additionalContext object). Returns ({}, ) shape-safe if stdout is empty.
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
        # The hook prints exactly one JSON object on stdout; take the last line.
        parsed = json.loads(out.splitlines()[-1])
    return proc.returncode, parsed


@pytest.mark.parametrize("base", sorted(RETIRED_BASE_PERSONAS))
def test_broker_rejects_bare_base_name(base: str) -> None:
    """The broker registry does not recognise a bare base name."""
    assert base not in ALLOWED_PERSONAS


@pytest.mark.parametrize("base", sorted(RETIRED_BASE_PERSONAS))
def test_alias_resolver_denies_bare_base_name_without_scope(base: str) -> None:
    """No scope hints → the hook DENIES (exit 2) with a real deny object."""
    code, parsed = _run_alias_resolver(base, "do some generic work")
    assert code == 2, f"expected exit 2 for unresolvable bare '{base}', got {code}"
    decision = parsed.get("hookSpecificOutput", {}).get("permissionDecision")
    assert decision == "deny", f"expected deny object for bare '{base}', got {parsed}"


# (base name, scope brief, expected split target) — resolvable cases.
_RESOLVABLE = [
    ("forge", "build an app/api server action route", "forge-wire"),
    ("forge", "build an app/components RSC page ui component", "forge-ui"),
    ("pipeline", "write polars transforms and duckdb write", "pipeline-data"),
    ("pipeline", "wire dramatiq workers and tableau clients", "pipeline-async"),
    ("quill", "author vitest react testing for .tsx", "quill-ts"),
    ("quill", "author pytest with a polars fixture in .py", "quill-py"),
]


@pytest.mark.parametrize(("base", "brief", "expected"), _RESOLVABLE)
def test_alias_resolver_redirects_resolvable_base_name(
    base: str, brief: str, expected: str
) -> None:
    """With scope hints the hook still never approves the bare base name — it
    redirects to the split persona via additionalContext (and that split target
    IS a legal broker persona)."""
    code, parsed = _run_alias_resolver(base, brief)
    assert code == 0, f"expected exit 0 (redirect) for '{base}' + scope, got {code}"
    ctx = parsed.get("hookSpecificOutput", {}).get("additionalContext", "")
    assert expected in ctx, f"expected redirect to '{expected}', got: {ctx!r}"
    # The redirect target is itself a valid dispatch persona (round-trips to the
    # broker), proving the hook points only at registry-legal names.
    assert expected in ALLOWED_PERSONAS


@pytest.mark.parametrize("base", sorted(RETIRED_BASE_PERSONAS))
def test_broker_and_alias_resolver_agree_base_name_is_not_dispatchable(
    base: str,
) -> None:
    """The load-bearing agreement: for a bare base name with no scope, BOTH the
    broker (registry membership) and the alias-resolver (hook decision) classify
    it as not-a-dispatch-target. Neither approves it."""
    broker_rejects = base not in ALLOWED_PERSONAS

    code, parsed = _run_alias_resolver(base, "do some generic work")
    decision = parsed.get("hookSpecificOutput", {}).get("permissionDecision")
    alias_rejects = code == 2 and decision == "deny"

    # Both sides must reject (the agreement) — and both must actually be True,
    # not merely equal-while-both-False.
    assert broker_rejects and alias_rejects, (
        f"broker/alias disagreement on bare '{base}': "
        f"broker_rejects={broker_rejects}, alias_rejects={alias_rejects}"
    )


@pytest.mark.parametrize("wrapper", ["tool_input", "input", None])
def test_alias_resolver_denies_bare_forge_across_payload_shapes(
    wrapper: str | None,
) -> None:
    """Regression for the live no-op bug: the resolver MUST read the real harness
    shape (`tool_input`), not only the legacy `input`/top-level shapes. Before the
    fix the `tool_input` case returned exit 0 (silent pass-through)."""
    inner = {
        "subagent_type": "forge",
        "description": "do some generic work",
        "prompt": "do some generic work",
    }
    payload = json.dumps({wrapper: inner} if wrapper else inner)
    proc = subprocess.run(
        ["bash", str(ALIAS_RESOLVER)],
        input=payload,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 2, f"shape={wrapper}: expected exit 2, got {proc.returncode}"
    parsed = json.loads(proc.stdout.strip().splitlines()[-1])
    assert parsed.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"
