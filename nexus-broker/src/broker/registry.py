"""Persona × intent legality table for the Nexus capability broker.

Base names `forge` / `pipeline` / `quill` are RETIRED — they are NOT dispatch
targets and deliberately absent here. As of R2-T03 FIX-4, the four `-pro`
escalation NAMES for the merged base/pro pairs
(`forge-ui-pro`, `forge-wire-pro`, `pipeline-data-pro`, `pipeline-async-pro`)
are ALSO retired as dispatch targets: each base/pro pair collapsed to one
tier-parameterized source (`tier=base|pro`), so escalation is now expressed as
a parameter on `forge-ui` / `forge-wire` / `pipeline-data` / `pipeline-async`,
not as a separate persona name. A dispatch to a retired `-pro` name must be
rejected by the broker exactly as `.claude/hooks/persona-alias-resolver.sh`
rejects it — see `tests/test_base_name_retirement.py` and
`tests/test_pro_variant_retirement.py` for the enforced agreement.

BUILD-SNAPSHOT NOTE (fable-planner, DEC-035): this module is byte-synced whole
into `nexus-package/nexus-broker/src/broker/registry.py` by
`tools/build_snapshot.sh` (unlike `.claude/**`, which is hand-reconciled per
copy). Adding `fable-planner` here therefore also makes it legal in the
PACKAGE broker, even though no `nexus-package/.claude/agents/fable-planner.md`
ships and neither the package's `dispatch-shape-guard.sh` nor `router_core.py`
name it — it stays practically undispatchable there (no on-disk agent file,
no package dispatch-shape allow-list entry), but the package's own
`test_deliverables_persona_drift.py` still requires a
`nexus-package/.claude/hooks/deliverables.json` contract row for every
`DISPATCHABLE_PERSONAS` member. That row exists and is marked inert; see its
comment for the full accounting.
"""
from __future__ import annotations

import os
from pathlib import Path


def _default_codex_agents_dir() -> Path:
    """Resolve the real repo's `.claude/agents/` dir: walk up from this file to
    find the repo root (`.memory/` dir is the marker — same convention as
    `broker.state._find_repo_root` / `broker.node_contract._default_codex_lane_flag_path`,
    duplicated here rather than imported so this module stays import-independent
    of those, matching the existing per-module pattern). `NEXUS_CODEX_AGENTS_DIR`
    env-overrides the resolved path so a test can point detection at an empty
    dir without touching the real `.claude/` tree.
    """
    override = os.environ.get("NEXUS_CODEX_AGENTS_DIR")
    if override:
        return Path(override)
    here = Path(__file__).resolve()
    for candidate in [here, *here.parents]:
        if (candidate / ".memory").is_dir():
            return candidate / ".claude" / "agents"
    return Path.cwd() / ".claude" / "agents"


def _codex_lane_agent_files_present(agents_dir: Path | None = None) -> bool:
    """RDEC-011 decorrelated-judge lane is a this-machine contrib feature, not a
    shipped package feature: only register codex-worker/codex-reviewer as legal
    dispatch targets when their agent files actually exist. A package install
    ships no `.claude/agents/codex-worker.md` / `codex-reviewer.md` (and no
    matching `deliverables.json` contract), so leaving these unconditionally in
    `PERSONA_INTENTS` there would make
    `test_deliverables_persona_drift.py::test_every_dispatchable_persona_has_a_contract`
    fail by construction. Conditional registration keeps the live tree (files
    present) green and the package tree (files absent) green, without either
    side special-casing the other.
    """
    directory = agents_dir if agents_dir is not None else _default_codex_agents_dir()
    return (directory / "codex-worker.md").is_file() and (directory / "codex-reviewer.md").is_file()


_CODEX_LANE_PRESENT: bool = _codex_lane_agent_files_present()

