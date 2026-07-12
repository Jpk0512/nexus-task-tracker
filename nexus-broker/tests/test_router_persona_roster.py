"""OPT-002 agreement guard: the dispatchable-persona roster is single-sourced.

The roster of "who Nexus may dispatch" historically drifted across >=4 hand-synced
places: router_core.build_persona_enum (read agents/*.md, EXCLUDED the four -pro
variants so the classifier could not escalate), router_train.NEXUS_PERSONAS (a
hardcoded allow-list), the broker registry, and the agent roster on disk.

The single source of truth is now ``broker.registry``:

  - ``DISPATCHABLE_PERSONAS`` — every name the broker / escalation path may legally
    name (== ``ALLOWED_PERSONAS`` == ``PERSONA_INTENTS.keys()``).
  - ``CLASSIFIER_PERSONAS`` — the subset the router *classifier* may emit
    (DISPATCHABLE minus orchestrator-only mechanism personas like ``lens-fast``).
    This INCLUDES the four ``-pro`` escalation variants per audit OPT-062.

Two consumers DERIVE from that source; this module asserts neither has drifted:

  1. ``broker.router_train.label.NEXUS_PERSONAS`` imports ``DISPATCHABLE_PERSONAS``
     directly — assert identity here as a belt-and-braces regression guard.
  2. ``.claude/hooks/router_core`` cannot import the broker package (it runs under
     system Python with no ``broker`` on sys.path), so it MIRRORS the registry as
     module constants ``RETIRED_BASE_PERSONAS`` and ``CLASSIFIER_PERSONAS``. This
     test imports BOTH and asserts they are identical — the same no-import +
     CI-agreement pattern ``test_base_name_retirement.py`` uses for the
     broker↔alias-resolver agreement. If anyone edits one roster without the other,
     CI fails here.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from broker.registry import (
    ALLOWED_PERSONAS,
    CLASSIFIER_PERSONAS,
    DISPATCHABLE_PERSONAS,
    NON_CLASSIFIER_PERSONAS,
    PERSONA_INTENTS,
    RETIRED_BASE_PERSONAS,
    RETIRED_PRO_PERSONAS,
)
from broker.router_train.label import NEXUS_PERSONAS

# router_core lives under .claude/hooks (system-Python hook env, not on the test
# path by default) — mirror how test_router.py imports it.
REPO_ROOT = Path(__file__).resolve().parents[2]
HOOKS_DIR = REPO_ROOT / ".claude" / "hooks"
LIVE_AGENTS_DIR = HOOKS_DIR.parent / "agents"
sys.path.insert(0, str(HOOKS_DIR))
import router_core  # type: ignore[import]  # noqa: E402, I001

# DEC-035: fable-planner (and R3-T04's planner) stay CLASSIFIER_PERSONAS members
# in broker.registry — that module is synced byte-for-byte into the package by
# build_snapshot.sh, so it can't diverge per-repo — but each ships NO on-disk
# nexus-package/.claude/agents/*.md file, by design (deliverables.json marks
# their contract rows "INERT" for exactly this reason: no agent file, no
# package dispatch-shape-guard entry, practically undispatchable there).
# build_persona_enum only ever emits CLASSIFIER_PERSONAS & on-disk-agent-files,
# so in the package tree the enum legitimately omits them while the live tree
# (which does ship fable-planner.md) legitimately includes them. The set below
# is read from deliverables.json's own "_note" field rather than hardcoded, so
# it can never drift silently from the documented exemption.
#
# deliverables.json's own two locations (mirrors test_deliverables_persona_drift.py's
# resolution): live tree -> REPO_ROOT/nexus-package/.claude/hooks/deliverables.json;
# package tree -> REPO_ROOT/.claude/hooks/deliverables.json directly.
_DELIVERABLES_VIA_PACKAGE = REPO_ROOT / "nexus-package" / ".claude" / "hooks" / "deliverables.json"
_DELIVERABLES_DIRECT = REPO_ROOT / ".claude" / "hooks" / "deliverables.json"
DELIVERABLES_JSON = (
    _DELIVERABLES_VIA_PACKAGE if _DELIVERABLES_VIA_PACKAGE.is_file() else _DELIVERABLES_DIRECT
)


def _package_inert_personas() -> frozenset[str]:
    if not DELIVERABLES_JSON.is_file():
        return frozenset()
    config = json.loads(DELIVERABLES_JSON.read_text(encoding="utf-8"))
    return frozenset(
        name
        for name, contract in config.items()
        if isinstance(contract, dict) and "INERT" in contract.get("_note", "")
    )


# ── The single source is internally consistent ──────────────────────────────


def test_registry_is_the_single_source() -> None:
    """The registry's three roster views agree by construction."""
    assert DISPATCHABLE_PERSONAS == ALLOWED_PERSONAS
    assert frozenset(PERSONA_INTENTS.keys()) == DISPATCHABLE_PERSONAS
    # CLASSIFIER is exactly DISPATCHABLE minus the orchestrator-only mechanism set.
    assert CLASSIFIER_PERSONAS == DISPATCHABLE_PERSONAS - NON_CLASSIFIER_PERSONAS
    # The retired bases never leak into any dispatchable view.
    assert not (RETIRED_BASE_PERSONAS & DISPATCHABLE_PERSONAS)
    # R2-T03 FIX-4: the retired -pro names never leak into any dispatchable view.
    assert not (RETIRED_PRO_PERSONAS & DISPATCHABLE_PERSONAS)


