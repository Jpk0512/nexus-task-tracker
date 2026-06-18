"""Drift guard: every DISPATCHABLE_PERSONAS member must resolve to a deliverables.json
contract via exact-or-base-fallback.

The base-name fallback mirrors the logic added to verify-deliverables.sh:
  forge-ui / forge-wire / forge-*-pro  -> forge
  pipeline-data / pipeline-async / pipeline-*-pro -> pipeline
  quill-ts / quill-py -> quill

Personas whose contracts exist only as a fallback (e.g. lens-fast -> lens) are also
covered: the test uses the same resolution function the hook uses, so if the hook
would resolve it, the test passes.

This test must be RED before the deliverables.json palette/lens-fast entries AND the
base-name fallback are added, and GREEN after.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from broker.registry import DISPATCHABLE_PERSONAS

# Resolve to deliverables.json that the hook reads.
# The test runs from two locations:
#   live: nexus-broker/tests/  -> nexus-broker/ -> repo-root/
#         deliverables.json lives at repo-root/nexus-package/.claude/hooks/
#   package: nexus-package/nexus-broker/tests/ -> nexus-package/nexus-broker/ -> nexus-package/
#            deliverables.json lives at nexus-package/.claude/hooks/
# Strategy: walk up from __file__ until we find a parent containing ".claude/hooks/deliverables.json"
# directly (package context) or via "nexus-package/.claude/hooks/deliverables.json" (live context).
def _find_deliverables() -> Path:
    candidate = Path(__file__).resolve()
    for parent in candidate.parents:
        # Live tree: repo-root contains nexus-package/.claude/hooks/deliverables.json
        via_package = parent / "nexus-package" / ".claude" / "hooks" / "deliverables.json"
        if via_package.exists():
            return via_package
        # Package tree: nexus-package/ contains .claude/hooks/deliverables.json
        direct = parent / ".claude" / "hooks" / "deliverables.json"
        if direct.exists():
            return direct
    raise FileNotFoundError(
        "deliverables.json not found in any parent of "
        f"{Path(__file__).resolve()} (checked nexus-package/.claude/hooks/ and .claude/hooks/)"
    )

DELIVERABLES_PATH = _find_deliverables()


def _base_name(persona: str) -> str | None:
    """Return the base fallback key for a persona, or None if no fallback applies.

    Mirrors the fallback logic in verify-deliverables.sh embedded Python.
    """
    p = persona.lower()
    # Exact orchestrator / base tombstones — no fallback needed (they should be exact
    # keys or legitimately absent from the hook).
    if p in ("forge", "pipeline", "quill"):
        return None
    # Sub-variants: strip suffix
    if p.startswith("forge-"):
        return "forge"
    if p.startswith("pipeline-"):
        return "pipeline"
    if p.startswith("quill-"):
        return "quill"
    if p.startswith("lens-"):
        return "lens"
    # palette, scout, hermes, atlas, lens — exact match expected
    return None


def _resolve_contract(
    persona: str, config: dict
) -> dict | None:
    """Try exact match, then base-name fallback. Returns the contract or None."""
    p = persona.lower()
    # Exact
    for key, val in config.items():
        if key.lower() == p:
            return val
    # Base-name fallback
    base = _base_name(p)
    if base is not None:
        for key, val in config.items():
            if key.lower() == base:
                return val
    return None


@pytest.fixture(scope="module")
def deliverables_config() -> dict:
    assert DELIVERABLES_PATH.exists(), (
        f"deliverables.json not found at {DELIVERABLES_PATH}"
    )
    return json.loads(DELIVERABLES_PATH.read_text(encoding="utf-8"))


def test_deliverables_json_is_loadable(deliverables_config: dict) -> None:
    """Basic sanity: file parses and has at least one key."""
    assert isinstance(deliverables_config, dict)
    assert len(deliverables_config) > 0


@pytest.mark.parametrize("persona", sorted(DISPATCHABLE_PERSONAS))
def test_every_dispatchable_persona_has_a_contract(
    persona: str, deliverables_config: dict
) -> None:
    """Every dispatchable persona must resolve to a deliverables contract.

    Exact match is preferred; base-name fallback (forge-*/pipeline-*/quill-*/lens-*)
    is accepted. Personas with no contract at all cause the SubagentStop gate to be
    inert — that is the bug this test enforces against.
    """
    contract = _resolve_contract(persona, deliverables_config)
    assert contract is not None, (
        f"persona '{persona}' has no deliverables.json contract (exact or via base-name "
        f"fallback). Add an entry for '{persona}' or its base name "
        f"({_base_name(persona)!r}) to nexus-package/.claude/hooks/deliverables.json."
    )