PERSONA_INTENTS: dict[str, list[str]] = {
    "scout": ["investigate"],
    "forge-wire": ["implement_ui", "implement_api"],
    "forge-ui": ["implement_ui", "implement_api"],
    "pipeline-data": ["implement_ingestion"],
    "pipeline-async": ["implement_ingestion"],
    "atlas": ["implement_schema"],
    "hermes": ["implement_wiring", "implement_api"],
    "lens": ["validate"],
    "lens-fast": ["validate"],
    "quill-ts": ["test"],
    "quill-py": ["test"],
    "palette": ["design"],
    # Plexus-meta-only deep-planning persona (DEC-035: scoped subagent-
    # recursion exception, .memory decision log). Deliberately NOT shipped as
    # nexus-package/.claude/agents/fable-planner.md — see this module's build
    # snapshot note below and nexus-package/.claude/hooks/deliverables.json's
    # "fable-planner" entry for why the package still needs a contract row.
    "fable-planner": ["author_plan", "plan"],
    # R3-T04 (FIX-5, node N10): the gated plan/decomposition-authoring persona.
    # Live ONLY because its independent plan-validation gate
    # (.claude/hooks/plan-validation-gate.py, SubagentStop, fail-closed —
    # wraps N08's broker.plan_validation.score_plan, which already folds in
    # N11's invocability check) landed in the SAME commit as this entry — the
    # FIX-5 invariant is that an ungated live planner must never exist, not
    # even transiently. Orchestrator-mechanism-only (see NON_CLASSIFIER_PERSONAS
    # below): Plexus dispatches it deliberately as part of its own planning
    # flow, never the user-prompt router classifier. Package accounting
    # mirrors fable-planner's: `nexus-package/.claude/agents/planner.md` stays
    # a `dispatchable: false` forward stub (never flipped live there), but
    # this dict is synced whole into the package by build_snapshot.sh, so
    # `nexus-package/.claude/hooks/deliverables.json` still needs an INERT
    # "planner" row for test_deliverables_persona_drift.py.
    "planner": ["plan"],
}

if _CODEX_LANE_PRESENT:
    # RDEC-011 decorrelated-judge lane: out-of-family (OpenAI Codex) relay
    # personas. codex-worker is a relay implementer (executes a brief on the
    # Codex CLI and hands back its result byte-faithfully); codex-reviewer is
    # the decorrelated review/judge seat (SOUND/REVISE/UNSOUND verdicts,
    # never judging codex-worker's own output — cross-vendor rule). Both are
    # orchestrator-dispatched only when the codex lane is enabled
    # (bin/codex-lane status) — see .claude/agents/codex-worker.md and
    # codex-reviewer.md. Registered ONLY when those agent files are present
    # (see `_codex_lane_agent_files_present` above) — a this-machine contrib
    # feature, not a shipped package feature, so a package install (no codex
    # agent files, no deliverables.json contract for them) never has these
    # names enter DISPATCHABLE_PERSONAS in the first place.
    PERSONA_INTENTS["codex-worker"] = ["implement_relay"]
    PERSONA_INTENTS["codex-reviewer"] = ["validate"]

# Base persona names retired in favour of split variants. Kept as an explicit
# constant so the agreement test and the drift guard can assert they are absent
# from ALLOWED_PERSONAS rather than relying on them merely not being listed.
RETIRED_BASE_PERSONAS: frozenset[str] = frozenset({"forge", "pipeline", "quill"})

# R2-T03 FIX-4: the four `-pro` escalation NAMES retired when each base/pro pair
# merged into one tier-parameterized source. Escalation now lives as a `tier`
# parameter on the merged persona, never as a separate dispatchable name. Kept
# explicit (mirroring RETIRED_BASE_PERSONAS) so the agreement test can assert
# absence from ALLOWED_PERSONAS rather than relying on omission alone.
RETIRED_PRO_PERSONAS: frozenset[str] = frozenset(
    {
        "forge-ui-pro",
        "forge-wire-pro",
        "pipeline-data-pro",
        "pipeline-async-pro",
    }
)