# ── router_train.NEXUS_PERSONAS derives from the source (no separate list) ───


def test_router_train_allow_list_derives_from_registry() -> None:
    """NEXUS_PERSONAS is the registry's DISPATCHABLE set — not a re-listed copy."""
    assert NEXUS_PERSONAS == DISPATCHABLE_PERSONAS
    # Identity (same frozenset object): proves it is the imported source, not a
    # value-equal duplicate that could silently diverge.
    assert NEXUS_PERSONAS is DISPATCHABLE_PERSONAS


# ── router_core mirrors the source (no-import + CI agreement) ────────────────


def test_router_retired_set_matches_broker_retired_set() -> None:
    """router_core.RETIRED_BASE_PERSONAS mirrors the broker registry's."""
    assert router_core.RETIRED_BASE_PERSONAS == RETIRED_BASE_PERSONAS


def test_router_classifier_set_matches_broker_classifier_set() -> None:
    """router_core.CLASSIFIER_PERSONAS mirrors broker.registry.CLASSIFIER_PERSONAS."""
    assert router_core.CLASSIFIER_PERSONAS == CLASSIFIER_PERSONAS


# ── The router enum agrees with the source AND includes -pro / excludes lens-fast


def test_router_enum_equals_classifier_set_plus_meta() -> None:
    """build_persona_enum renders exactly CLASSIFIER_PERSONAS + the 'meta' route,
    modulo the DEC-035 package-inert exemption (fable-planner / planner: real
    CLASSIFIER_PERSONAS members with NO shipped nexus-package/.claude/agents/*.md
    file — see deliverables.json's "INERT" notes). Asserted against the agents
    dir for whichever repo (live or package) this test is running in — the
    roster the router actually serves THERE.
    """
    enum = router_core.build_persona_enum(str(LIVE_AGENTS_DIR))
    assert "meta" in enum, "the synthetic no-dispatch 'meta' route must be present"
    on_disk_stems = {p.stem for p in LIVE_AGENTS_DIR.glob("*.md")}
    inert_and_missing = _package_inert_personas() - on_disk_stems
    expected = CLASSIFIER_PERSONAS - inert_and_missing
    assert set(enum) - {"meta"} == expected, (
        "router enum drifted from the single-source CLASSIFIER_PERSONAS roster "
        "(minus any DEC-035 package-inert personas legitimately absent on disk here); "
        f"enum (minus meta)={sorted(set(enum) - {'meta'})}, "
        f"expected={sorted(expected)}, "
        f"CLASSIFIER_PERSONAS={sorted(CLASSIFIER_PERSONAS)}, "
        f"inert_and_missing={sorted(inert_and_missing)}"
    )
    # No duplicates.
    assert len(enum) == len(set(enum))


def test_router_enum_excludes_retired_pro_variant_names() -> None:
    """R2-T03 FIX-4: the four -pro escalation NAMES are retired dispatch targets.

    Each base/pro pair merged into one tier-parameterized source
    (`tier=base|pro`), so escalation is now expressed as a `tier=pro` parameter
    on the merged persona, never as a distinct classifier-emittable name. This
    supersedes the pre-R2-T03 OPT-062 assertion that these names WERE
    classifier-emittable — that was true only while the -pro variants were
    separate dispatchable personas.
    """
    enum = set(router_core.build_persona_enum(str(LIVE_AGENTS_DIR)))
    from broker.registry import RETIRED_PRO_PERSONAS

    leaked = RETIRED_PRO_PERSONAS & enum
    assert not leaked, f"retired -pro names leaked into the router enum: {sorted(leaked)}"


def test_router_enum_excludes_orchestrator_only_personas() -> None:
    """lens-fast is dispatchable but orchestrator-only — never classifier-emitted.

    It is dispatched as the fixed parallel sibling of `lens` after an implementer
    NEXUS:DONE, not selected from a user prompt, so it must NOT be in the enum the
    classifier picks from — even though it IS a legal broker dispatch target.
    """
    enum = set(router_core.build_persona_enum(str(LIVE_AGENTS_DIR)))
    assert NON_CLASSIFIER_PERSONAS, "expected at least one orchestrator-only persona"
    leaked = NON_CLASSIFIER_PERSONAS & enum
    assert not leaked, f"orchestrator-only personas leaked into the classifier enum: {sorted(leaked)}"
    # But they ARE legal broker dispatch targets.
    assert NON_CLASSIFIER_PERSONAS <= DISPATCHABLE_PERSONAS


def test_router_enum_excludes_retired_base_names() -> None:
    """The retired base names (forge/pipeline/quill) are never in the router enum."""
    enum = set(router_core.build_persona_enum(str(LIVE_AGENTS_DIR)))
    leaked = RETIRED_BASE_PERSONAS & enum
    assert not leaked, f"retired base names leaked into the router enum: {sorted(leaked)}"
