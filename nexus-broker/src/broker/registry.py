"""Persona × intent legality table for the Nexus capability broker.

Base names `forge` / `pipeline` / `quill` are RETIRED — they are NOT dispatch
targets and deliberately absent here. The split personas
(`forge-ui` / `forge-wire`, `pipeline-data` / `pipeline-async`,
`quill-ts` / `quill-py`) plus their `-pro` escalation variants are the only
implementers the broker recognises. A bare base name must be rejected by the
broker exactly as `.claude/hooks/persona-alias-resolver.sh` rejects it — see
`tests/test_base_name_retirement.py` for the enforced agreement.
"""
from __future__ import annotations

PERSONA_INTENTS: dict[str, list[str]] = {
    "scout": ["investigate"],
    "forge-wire": ["implement_ui", "implement_api"],
    "forge-wire-pro": ["implement_ui", "implement_api"],
    "forge-ui": ["implement_ui", "implement_api"],
    "forge-ui-pro": ["implement_ui", "implement_api"],
    "pipeline-data": ["implement_ingestion"],
    "pipeline-data-pro": ["implement_ingestion"],
    "pipeline-async": ["implement_ingestion"],
    "pipeline-async-pro": ["implement_ingestion"],
    "atlas": ["implement_schema"],
    "hermes": ["implement_wiring", "implement_api"],
    "lens": ["validate"],
    "lens-fast": ["validate"],
    "quill-ts": ["test"],
    "quill-py": ["test"],
    "palette": ["design"],
}

# Base persona names retired in favour of split variants. Kept as an explicit
# constant so the agreement test and the drift guard can assert they are absent
# from ALLOWED_PERSONAS rather than relying on them merely not being listed.
RETIRED_BASE_PERSONAS: frozenset[str] = frozenset({"forge", "pipeline", "quill"})

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
# implementer's NEXUS:DONE, not selected from a user request. The four `-pro`
# escalation variants REMAIN classifier-emittable (audit OPT-062: the classifier
# must be ABLE to escalate `complex`/low-confidence work; the orchestrator's
# PreToolUse escalation gate is the action-side partner, not a replacement).
DISPATCHABLE_PERSONAS: frozenset[str] = frozenset(PERSONA_INTENTS.keys())

# Dispatchable, but NOT emitted by the router classifier — orchestrator-mechanism
# personas only. `lens-fast` is dispatched in a fixed parallel pair with `lens`
# by the orchestrator's Lens gate, never chosen from a user prompt.
NON_CLASSIFIER_PERSONAS: frozenset[str] = frozenset({"lens-fast"})

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