# ── OPT-002: the broker registry is the SINGLE SOURCE OF TRUTH for the
# dispatchable-persona roster ────────────────────────────────────────────────
# This module is canonical because (a) the broker gate (`server.py`) already
# refuses any dispatch whose persona is not in ALLOWED_PERSONAS — so the registry
# is the surface that actually decides "can this be dispatched"; (b) it is the
# one roster definition that lives inside the importable `broker` package, so
# every Python consumer (router_train.label) can DERIVE from it instead of
# re-listing; (c) the agents-dir⊆registry drift test already treats it as the
# required superset. Two derived consumers single-source from here:
#   - broker.router_train.label.NEXUS_PERSONAS  (training-grade allow-list) imports
#     DISPATCHABLE_PERSONAS directly.
#   - .claude/hooks/router_core.build_persona_enum cannot hard-import this module
#     (the UserPromptSubmit hook runs under system Python with no `broker` on
#     sys.path), so it re-lists CLASSIFIER_PERSONAS as a module constant and the
#     agreement test (tests/test_router_persona_roster.py) asserts the two match —
#     the same no-import + CI-agreement pattern already used for
#     RETIRED_BASE_PERSONAS.
#
# DISPATCHABLE_PERSONAS = every name the broker / escalation path may legally
# name. CLASSIFIER_PERSONAS = the subset the router *classifier* may emit. They
# differ by NON_CLASSIFIER_PERSONAS: personas the ORCHESTRATOR dispatches as a
# mechanism, never the user-prompt classifier. `lens-fast` is orchestrator-only —
# it is dispatched as the parallel fast-lane sibling of `lens` after an
# implementer's NEXUS:DONE, not selected from a user request.
#
# R2-T03 FIX-4 supersedes the prior OPT-062 note here: the four `-pro` names
# are no longer classifier-emittable NAMES — each base/pro pair merged into one
# tier-parameterized source, so escalation is expressed via a `tier=pro`
# parameter on the merged persona, not via a distinct dispatchable name. The
# classifier may still request escalation; it does so by setting tier=pro on
# the base name, not by emitting e.g. "forge-ui-pro".
DISPATCHABLE_PERSONAS: frozenset[str] = frozenset(PERSONA_INTENTS.keys())

# Dispatchable, but NOT emitted by the router classifier — orchestrator-mechanism
# personas only. `lens-fast` is dispatched in a fixed parallel pair with `lens`
# by the orchestrator's Lens gate, never chosen from a user prompt. `planner`
# (R3-T04) is likewise orchestrator-driven only — Plexus dispatches it as part
# of its own planning flow, never in response to a raw user utterance; keeping
# it out of CLASSIFIER_PERSONAS also means no router_core.py mirror-list edit
# is needed for it (see test_router_persona_roster.py's agreement tests).
# `codex-worker` / `codex-reviewer` (RDEC-011 decorrelated-judge lane), when
# present (see `_CODEX_LANE_PRESENT` above), are the same shape: both agent
# files are explicitly "Nexus-dispatched only — NOT for direct user
# invocation" — the orchestrator selects them via a node-contract
# `executor: codex` leg or the Lens-adjacent judge seat, never via the
# user-prompt classifier — so they belong here for the identical reason
# `lens-fast` does, and for the identical practical benefit: router_core.py's
# hardcoded CLASSIFIER_PERSONAS mirror (system-Python hook env, no broker
# import) needs no edit to stay in agreement. Conditional so a package
# install (codex agent files absent, `_CODEX_LANE_PRESENT` False) never adds
# the names PERSONA_INTENTS never registered in the first place.
NON_CLASSIFIER_PERSONAS: frozenset[str] = frozenset(
    {"lens-fast", "planner"} | ({"codex-worker", "codex-reviewer"} if _CODEX_LANE_PRESENT else set())
)

# The persona enum the router classifier may emit (DISPATCHABLE minus the
# orchestrator-only mechanism personas). router_core.build_persona_enum must
# render exactly this set (plus the synthetic 'meta' no-dispatch route).
CLASSIFIER_PERSONAS: frozenset[str] = DISPATCHABLE_PERSONAS - NON_CLASSIFIER_PERSONAS

# Back-compat alias: ALLOWED_PERSONAS is the historical name for the broker's
# legal-dispatch set. It IS the dispatchable roster.
ALLOWED_PERSONAS: frozenset[str] = DISPATCHABLE_PERSONAS
ALLOWED_INTENTS: frozenset[str] = frozenset(
    intent for intents in PERSONA_INTENTS.values() for intent in intents
)
