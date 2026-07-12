"""Nexus Capability Broker — FastMCP server exposing nexus_validate_brief."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from typing import Any, TypedDict

from fastmcp import FastMCP

from broker.db import log_broker_validation
from broker.registry import ALLOWED_PERSONAS, PERSONA_INTENTS
from broker.state import (
    REPO_ROOT,
    BrokerState,
    is_notepad_fresh,
    read_state,
    write_state,
)

mcp = FastMCP("nexus-broker")

# DEC-019 — categories the self-feedback tool accepts (mirrors nexus_feedback.category).
_FEEDBACK_CATEGORIES = frozenset(
    {
        "gate_deny",
        "gate_needs_decision",
        "gate_revise_stall",
        "unclear_persona",
        "unclear_skill",
        "missing_context",
        "roster_mismatch",
        "workflow_friction",
        "other",
    }
)
_FEEDBACK_SEVERITIES = frozenset({"critical", "high", "medium", "low", "info"})

REQUIRED_BRIEF_FIELDS = ("goal", "context_files", "acceptance_criteria", "verification_required", "do_not_touch")

# NATIVE-25 — consolidated dispatch PRE-FLIGHT. These two sets MIRROR
# .claude/hooks/broker-gate.py (CODE_WRITING_PERSONAS / CODE_WRITING_INTENTS) and
# .claude/hooks/skills-required-guard.py (CODE_WRITING_PERSONAS) so that the
# verdicts the downstream PreToolUse gates enforce are surfaced HERE, in one
# validate call, instead of across five sequential dispatch attempts. This is NOT
# new policy — it moves the SAME conditions earlier. Drift between these copies is
# guarded by nexus-broker/tests/test_drift_guard.py / the gate-agreement tests.
CODE_WRITING_PERSONAS = frozenset(
    {
        "forge",
        "forge-ui",
        "forge-ui-pro",
        "forge-wire",
        "forge-wire-pro",
        "pipeline",
        "pipeline-data",
        "pipeline-data-pro",
        "pipeline-async",
        "pipeline-async-pro",
        "atlas",
        "hermes",
        "quill",
        "quill-ts",
        "quill-py",
    }
)
CODE_WRITING_INTENTS = frozenset(
    {
        "implement_ui",
        "implement_api",
        "implement_ingestion",
        "implement_schema",
        "implement_wiring",
        "test",
    }
)


def _is_code_writing(persona: str, intent: str) -> bool:
    """A dispatch writes feature code iff its persona OR intent is code-writing.

    Mirrors broker-gate.py:_is_code_writing exactly — the planning-gate and
    skills_required gates both pivot on this predicate, so validate must use the
    identical condition to pre-surface their verdicts without diverging.
    """
    return persona in CODE_WRITING_PERSONAS or intent in CODE_WRITING_INTENTS


# ── Dispatch-speed program — NORMALIZE-instead-of-REJECT ─────────────────────
# Relayed user feedback + Plexus self-experience: almost every dispatch cost
# 2-3 round-trips on MECHANICAL field-shape rejections that had nothing to do
# with brief QUALITY. The helpers below COERCE the mechanical shape and emit a
# warning instead of an error, so a brief that is substantively fine but
# shape-imperfect approves on the FIRST call. They NEVER relax a real guardrail:
# invalid persona, JSON-parse failure, Complex stale-notepad, and the
# skills-required HARD CHECK remain hard errors elsewhere in the validator.

_VALID_TASK_TIERS = frozenset({"simple", "standard", "complex"})

# Router pre-fill `difficulty` vocabulary → broker `task_tier` vocabulary. The
# router classifier emits a `difficulty` token; the broker only knows
# `task_tier`. When a brief carries difficulty but no task_tier, coerce it.
_DIFFICULTY_TO_TIER = {
    "trivial": "simple",
    "easy": "simple",
    "simple": "simple",
    "low": "simple",
    "medium": "standard",
    "moderate": "standard",
    "standard": "standard",
    "normal": "standard",
    "hard": "complex",
    "complex": "complex",
    "high": "complex",
    "difficult": "complex",
}

# work_type tokens that imply genuine FEATURE work — the only class that should
# require a planning-gate row at standard/complex tier. A bugfix/chore/meta/
# refactor brief is NOT a feature and MUST NOT require the planning-gate row.
_FEATURE_WORK_TYPES = frozenset(
    {
        "feature",
        "implement_ui",
        "implement_api",
        "implement_ingestion",
        "implement_schema",
        "implement_wiring",
    }
)


def _normalize_context_files(value: Any) -> tuple[list[str], str | None]:
    """Coerce context_files to a non-empty list[str]; warn if anything changed.

    Mechanical shapes seen in the wild: a bare string ("server.py"), a None /
    missing value, or an empty list. All are SHAPE problems, not quality
    problems — a brief with no files declared still points at the repo. Default
    to a single inferred entry ['.'] (the repo root) so the value stays
    non-empty and well-typed. Returns (normalized, warning_or_none).
    """
    if isinstance(value, list):
        cleaned = [str(f).strip() for f in value if str(f).strip()]
        if cleaned:
            return cleaned, None
        return ["."], (
            "normalized: 'context_files' was empty — defaulted to ['.'] "
            "(repo root); name explicit files for a tighter sub-agent scope"
        )
    if isinstance(value, str) and value.strip():
        return [value.strip()], (
            "normalized: 'context_files' was a bare string — coerced to a "
            f"single-element list ['{value.strip()}']"
        )
    return ["."], (
        "normalized: 'context_files' was missing or empty — defaulted to ['.'] "
        "(repo root); name explicit files for a tighter sub-agent scope"
    )


def _normalize_acceptance_criteria(value: Any) -> tuple[list[str], str | None]:
    """Coerce acceptance_criteria to a non-empty list[str]; warn if changed.

    Accepts a bare string (single criterion) or a missing/empty value. A missing
    acceptance bar defaults to a single placeholder so the brief stays shaped;
    the warning nudges the dispatcher to state a real bar.
    """
    if isinstance(value, list):
        cleaned = [str(c).strip() for c in value if str(c).strip()]
        if cleaned:
            return cleaned, None
        return ["work completed as described in goal"], (
            "normalized: 'acceptance_criteria' was empty — defaulted to a "
            "placeholder; state explicit acceptance criteria for a sharper oracle"
        )
    if isinstance(value, str) and value.strip():
        return [value.strip()], (
            "normalized: 'acceptance_criteria' was a bare string — coerced to a "
            "single-element list"
        )
    return ["work completed as described in goal"], (
        "normalized: 'acceptance_criteria' was missing or empty — defaulted to "
        "a placeholder; state explicit acceptance criteria for a sharper oracle"
    )


def _normalize_verification_required(value: Any) -> tuple[list[str], str | None]:
    """Coerce verification_required to a non-empty list[str]; warn if changed.

    A bare string becomes a single-element list; a missing/empty value defaults
    to a manual-review placeholder so the field is never an empty array.
    """
    if isinstance(value, list):
        cleaned = [str(v).strip() for v in value if str(v).strip()]
        if cleaned:
            return cleaned, None
        return ["manual review"], (
            "normalized: 'verification_required' was empty — defaulted to "
            "['manual review']; name explicit verification commands"
        )
    if isinstance(value, str) and value.strip():
        return [value.strip()], (
            "normalized: 'verification_required' was a bare string — coerced to "
            "a single-element list"
        )
    return ["manual review"], (
        "normalized: 'verification_required' was missing or empty — defaulted "
        "to ['manual review']; name explicit verification commands"
    )


def _normalize_do_not_touch(value: Any) -> tuple[list[str], str | None]:
    """Coerce do_not_touch to a list[str]; warn if changed.

    Unlike the other collections an EMPTY do_not_touch is legitimate (nothing is
    off-limits), so the empty list is preserved without a warning. Only a wrong
    TYPE (string / None / missing) is coerced.
    """
    if isinstance(value, list):
        return [str(d).strip() for d in value if str(d).strip()], None
    if isinstance(value, str) and value.strip():
        return [value.strip()], (
            "normalized: 'do_not_touch' was a bare string — coerced to a "
            "single-element list"
        )
    return [], (
        "normalized: 'do_not_touch' was missing — defaulted to [] (nothing "
        "off-limits); declare protected paths explicitly if any exist"
    )


def _normalize_goal(value: Any) -> tuple[str, str | None]:
    """Coerce goal to a non-empty string; warn if a placeholder was injected.

    An empty / missing goal is a SHAPE gap — but goal is the single most
    quality-bearing field. We still normalize to a placeholder (so the brief is
    shaped and the dispatch is not bounced) BUT the warning is loud: a
    placeholder goal is almost certainly a mistake the dispatcher should fix.
    """
    text = str(value).strip() if value is not None else ""
    if text:
        return text, None
    return "(goal not specified — see acceptance_criteria)", (
        "normalized: 'goal' was empty or missing — injected a placeholder. A "
        "real goal is strongly recommended; this brief approves but the "
        "sub-agent has no stated objective"
    )


def _normalize_files_touched_estimate(value: Any) -> tuple[int, str | None]:
    """Coerce files_touched_estimate to an int >= 1; warn if changed.

    Accepts an int as-is. An ARRAY coerces to its length (the dispatcher listed
    the files). A numeric string parses. Anything unparseable defaults to 1.
    This field is NOT in REQUIRED_BRIEF_FIELDS and is advisory only — it is
    normalized purely so a wrong shape never bounces a dispatch.
    """
    if isinstance(value, bool):
        return 1, (
            "normalized: 'files_touched_estimate' was a bool — defaulted to 1"
        )
    if isinstance(value, int):
        return (value if value >= 1 else 1), None
    if isinstance(value, list):
        n = len(value)
        return (n if n >= 1 else 1), (
            "normalized: 'files_touched_estimate' was a list — coerced to its "
            f"length ({n if n >= 1 else 1})"
        )
    if isinstance(value, str) and value.strip():
        try:
            n = int(value.strip())
        except (TypeError, ValueError):
            return 1, (
                "normalized: 'files_touched_estimate' was non-numeric — "
                "defaulted to 1"
            )
        return (n if n >= 1 else 1), (
            "normalized: 'files_touched_estimate' was a string — coerced to int"
        )
    return 1, None


def _normalize_task_tier(brief: dict[str, Any]) -> tuple[str, str | None]:
    """Resolve task_tier, COERCING from `difficulty` and snapping invalid values.

    Resolution order (each step warns):
      1. A valid task_tier value (case-normalized) is taken as-is.
      2. An INVALID task_tier is snapped to 'standard' + warn.
      3. task_tier MISSING but `difficulty` present → map difficulty→tier + warn.
      4. Both missing → infer a safe default from work_type (feature-like work
         defaults to 'standard'; everything else to 'standard' too — 'standard'
         is the validator's historical default) + warn.

    `difficulty` is a router pre-fill vocabulary token that lives in the brief
    only as a hint; the broker stores `task_tier` exclusively.
    """
    raw_tier = brief.get("task_tier")
    if raw_tier is not None and str(raw_tier).strip():
        tier = str(raw_tier).strip().lower()
        if tier in _VALID_TASK_TIERS:
            return tier, None
        return "standard", (
            f"normalized: task_tier '{raw_tier}' is not one of "
            f"{sorted(_VALID_TASK_TIERS)} — snapped to 'standard'"
        )

    difficulty = brief.get("difficulty")
    if difficulty is not None and str(difficulty).strip():
        diff = str(difficulty).strip().lower()
        mapped = _DIFFICULTY_TO_TIER.get(diff)
        if mapped:
            return mapped, (
                f"normalized: task_tier was missing — coerced from "
                f"difficulty '{difficulty}' to tier '{mapped}'"
            )
        return "standard", (
            f"normalized: task_tier was missing and difficulty '{difficulty}' "
            "is unrecognized — defaulted to 'standard'"
        )

    return "standard", (
        "normalized: task_tier was missing — defaulted to 'standard'"
    )


def _snap_intent_to_legal(persona: str, intent: str) -> tuple[str, str | None]:
    """Snap an illegal intent to the nearest legal one for a VALID persona.

    Strategy (case-insensitive):
      1. Exact match (case-normalized) → no snap.
      2. Token/substring overlap with a legal intent → pick the best-overlapping
         legal intent.
      3. No reasonable match → fall back to the persona's PRIMARY (first) intent.

    Returns (snapped_intent, warning_or_none). Caller must only invoke this for a
    persona that IS in PERSONA_INTENTS — an INVALID persona is a HARD error and
    is never snapped (routing, not shape).
    """
    legal = PERSONA_INTENTS[persona]
    want = (intent or "").strip().lower()
    if want in {li.lower() for li in legal}:
        # Return the canonically-cased legal intent matching the request.
        for li in legal:
            if li.lower() == want:
                return li, None
    want_tokens = set(want.replace("-", "_").split("_")) if want else set()

    best: str | None = None
    best_score = 0
    for candidate in legal:
        cand_tokens = set(candidate.lower().replace("-", "_").split("_"))
        overlap = len(want_tokens & cand_tokens)
        substr = 1 if (want and (want in candidate.lower() or candidate.lower() in want)) else 0
        score = overlap + substr
        if score > best_score:
            best_score = score
            best = candidate

    if best is not None and best_score > 0:
        return best, (
            f"normalized: intent '{intent}' is not legal for persona "
            f"'{persona}' — snapped to nearest legal intent '{best}' "
            f"(allowed: {legal})"
        )

    primary = legal[0]
    return primary, (
        f"normalized: intent '{intent}' is not legal for persona '{persona}' "
        f"and had no close match — snapped to the persona's primary intent "
        f"'{primary}' (allowed: {legal})"
    )


def _derive_notepad_topic(work_type: str, intent: str, goal: str) -> str:
    """Derive a notepad_topic for a standard/complex brief that omitted one.

    The topic is the scope hint a sub-agent uses to load the right notepad; a
    missing one is a SHAPE gap, not a quality gap. Derive a slug from the most
    specific available signal (work_type → intent → goal) so the dispatch is not
    bounced for a missing topic.
    """
    for source in (work_type, intent):
        token = (source or "").strip().lower().replace(" ", "-")
        if token:
            return token[:48]
    words = (goal or "").strip().lower().split()
    if words:
        return "-".join(words[:4])[:48]
    return "general"


def _normalize_skills_required(value: Any) -> list[str]:
    """Coerce skills_required to a clean list[str], mirroring the guard's parse.

    Accepts a list (filtering blanks) OR a free-text 'a, b, c' string — the same
    shapes .claude/hooks/skills-required-guard.sh now detects. Anything else
    (None, int, dict) collapses to an empty list, which the HARD CHECK treats as
    'missing' for a code-writing persona.
    """
    if isinstance(value, list):
        return [str(s).strip() for s in value if str(s).strip()]
    if isinstance(value, str):
        return [s.strip() for s in value.split(",") if s.strip()]
    return []


# ── Phase 3 — ADVISORY pre-dispatch decomposition forcing-function ───────────
# Personas that NEVER write feature code (orchestrators + read-only recon/verify).
# A run of single-Agent dispatches to these is expected (a Scout fan-out, repeated
# Lens passes), so they are EXEMPT from the decomposition nudge. Mirrors the
# DEC-027 gate-exempt list documented in CLAUDE.md.
DECOMP_NUDGE_EXEMPT_PERSONAS = frozenset(
    {"plexus", "nexus", "scout", "lens", "lens-fast", "palette"}
)

# How many CONSECUTIVE single-agent dispatches (with no Workflow/fanout since the
# last one or session start) trip the advisory. Env-overridable, default 3.
_DEFAULT_DECOMP_NUDGE_THRESHOLD = 3


def _decomp_nudge_threshold() -> int:
    """Threshold for the nudge, env-overridable; falls back to default on garbage."""
    raw = os.getenv("NEXUS_DECOMP_NUDGE_THRESHOLD")
    if not raw:
        return _DEFAULT_DECOMP_NUDGE_THRESHOLD
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return _DEFAULT_DECOMP_NUDGE_THRESHOLD
    return value if value > 0 else _DEFAULT_DECOMP_NUDGE_THRESHOLD


def _consecutive_single_dispatches() -> int:
    """Count single dispatches since the last fanout this session (fail-open).

    Reads .memory/files/router_dispatches.jsonl (written by the dispatch-capture
    hook). validate_brief has no session_id argument, so the CURRENT session is
    taken to be the session_id of the LAST recorded row, and the tail run of
    consecutive dispatch_kind=="single" rows FOR THAT SESSION is counted — i.e.
    how many serial dispatches have happened since the most recent "fanout" (or
    session start). A "fanout" resets the count to 0. Rows missing dispatch_kind
    are treated as "single" (the hook default) for back-compat with pre-Phase-3
    logs; rows for OTHER sessions are skipped.

    ANY error (missing file, unreadable, bad JSON) returns 0 so the caller emits no
    nudge — the advisory is strictly fail-open and never affects approval.
    """
    path = REPO_ROOT / ".memory" / "files" / "router_dispatches.jsonl"
    try:
        records: list[dict[str, Any]] = []
        with path.open() as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if isinstance(rec, dict):
                    records.append(rec)
    except OSError:
        return 0

    if not records:
        return 0
    current_session = records[-1].get("session_id")
    if not current_session or current_session == "unknown":
        return 0

    run = 0
    for rec in records:
        if rec.get("session_id") != current_session:
            continue
        if rec.get("dispatch_kind") == "fanout":
            run = 0
        else:
            run += 1
    return run


def _serial_justified(brief: dict[str, Any]) -> bool:
    """True when the brief DECLARES the work is genuinely serial/indivisible.

    Reads an OPTIONAL `decomposition` field: {independent_units:int,
    serial_justification?:str}. Its absence is fine (returns False — nudge may
    still fire). A non-empty serial_justification, or independent_units<=1,
    SUPPRESSES the nudge. Malformed shapes fail-open to False (no suppression).
    """
    decomposition = brief.get("decomposition")
    if not isinstance(decomposition, dict):
        return False
    justification = decomposition.get("serial_justification")
    if isinstance(justification, str) and justification.strip():
        return True
    units = decomposition.get("independent_units")
    if isinstance(units, bool):
        return False
    return bool(isinstance(units, int) and units <= 1)


# Width threshold for the disjoint-file advisory (DEC-029).
_WIDTH_DISJOINT_THRESHOLD = 4


def _width_disjoint_trigger(brief: dict[str, Any]) -> str | None:
    """Return an advisory warning when a wide disjoint brief should be a Workflow.

    Fires when ALL of:
      (a) decomposition.no_read_after_write is explicitly True (the dispatcher
          declared the files are write-disjoint — no cross-file read-after-write),
      (b) width >= _WIDTH_DISJOINT_THRESHOLD (default 4).

    width = files_touched_estimate (normalized int, >=1) when present in the brief,
    else len(context_files) (the fallback; already a non-empty list post-normalize).

    Returns None when the signal is absent or width is below threshold.
    Advisory only: the caller MUST NOT put this into errors[] or flip approved.
    Warning prefix MUST start '[decomposition]' so _has_decomp_nudge detector
    recognizes it downstream.
    """
    decomposition = brief.get("decomposition")
    if not isinstance(decomposition, dict):
        return None
    # The signal must be the explicit Python True — not truthy, not 1.
    if decomposition.get("no_read_after_write") is not True:
        return None

    # Determine width: prefer the normalized files_touched_estimate when present.
    if "files_touched_estimate" in brief:
        fte = brief["files_touched_estimate"]
        width = int(fte) if isinstance(fte, int) and fte >= 1 else 1
    else:
        cf = brief.get("context_files", [])
        width = len(cf) if isinstance(cf, list) and len(cf) >= 1 else 1

    if width < _WIDTH_DISJOINT_THRESHOLD:
        return None

    return (
        f"[decomposition] {width} write-disjoint files declared "
        f"(no_read_after_write=true, width>={_WIDTH_DISJOINT_THRESHOLD}). "
        "Consider authoring ONE Workflow with parallel teammates instead of a "
        "single serial Agent — parallel writes are safe here (Art. XIII.d, DEC-029). "
        "If this is genuinely indivisible, declare it via the brief's "
        "`decomposition.serial_justification`. (advisory — not blocking)"
    )


class BrokerResult(TypedDict):
    approved: bool
    warnings: list[str]
    errors: list[str]
    approved_brief: dict[str, Any] | None


async def nexus_validate_brief(
    persona: str,
    intent: str,
    brief_json: str,
    turn_id: str,
    router_pre_fill: str | None = None,
    team_name: str | None = None,
) -> BrokerResult:
    errors: list[str] = []
    warnings: list[str] = []

    # 1. Persona legality — HARD (routing, not shape; an invalid persona is
    #    NEVER snapped/normalized, per the guardrail contract).
    persona_valid = persona in ALLOWED_PERSONAS
    if not persona_valid:
        errors.append(
            f"persona '{persona}' is not in the dispatch registry — "
            "built-in agents are reserved for orchestrator-internal use only"
        )

    # 2. Persona × intent legality — NORMALIZE (R8). For a VALID persona an
    #    illegal intent is a mechanical SHAPE problem (a freeform / mis-typed
    #    intent token), not a routing failure: snap it to the nearest legal
    #    intent for that persona and WARN instead of rejecting. An invalid
    #    persona is left to the hard error above — we do not snap an intent for a
    #    persona we cannot route at all.
    if persona_valid:
        snapped_intent, intent_warning = _snap_intent_to_legal(persona, intent)
        if intent_warning is not None:
            intent = snapped_intent
            warnings.append(intent_warning)

    # 3. Brief JSON parse + required fields
    brief: dict[str, Any] = {}
    json_parse_failed = False
    try:
        brief = json.loads(brief_json)
    except json.JSONDecodeError as exc:
        errors.append(f"brief_json is not valid JSON: {exc}")
        json_parse_failed = True

    if not json_parse_failed:
        # ── NORMALIZE-instead-of-REJECT (dispatch-speed program) ──────────────
        # Every required field is COERCED into a well-typed, non-empty shape and
        # the brief dict is MUTATED in place so the approved_brief / state write
        # persists the normalized values. A coercion appends a WARNING (never an
        # error), so a substantively-fine but shape-imperfect brief approves on
        # the FIRST call. Genuinely-unfixable gaps (none, post-normalization,
        # for these required fields — every one has a safe default) are the only
        # things that would land in errors[]; they are COLLECTED here and
        # returned together in this one response (never one-at-a-time).

        # goal (R2-missing / R3-empty) — normalize to a placeholder + loud warn.
        goal_value, goal_warning = _normalize_goal(brief.get("goal"))
        brief["goal"] = goal_value
        if goal_warning is not None:
            warnings.append(goal_warning)

        # context_files (R2-missing / R4a-type / R4b-empty) — coerce to list.
        cf_value, cf_warning = _normalize_context_files(brief.get("context_files"))
        brief["context_files"] = cf_value
        if cf_warning is not None:
            warnings.append(cf_warning)

        # acceptance_criteria (R2-missing / R5a-type / R5b-empty) — coerce.
        ac_value, ac_warning = _normalize_acceptance_criteria(
            brief.get("acceptance_criteria")
        )
        brief["acceptance_criteria"] = ac_value
        if ac_warning is not None:
            warnings.append(ac_warning)

        # verification_required (R2-missing / R6-empty) — coerce.
        vr_value, vr_warning = _normalize_verification_required(
            brief.get("verification_required")
        )
        brief["verification_required"] = vr_value
        if vr_warning is not None:
            warnings.append(vr_warning)

        # do_not_touch (R2-missing) — coerce; empty list is legitimate.
        dnt_value, dnt_warning = _normalize_do_not_touch(brief.get("do_not_touch"))
        brief["do_not_touch"] = dnt_value
        if dnt_warning is not None:
            warnings.append(dnt_warning)

        # files_touched_estimate — NOT required, advisory; coerce type only when
        # the dispatcher supplied it so a wrong shape (array / numeric string)
        # never bounces a dispatch.
        if "files_touched_estimate" in brief:
            fte_value, fte_warning = _normalize_files_touched_estimate(
                brief.get("files_touched_estimate")
            )
            brief["files_touched_estimate"] = fte_value
            if fte_warning is not None:
                warnings.append(fte_warning)

    # 4. Notepad ritual check
    #    - task_tier in {standard, complex} (i.e. NOT simple): the brief MUST
    #      carry a non-empty notepad_topic. This makes the notepad load-bearing
    #      (P2-07 / GAP-06): approved=true is only granted when the dispatcher
    #      has declared the notepad scope it will hand the sub-agent.
    #    - notepad freshness (notepad_logged_at within window) is still required
    #      for Complex; advisory otherwise.
    state = read_state()
    # task_tier — NORMALIZE: resolve from task_tier (snap invalid → standard) or
    # coerce from a router pre-fill `difficulty` token when task_tier is absent.
    # MUTATE the brief so the persisted approved_brief carries the resolved tier.
    if json_parse_failed:
        task_tier = "standard"
    else:
        task_tier, tier_warning = _normalize_task_tier(brief)
        brief["task_tier"] = task_tier
        if tier_warning is not None:
            warnings.append(tier_warning)
    is_standard_or_complex = task_tier in {"standard", "complex"}

    if is_standard_or_complex and not json_parse_failed:
        # notepad_topic (R9) — NORMALIZE: a missing topic is a SHAPE gap, not a
        # quality gap. DERIVE one from work_type → intent → goal and WARN instead
        # of rejecting, then mutate the brief so the derived scope is persisted.
        notepad_topic = str(brief.get("notepad_topic", "")).strip()
        if not notepad_topic:
            derived = _derive_notepad_topic(
                str(brief.get("work_type", "")), intent, str(brief.get("goal", ""))
            )
            brief["notepad_topic"] = derived
            warnings.append(
                f"normalized: 'notepad_topic' was missing for this {task_tier} "
                f"dispatch — derived '{derived}'; set an explicit notepad_topic "
                "for a tighter sub-agent scope"
            )

    notepad_fresh = is_notepad_fresh(state)
    if not notepad_fresh:
        if task_tier == "complex":
            errors.append(
                "notepad ritual required for Complex tasks — "
                "log a planning note before dispatching"
            )
        else:
            warnings.append(
                "notepad_logged_at is absent or stale — "
                "consider running notepad list before dispatching"
            )

    # 5. Router pre-fill mismatch (warning only)
    if router_pre_fill and router_pre_fill != persona:
        warnings.append(
            f"router pre-fill was '{router_pre_fill}' but dispatching '{persona}' "
            "— confirm override is intentional"
        )

    # 6. NATIVE-25 — CONSOLIDATED DISPATCH PRE-FLIGHT.
    #    Surface, in this ONE validate call, the downstream-gate requirements that
    #    otherwise only fire (and DENY) at Task-dispatch time — killing the
    #    '5 dispatch attempts' friction. This is NOT new policy: each sub-check
    #    mirrors the EXACT condition the corresponding PreToolUse gate enforces.
    #
    #    Scope guard: a malformed brief, a non-code-writing persona, a
    #    non-feature / Plexus-meta dispatch, and Simple tier are ALL left
    #    untouched — validate must not newly block them and must not warn them.
    if not json_parse_failed:
        work_type = str(brief.get("work_type", "")).strip().lower()
        code_writing = _is_code_writing(persona, intent) or _is_code_writing(
            persona, work_type
        )

        # (a) skills_required HARD CHECK — moves skills-required-guard.sh Gate-1
        #     EARLIER. For a code-writing persona, an absent / malformed /
        #     empty skills_required is a HARD reject (flips approved=false) with a
        #     reason naming the requirement. Non-code-writing personas: unaffected.
        if persona in CODE_WRITING_PERSONAS:
            normalized_skills = _normalize_skills_required(brief.get("skills_required"))
            if not normalized_skills:
                errors.append(
                    f"skills_required is absent or empty for code-writing persona "
                    f"'{persona}' — per CONTRACT R19 every code-writing brief MUST "
                    "name explicit skills (an array, or a 'skills_required: a, b' "
                    "prose line). This is enforced downstream by skills-required-guard; "
                    "validate pre-checks it so the dispatch is not denied later. "
                    "See docs/agents/SKILL_MAP.md for the per-persona minimum."
                )

        # (b) planning-gate ADVISORY — SCOPED to GENUINE FEATURE work only.
        #     Constitution Art. I's spec-first / planning-gate mandate applies to
        #     FEATURE work, not to every code-writing dispatch: a bugfix / chore /
        #     refactor / meta dispatch (even at standard/complex tier with a
        #     code-writing persona) is NOT a feature and MUST NOT require a
        #     planning-gate row. The advisory therefore fires only when ALL hold:
        #       (1) tier in {standard, complex} (Simple is always exempt),
        #       (2) the dispatch is code-writing (persona or intent), AND
        #       (3) work_type names genuine FEATURE work (_FEATURE_WORK_TYPES) —
        #           the new scope narrowing. A blank / bugfix / chore / meta
        #           work_type yields NO advisory.
        #     ADVISORY ONLY: it never flips approved, and it is condition-derived
        #     (validate does NOT read project.db's planning_gate table, to avoid
        #     coupling + drift with broker-gate).
        is_feature_work = work_type in _FEATURE_WORK_TYPES
        if is_standard_or_complex and code_writing and is_feature_work:
            warnings.append(
                f"PLANNING-GATE ADVISORY: this {task_tier} code-writing dispatch to "
                f"'{persona}' will require an ACCEPTED planning-gate row within 4h "
                "or broker-gate will DENY at dispatch time. Run "
                "'python3 .memory/log.py planning-gate submit --feat <id> --json ...' "
                "BEFORE dispatching (Constitution Art. I, spec-first). This is "
                "advisory — it does not block this validation."
            )

    # 6b. Phase 3 — decomposition forcing-function (3-tier escalation).
    #
    #     The `decomposition` brief field is OPTIONAL: {
    #       independent_units?: string[],
    #       serial_justification?: string,   # escape hatch at N>=6
    #       serial_override?: bool,          # hard-override at N>=9
    #     }
    #
    #     Tier 1 — N >= NEXUS_DECOMP_NUDGE_THRESHOLD (default 3):
    #       ADVISORY warning only; approved is unaffected.
    #
    #     Tier 2 — N >= 6 (FORCED PAUSE):
    #       approved=False unless brief carries a non-empty
    #       decomposition.serial_justification (a real dependency reason) — that
    #       string is the escape hatch.  A fan-out dispatch resets the counter to 0
    #       (the counter never deadlocks when you actually parallelize).
    #
    #     Tier 3 — N >= 9 (HARD BLOCK):
    #       approved=False; escapable ONLY by
    #         (a) decomposition.serial_override: true (with justification), OR
    #         (b) actually fanning out (resets counter to 0).
    #
    #     NEVER DEADLOCK INVARIANT: there is ALWAYS a forward path.
    #       N<6  — proceed, advisory only.
    #       N>=6 — add serial_justification to the brief, or fan out.
    #       N>=9 — set serial_override:true with justification, or fan out.
    #
    #     Suppressed for read-only/recon personas and when json_parse_failed.
    #     Fail-open: any read error on the dispatch log => count 0 => advisory only.
    #
    #     IMPORTANT: `approved` is computed AFTER this block so tier-2/3 errors
    #     can contribute to `errors` and legitimately flip it false.
    _DECOMP_FORCED_PAUSE_THRESHOLD = 6
    _DECOMP_HARD_BLOCK_THRESHOLD = 9

    if (
        not json_parse_failed
        and persona not in DECOMP_NUDGE_EXEMPT_PERSONAS
    ):
        consecutive = _consecutive_single_dispatches()
        nudge_threshold = _decomp_nudge_threshold()

        if consecutive >= _DECOMP_HARD_BLOCK_THRESHOLD:
            # Tier 3: HARD BLOCK — serial_override with justification, or fan out.
            decomp = brief.get("decomposition") if not json_parse_failed else None
            has_override = (
                isinstance(decomp, dict)
                and decomp.get("serial_override") is True
                and isinstance(decomp.get("serial_justification"), str)
                and decomp.get("serial_justification", "").strip()
            )
            if not has_override:
                errors.append(
                    f"[decomposition] HARD BLOCK: {consecutive} consecutive single-agent "
                    "dispatches with no Workflow/fanout. At N>=9 the orchestrator MUST "
                    "either (a) fan out — use a Workflow or parallel dispatch, which resets "
                    "the counter — or (b) include decomposition.serial_override=true with a "
                    "non-empty serial_justification explaining the irreducible dependency. "
                    "There is always a forward path: serial_override escapes this block."
                )

        elif consecutive >= _DECOMP_FORCED_PAUSE_THRESHOLD:
            # Tier 2: FORCED PAUSE — serial_justification escapes, or fan out.
            if not _serial_justified(brief):
                errors.append(
                    f"[decomposition] FORCED PAUSE: {consecutive} consecutive single-agent "
                    "dispatches with no Workflow/fanout. At N>=6 you must either (a) fan out "
                    "— use a Workflow or parallel dispatch, which resets the counter — or "
                    "(b) include a non-empty decomposition.serial_justification in the brief "
                    "explaining the genuine dependency. There is always a forward path."
                )

        elif consecutive >= nudge_threshold:
            # Tier 1: ADVISORY only — no error added, approved unaffected.
            # Suppressed when brief declares serial/indivisible work (same as
            # the old behaviour: _serial_justified suppresses the advisory).
            if not _serial_justified(brief):
                warnings.append(
                    f"[decomposition] This is the {consecutive}th consecutive "
                    "single-agent dispatch this session with no Workflow. If the "
                    "remaining work has >=2 INDEPENDENT units, author ONE Workflow now "
                    "(Art XIII.d) rather than continuing serial; if it is genuinely "
                    "dependent/indivisible, proceed and (optionally) declare it via the "
                    "brief's `decomposition` field. (advisory — not blocking)"
                )

        # Width-disjoint trigger (DEC-029): advisory nudge when the brief declares
        # >=4 write-disjoint files (no_read_after_write=True) — a Workflow can
        # safely fan these out in parallel. Suppressed for exempt personas and when
        # _serial_justified (same guard as tier-1/2 advisory, checked inline).
        if not _serial_justified(brief):
            width_msg = _width_disjoint_trigger(brief)
            if width_msg is not None:
                warnings.append(width_msg)

    approved = len(errors) == 0

    # 7. Write broker_state.json on approval
    if approved:
        new_state = BrokerState(
            turn_id=turn_id,
            approved=True,
            persona=persona,
            called_at=datetime.now(tz=UTC).isoformat(),
            notepad_logged_at=state.get("notepad_logged_at"),
        )
        # Only write team_name when non-empty — an empty string would confuse
        # the consumer (broker-gate.py reads state_team and relaxes staleness
        # only when it is truthy).
        if team_name:
            new_state["team_name"] = team_name
        # TASK-083: single-source the dispatch-gate brief. The validator already
        # parsed the full brief here; persist its gate-relevant fields so the
        # dispatch gates read them from state instead of forcing the orchestrator
        # to re-embed a full JSON brief in every Agent prompt. `intent` is the
        # function argument (the brief itself need not carry it); task_tier
        # normalizes to the same default the validator used above.
        new_state["approved_brief"] = {
            "task_tier": task_tier,
            "work_type": str(brief.get("work_type", "")),
            "intent": intent,
            "skills_required": brief.get("skills_required", []),
        }
        write_state(new_state)

    # 8. Log to DB (fire-and-forget)
    log_broker_validation(
        persona=persona,
        intent=intent,
        turn_id=turn_id,
        router_pre_fill=router_pre_fill,
        approved=approved,
        errors=errors,
    )

    return BrokerResult(
        approved=approved,
        warnings=warnings,
        errors=errors,
        approved_brief=brief if approved else None,
    )


@mcp.tool()
async def nexus_validate_brief_tool(
    persona: str,
    intent: str,
    brief_json: str,
    turn_id: str,
    router_pre_fill: str | None = None,
    team_name: str | None = None,
) -> BrokerResult:
    """Validate a Nexus delegation brief before Task dispatch.

    Returns approved=true if persona, intent, brief fields, and notepad ritual
    are all satisfied. Must be called before every Task dispatch from Nexus.

    team_name: optional — supply when dispatching a dynamic-Workflow teammate so
    the broker writes team_name into state, enabling the per-(team,persona)
    turn-staleness relaxation in broker-gate.py.
    """
    return await nexus_validate_brief(
        persona=persona,
        intent=intent,
        brief_json=brief_json,
        turn_id=turn_id,
        router_pre_fill=router_pre_fill,
        team_name=team_name,
    )


@mcp.tool()
async def nexus_notepad_ping() -> dict[str, str]:
    """Record that Nexus has completed the notepad ritual for this turn.

    Call this immediately after running 'python3 .memory/log.py notepad list'.
    Updates notepad_logged_at in broker_state.json so nexus_validate_brief
    accepts Complex task dispatches without requiring a stale-notepad error.

    NATIVE-4 / PING-1 semantics:
    - Always updates notepad_logged_at (the ping's primary purpose).
    - If and only if the current state already has approved=True, also refreshes
      called_at so a >120s turn does not deadlock the gate (liveness heartbeat).
    - An empty or unapproved state MUST NOT have called_at written; that would
      fabricate an approval the broker never granted.
    """
    now = datetime.now(tz=UTC).isoformat()
    state = read_state()
    state["notepad_logged_at"] = now
    # NATIVE-4: refresh called_at only when the state already carries an approval.
    # This makes ping a true per-turn liveness heartbeat without forging approvals.
    if state.get("approved") is True:
        state["called_at"] = now
    write_state(state)
    return {"notepad_logged_at": now, "status": "recorded"}


async def nexus_submit_feedback(
    severity: str,
    category: str,
    message: str,
    context_json: str | None = None,
) -> dict[str, Any]:
    """Self-feedback (DEC-019): record one Nexus-friction row via log.py feedback add.

    Validates severity/category, enriches context with the current broker_state
    turn/persona, and shells out to the per-project memory CLI. Always returns a
    dict — never raises into the MCP transport.
    """
    sev = (severity or "").strip().lower()
    cat = (category or "").strip().lower()
    msg = (message or "").strip()

    if sev not in _FEEDBACK_SEVERITIES:
        return {"ok": False, "error": f"invalid severity '{severity}'", "id": None}
    if cat not in _FEEDBACK_CATEGORIES:
        return {"ok": False, "error": f"invalid category '{category}'", "id": None}
    if not msg:
        return {"ok": False, "error": "message must be non-empty", "id": None}

    # Enrich the supplied context (or build one) with the current dispatch context
    # from broker_state.json so the harvest has persona/turn attribution.
    state = read_state()
    ctx: dict[str, Any] = {}
    if context_json:
        try:
            parsed = json.loads(context_json)
            if isinstance(parsed, dict):
                ctx = parsed
        except (json.JSONDecodeError, ValueError):
            ctx = {"raw_context": context_json}
    ctx.setdefault("turn_id", state.get("turn_id"))
    ctx.setdefault("persona", state.get("persona"))
    ctx.setdefault("team_name", state.get("team_name"))

    # Version-stamp the feedback row at capture time. Read the installed version
    # from .memory/.nexus-version (written by safe_update.py) and pass it through
    # so broker-initiated feedback carries version attribution automatically —
    # no caller ever supplies it. Fail-soft to 'unknown' if the file is missing.
    try:
        nexus_version = (REPO_ROOT / ".memory" / ".nexus-version").read_text().strip() or "unknown"
    except OSError:
        nexus_version = "unknown"

    log_py = REPO_ROOT / ".memory" / "log.py"
    cmd = [
        sys.executable,
        str(log_py),
        "feedback",
        "add",
        "--source",
        "tool",
        "--severity",
        sev,
        "--category",
        cat,
        "--message",
        msg,
        "--context-json",
        json.dumps(ctx, default=str),
        "--nexus-version",
        nexus_version,
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(REPO_ROOT),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "error": f"feedback add failed: {exc}", "id": None}

    if proc.returncode != 0:
        return {
            "ok": False,
            "error": (proc.stderr or proc.stdout or "feedback add nonzero exit").strip(),
            "id": None,
        }
    try:
        out = json.loads(proc.stdout.strip())
        return {"ok": True, "id": out.get("id"), "captured_at": out.get("captured_at")}
    except (json.JSONDecodeError, ValueError):
        return {"ok": True, "id": None}


@mcp.tool()
async def nexus_submit_feedback_tool(
    severity: str,
    category: str,
    message: str,
    context_json: str | None = None,
) -> dict[str, Any]:
    """Report Nexus friction (DEC-019 self-feedback).

    Call this when Nexus itself blocks, confuses, or stalls you — a gate DENY, a
    NEEDS-DECISION/REVISE you had to emit, a wrong-fit persona/skill, a roster
    mismatch, or missing context. No permission needed — Plexus harvests these to
    improve Nexus.

    severity: critical | high | medium | low | info
    category: gate_deny | gate_needs_decision | gate_revise_stall | unclear_persona
              | unclear_skill | missing_context | roster_mismatch | workflow_friction | other
    message:  one-line description of what blocked/confused/stalled you.
    context_json: optional JSON object with extra attribution.
    """
    return await nexus_submit_feedback(
        severity=severity,
        category=category,
        message=message,
        context_json=context_json,
    )


if __name__ == "__main__":
    mcp.run()
