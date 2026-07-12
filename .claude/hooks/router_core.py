"""
router_core.py — importable library for the single-stage router (Phase E1).

Provides:
  build_persona_enum(agents_dir)         — dynamic list from .claude/agents/*.md
  build_schema(personas)                 — JSON schema with dynamic enum
  call_router_model(user_msg, agents_dir) — LM Studio HTTP call; returns dict or None.
                                     On success the dict carries the parsed
                                     classification PLUS the exact model input
                                     (messages/model/system_prompt_sha256) so the
                                     capture log records a reproducible input.

Default model: granite-4.1-3b-instruct (override via _HOOK_ROUTER_MODEL env var).
"""

# .claude/hooks/*.py execute under the SYSTEM python3 (3.9.6 here), NOT uv/3.12.
# PEP-563 lazy annotations keep PEP-604 'X | None' unions from being evaluated at
# def-time (3.10+), so this module imports clean under 3.9.
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import socket
import subprocess
import sys
import urllib.error
from pathlib import Path
from typing import Any

# _HOOK_ROUTER_URL is the canonical env; _HOOK_QWEN_URL is a deprecated back-compat
# fallback so any external config that still sets the old name keeps working.
ROUTER_URL = os.environ.get("_HOOK_ROUTER_URL") or os.environ.get(
    "_HOOK_QWEN_URL", "http://127.0.0.1:1234/v1/chat/completions"
)
# Configurable via _HOOK_ROUTER_MODEL env var — update when switching models
ROUTER_MODEL = os.environ.get("_HOOK_ROUTER_MODEL", "granite-4.1-3b")
ROUTER_TIMEOUT = float(os.environ.get("_HOOK_ROUTER_TIMEOUT", "10.0"))
ROUTER_MAX_TOKENS = 256
DIFFICULTIES = ["trivial", "simple", "standard", "complex"]

# ── OPT-002: the dispatchable-persona roster is single-sourced in
# broker.registry. These two sets MIRROR that source. They are re-listed here as
# module constants — rather than imported from the broker package — because
# router_core.py runs under the system Python hook environment (Python 3.9, no uv
# venv), where the nexus-broker package is NOT on sys.path; a hard import would
# break the UserPromptSubmit hook at runtime. Drift is instead caught in CI by the
# agreement test (tests/test_router_persona_roster.py), which imports BOTH the
# broker registry AND this module and asserts they are identical — exactly the
# no-import + CI-agreement pattern the broker↔alias-resolver agreement
# (test_base_name_retirement.py) already uses.
#
# RETIRED_BASE_PERSONAS  ⇔  broker.registry.RETIRED_BASE_PERSONAS
# CLASSIFIER_PERSONAS    ⇔  broker.registry.CLASSIFIER_PERSONAS
#   = the personas the router classifier may emit. DELIBERATELY EXCLUDES
#     orchestrator-only mechanism personas like `lens-fast` (dispatched as the
#     fixed parallel sibling of `lens` after an implementer NEXUS:DONE, never
#     selected from a user prompt). build_persona_enum renders exactly this set
#     plus the synthetic 'meta' no-dispatch route.
#
#   R2-T03 FIX-4 supersedes the prior OPT-062 note here: the four `-pro` names
#   are no longer classifier-emittable — each base/pro pair merged into one
#   tier-parameterized source, so escalation is now a `tier=pro` parameter on
#   the merged persona, not a distinct dispatchable name. See
#   broker.registry.RETIRED_PRO_PERSONAS.
RETIRED_BASE_PERSONAS: frozenset[str] = frozenset({"forge", "pipeline", "quill"})
CLASSIFIER_PERSONAS: frozenset[str] = frozenset(
    {
        "atlas",
        "fable-planner",
        "forge-ui",
        "forge-wire",
        "hermes",
        "lens",
        "palette",
        "pipeline-async",
        "pipeline-data",
        "quill-py",
        "quill-ts",
        "scout",
    }
)


def _normalize_confidence(parsed: dict[str, Any]) -> dict[str, Any]:
    """Normalize confidence from 0-100 integer range to 0.0-1.0 float if needed."""
    conf = parsed.get("confidence")
    if isinstance(conf, (int, float)) and conf > 1.0:
        parsed["confidence"] = conf / 100.0
    return parsed


def _compute_logprob_margin(logprobs: Any) -> float | None:
    """Compute the top-1 vs top-2 probability margin from an OpenAI-style logprobs block.

    OPT-012: the model's VERBALIZED confidence is near-uncorrelated with accuracy on
    a small local model (a known-WRONG route scored the same 0.85 as a correct one).
    The logprob MARGIN — the gap between the most-likely and second-most-likely token
    at the decision point — is a far better calibrated signal. We compute it as a
    probability margin P(top1) − P(top2) in [0, 1] (higher = more decisive), the form
    the OPT-007 eval/fine-tune harness will consume.

    Parsing is DEFENSIVE by mandate: LM Studio / the local GGUF server may not emit
    logprobs at all, or may emit a shape we do not recognise. On any absence or
    malformation we return None (the caller falls back to verbalized confidence) — a
    missing margin must NEVER crash the routing hot path.

    Expected OpenAI-compatible shape (chat completions):
        logprobs = {"content": [
            {"token": "scout", "logprob": -0.1,
             "top_logprobs": [{"token": "scout", "logprob": -0.1},
                              {"token": "atlas", "logprob": -2.4}, ...]},
            ...]}
    We take the FIRST content token whose top_logprobs has >=2 entries (the first
    genuine decision point) and return P(top1) − P(top2).
    """
    import math

    if not isinstance(logprobs, dict):
        return None
    content = logprobs.get("content")
    if not isinstance(content, list):
        return None
    for entry in content:
        if not isinstance(entry, dict):
            continue
        top = entry.get("top_logprobs")
        if not isinstance(top, list) or len(top) < 2:
            continue
        try:
            ranked = sorted(
                (float(t["logprob"]) for t in top if isinstance(t, dict) and "logprob" in t),
                reverse=True,
            )
        except (TypeError, ValueError):
            continue
        if len(ranked) < 2:
            continue
        margin = math.exp(ranked[0]) - math.exp(ranked[1])
        # Clamp to [0, 1] — exp of a logprob is a probability, the gap cannot
        # legitimately exceed 1; clamp guards against malformed/oversized values.
        return max(0.0, min(1.0, margin))
    return None


# Leading boilerplate clauses that carry ZERO routing signal — present verbatim
# at the head of most agent descriptions. They must be skipped when choosing the
# sentence the router sees, otherwise the classifier disambiguates the hardest,
# highest-value personas (forge-ui/forge-wire/pipeline-*/quill-*) on noise.
# Matched case-insensitively, ignoring a leading "(".
_BOILERPLATE_CLAUSE_PREFIXES: tuple[str, ...] = (
    "nexus-dispatched only",
    "not for direct user invocation",
    "spawned by nexus orchestrator",
    "spawned when difficulty",
)


def _persona_blurb(desc: str) -> str:
    """Reduce a raw agent description to a single meaningful routing sentence.

    The bug (OPT-001): every implementer description LEADS with the
    'Nexus-dispatched only — …' boilerplate, so the old `desc[:desc.index(marker)]`
    slice produced '' and those personas rendered as bare names. The fix selects
    the first sentence that is NOT pure boilerplate — for the boilerplate-leading
    personas that is the ownership-boundary sentence ('Owns …' / 'Authors …'),
    which is exactly the signal the classifier needs.

    The prior code path (a meaningful clause BEFORE '(Nexus-dispatched only)', as
    in atlas/hermes/scout/lens) still wins because that clause is the first
    non-boilerplate sentence encountered.
    """
    # Split on sentence boundaries; '. ' is the reliable separator in these
    # single-line frontmatter descriptions.
    sentences = [s.strip(" —-()") for s in desc.replace("\n", " ").split(". ")]
    for sentence in sentences:
        if not sentence:
            continue
        lowered = sentence.lower()
        if any(lowered.startswith(prefix) for prefix in _BOILERPLATE_CLAUSE_PREFIXES):
            continue
        # Drop a parenthetical '(Nexus-dispatched only …)' tail if it survived
        # inside the chosen sentence (boilerplate-mid personas).
        for marker in ("(Nexus-dispatched only", "Nexus-dispatched only"):
            cut = sentence.find(marker)
            if cut != -1:
                sentence = sentence[:cut].strip(" —-(")
                break
        if sentence:
            return sentence
    # Every sentence was boilerplate — fall back to the longest sentence so the
    # persona is never rendered as a bare name (the failure OPT-008 guards).
    return max((s for s in sentences if s), key=len, default="")


def _read_persona_descriptions(agents_dir: str) -> str:
    """
    Return a formatted block of persona descriptions read from agent frontmatter.

    - Renders exactly the CLASSIFIER_PERSONAS roster (OPT-002 single source) — so
      the rendered block and build_persona_enum stay in lockstep. This now
      INCLUDES the four `-pro` escalation variants (the OPT-062 fix: they are
      classifier-emittable, so the model must see their descriptions) and excludes
      orchestrator-only / retired / template / internal files by virtue of their
      absence from the roster.
    - Injects a hardcoded 'meta' entry first.
    - Reduces each description to its first meaningful sentence via _persona_blurb
      (so boilerplate-leading personas keep their ownership-boundary signal) and
      caps each blurb at 120 chars on a word boundary.
    """
    lines: list[str] = [
        "- meta: questions, status checks, clarifications, ops decisions where Nexus should answer directly — NOT a dispatch",
    ]

    agents_path = Path(agents_dir)
    for p in sorted(agents_path.glob("*.md")):
        stem = p.stem
        if stem not in CLASSIFIER_PERSONAS:
            continue

        desc = ""
        try:
            text = p.read_text(encoding="utf-8")
            # Parse YAML frontmatter for description field
            if text.startswith("---"):
                end = text.find("---", 3)
                if end != -1:
                    frontmatter = text[3:end]
                    for line in frontmatter.splitlines():
                        if line.startswith("description:"):
                            raw = line[len("description:"):].strip().strip('"').strip("'")
                            desc = raw
                            break
        except OSError:
            pass

        blurb = _persona_blurb(desc)

        # Cap at 120 chars, cutting on a word boundary so the blurb never ends
        # mid-clause (the second OPT-001 defect: the old 100-char cap chopped
        # surviving descriptions mid-word).
        if len(blurb) > 120:
            blurb = blurb[:120].rsplit(" ", 1)[0] + "…"

        lines.append(f"- {stem}: {blurb}" if blurb else f"- {stem}")

    return "\n".join(lines)


def _read_skill_names(skills_dir: str) -> list[str]:
    """Return sorted list of skill names from subdirectory names in skills_dir."""
    skills_path = Path(skills_dir)
    if not skills_path.exists():
        return []
    return sorted(
        p.name for p in skills_path.iterdir() if p.is_dir() and not p.name.startswith("_")
    )


def _build_system_prompt(agents_dir: str, skills_dir: str) -> str:
    """Build routing system prompt dynamically from project's agent/skill files."""
    personas_block = _read_persona_descriptions(agents_dir)
    skills_list = _read_skill_names(skills_dir)

    # Group skills by persona prefix for better model attention
    skill_groups: dict[str, list[str]] = {}
    ungrouped: list[str] = []
    for skill in skills_list:
        matched = False
        for skill_prefix in [
            "forge-ui",
            "forge-wire",
            "pipeline-data",
            "pipeline-async",
            "quill-ts",
            "quill-py",
            "atlas",
            "hermes",
            "lens",
            "palette",
            "scout",
            "meta",
        ]:
            if skill.startswith(skill_prefix):
                skill_groups.setdefault(skill_prefix, []).append(skill)
                matched = True
                break
        if not matched:
            ungrouped.append(skill)

    # Build skills section: flat list grouped by prefix
    if skill_groups or ungrouped:
        skills_lines = ["REQUIRED_SKILLS (pick from the persona's group, zero-to-many):"]
        for prefix, skills in sorted(skill_groups.items()):
            skills_lines.append(f"  {prefix}: {', '.join(skills)}")
        if ungrouped:
            skills_lines.append(f"  (shared): {', '.join(ungrouped)}")
        skills_section = "\n".join(skills_lines)
    else:
        skills_section = "REQUIRED_SKILLS: [] (no skills configured)"

    # Few-shot examples — project-agnostic, calibrate confidence to fractional values
    examples = """\
EXAMPLES (calibrate from these):
{"persona":"meta","difficulty":"trivial","confidence":0.95,"required_skills":[],"tdd_required":false}  // "what's next?" / "why did X happen?" / "should I do A or B?"
{"persona":"scout","difficulty":"simple","confidence":0.88,"required_skills":["codebase-exploration"],"tdd_required":false}  // "this build is failing — investigate"
{"persona":"lens","difficulty":"trivial","confidence":0.90,"required_skills":["verification-protocols"],"tdd_required":false}  // "validate the last change"
{"persona":"meta","difficulty":"complex","confidence":0.90,"required_skills":[],"tdd_required":false}  // "ship all open tasks" / "what's the status?"
{"persona":"hermes","difficulty":"simple","confidence":0.87,"required_skills":[],"tdd_required":false}  // "update .claude/settings.json hook wiring" / config-only change
{"persona":"meta","difficulty":"trivial","confidence":0.92,"required_skills":[],"tdd_required":false}  // "what is the state of X?" / "is X enabled?" — informational, no code written"""  # noqa: E501

    return f"""\
You are a routing classifier for the Nexus orchestrator. Given a user request, emit ONE JSON object.

PERSONAS (pick exactly one):
{personas_block}

DIFFICULTY:
- trivial: ≤1 file, ≤5 LOC, no logic change
- simple: ≤2 files, no design decision
- standard: 3-10 files, single domain
- complex: cross-domain, multi-persona, planning required

{skills_section}

TDD_REQUIRED: true ONLY if production source code will be written; config files, hook wiring, docs, and informational answers are false.
CONFIDENCE: fractional 0.0-1.0. Use 0.95 when obvious, 0.70-0.85 when uncertain. Never output an integer.

{examples}"""


def build_persona_enum(agents_dir: str) -> list[str]:
    """Return the sorted persona enum the router classifier may emit, + 'meta'.

    OPT-002 single-source: the authoritative roster is CLASSIFIER_PERSONAS (the
    mirror of broker.registry.CLASSIFIER_PERSONAS). The enum is the intersection
    of that roster with the agent files actually present on disk — so a persona is
    emitted iff it is BOTH a canonical classifier target AND has a renderable
    agent file. This:
      - INCLUDES the four `-pro` escalation variants (they are in
        CLASSIFIER_PERSONAS and have agent files) — fixing the OPT-062 defect
        where the old `endswith("-pro")` filter made the classifier structurally
        unable to escalate;
      - EXCLUDES orchestrator-only mechanism personas (e.g. `lens-fast`, absent
        from CLASSIFIER_PERSONAS), retired base names (forge/pipeline/quill — not
        in CLASSIFIER_PERSONAS), the orchestrator self-handle, _-prefixed internal
        files, and DOMAIN-AGENT-TEMPLATE — all by virtue of not being in the
        roster.

    'meta' (the synthetic no-dispatch route) is appended last.
    """
    on_disk = {
        p.stem
        for p in Path(agents_dir).glob("*.md")
        if not p.stem.startswith("_") and p.stem != "DOMAIN-AGENT-TEMPLATE"
    }
    personas = sorted(CLASSIFIER_PERSONAS & on_disk)
    personas.append("meta")
    return personas


def build_schema(personas: list[str]) -> dict[str, Any]:
    """Return a JSON schema with dynamic persona enum for structured output."""
    return {
        "type": "object",
        "required": ["persona", "difficulty", "confidence", "required_skills", "tdd_required"],
        "properties": {
            "persona": {"type": "string", "enum": personas},
            "difficulty": {"type": "string", "enum": DIFFICULTIES},
            "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "required_skills": {"type": "array", "items": {"type": "string"}},
            "tdd_required": {"type": "boolean"},
        },
        "additionalProperties": False,
    }


# Benign LM-Studio-down failures: the backend is simply not listening / slow.
# These are the expected steady-state when no model server is running, so the
# router falls through SILENTLY (no stderr noise). Anything else (bad payload,
# KeyError on an unexpected response shape, JSON corruption) is UNEXPECTED and
# must be surfaced so a real bug in the router path is not swallowed.
_BENIGN_CALL_ERRORS = (
    ConnectionError,  # incl. ConnectionRefusedError when the server is down
    TimeoutError,  # incl. socket.timeout (alias of TimeoutError on 3.10+)
    socket.timeout,
    urllib.error.URLError,  # wraps refused/unreachable; reason inspected below
)


def _is_benign_call_error(exc: BaseException) -> bool:
    """True if `exc` is an expected 'LM Studio is down/slow' failure (silent fallthrough)."""
    if isinstance(exc, _BENIGN_CALL_ERRORS):
        # A URLError can also wrap a genuinely unexpected cause; only treat the
        # connection-class reasons as benign.
        if isinstance(exc, urllib.error.URLError) and not isinstance(
            exc, _BENIGN_CALL_ERRORS[:-1]
        ):
            reason = getattr(exc, "reason", None)
            return isinstance(reason, (ConnectionError, TimeoutError, socket.timeout, OSError))
        return True
    return False


def _resolve_dirs(
    agents_dir: str | None, skills_dir: str | None
) -> tuple[str, str]:
    """Resolve agents/skills dirs, defaulting to .claude/agents and .claude/skills."""
    resolved_agents_dir = agents_dir or str(Path(__file__).parent.parent / "agents")
    resolved_skills_dir = skills_dir or str(Path(__file__).parent.parent / "skills")
    return resolved_agents_dir, resolved_skills_dir


def _build_messages(user_msg: str, agents_dir: str, skills_dir: str) -> list[dict[str, str]]:
    """Build the exact 2-message system+user array sent to the model."""
    system_prompt = _build_system_prompt(agents_dir, skills_dir)
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_msg},
    ]


def _extract_token_usage(usage: Any) -> tuple[int | None, int | None]:
    """Best-effort extraction of (input_tokens, output_tokens) from an OpenAI-style
    `usage` block: {"prompt_tokens": N, "completion_tokens": N, "total_tokens": N}.

    AUDIT-5-router-cost: the router captures zero token/cost data today. This is
    purely additive telemetry — on any absence or malformed shape we return
    (None, None) rather than raising, so a missing/odd `usage` block can NEVER
    break routing (the caller falls back to null fields in the capture record).
    """
    if not isinstance(usage, dict):
        return None, None
    prompt_tokens = usage.get("prompt_tokens")
    completion_tokens = usage.get("completion_tokens")
    if not isinstance(prompt_tokens, int):
        prompt_tokens = None
    if not isinstance(completion_tokens, int):
        completion_tokens = None
    return prompt_tokens, completion_tokens


def _wrap_result(
    parsed: dict[str, Any],
    messages: list[dict[str, str]],
    confidence_margin: float | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
) -> dict[str, Any]:
    """
    Wrap a parsed classification with the exact model input so the capture log
    records a reproducible (input, output) pair. system_prompt_sha256 hashes the
    rendered system message verbatim — it pins the OPT-001-buggy prompt.

    confidence_margin (OPT-012) is the logprob-derived top-2 margin when the server
    returned logprobs, else None (verbalized-confidence fallback). It rides alongside
    the classification so router.py can record it AND prefer it where available.

    input_tokens/output_tokens (AUDIT-5-router-cost) are the OpenAI-style
    prompt_tokens/completion_tokens from the response `usage` block, or None when
    the server omitted usage — best-effort, never required.
    """
    system_content = next(
        (m["content"] for m in messages if m.get("role") == "system"), ""
    )
    return {
        "classification": _normalize_confidence(parsed),
        "messages": messages,
        "model": ROUTER_MODEL,
        "confidence_margin": confidence_margin,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "system_prompt_sha256": hashlib.sha256(
            system_content.encode("utf-8")
        ).hexdigest(),
    }


def call_router_model(
    user_msg: str,
    agents_dir: str | None = None,
    skills_dir: str | None = None,
) -> dict[str, Any] | None:
    """
    Call the router model via LM Studio. Returns a dict on success, None on any failure.

    The returned dict carries the parsed classification PLUS the exact model input:
      {"classification": parsed, "messages": [...], "model": ROUTER_MODEL,
       "system_prompt_sha256": <sha256 of rendered system prompt>}
    Callers wanting only the classification read result["classification"].

    Respects test env vars:
      _MOCK_ROUTER_RESPONSE       — JSON string to return instead of real HTTP call.
                                    Either a bare classification object, OR an
                                    OpenAI-style envelope {"classification": {...},
                                    "logprobs": {...}, "usage": {...}} to exercise
                                    the OPT-012 margin parse + graceful fallback and
                                    the AUDIT-5-router-cost token capture.
      _MOCK_ROUTER_CONNECT_ERROR  — if set, simulate connection failure
    """
    # Test mock: simulate connection error
    if os.environ.get("_MOCK_ROUTER_CONNECT_ERROR"):
        return None

    resolved_agents_dir, resolved_skills_dir = _resolve_dirs(agents_dir, skills_dir)

    # Test mock: return pre-baked classification, wrapped in the real input shape
    # so the capture log still records the exact system+user messages the model
    # would have seen. A mock may optionally carry a sibling "logprobs" block (the
    # OpenAI-compatible shape) so the OPT-012 margin computation + its graceful
    # fallback are exercisable without a live server, and/or a sibling "usage"
    # block (AUDIT-5-router-cost) to exercise token capture + its graceful fallback.
    mock_resp = os.environ.get("_MOCK_ROUTER_RESPONSE")
    if mock_resp is not None:
        try:
            raw = json.loads(mock_resp)
        except json.JSONDecodeError:
            return None
        if isinstance(raw, dict) and "classification" in raw:
            parsed = raw["classification"]
            margin = _compute_logprob_margin(raw.get("logprobs"))
            input_tokens, output_tokens = _extract_token_usage(raw.get("usage"))
        else:
            parsed = raw
            margin = None
            input_tokens = None
            output_tokens = None
        messages = _build_messages(user_msg, resolved_agents_dir, resolved_skills_dir)
        return _wrap_result(
            parsed,
            messages,
            confidence_margin=margin,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    # Real HTTP call
    try:
        import urllib.request

        personas = build_persona_enum(resolved_agents_dir)
        schema = build_schema(personas)
        messages = _build_messages(user_msg, resolved_agents_dir, resolved_skills_dir)

        payload = json.dumps({
            "model": ROUTER_MODEL,
            "temperature": 0.0,
            "max_tokens": ROUTER_MAX_TOKENS,
            "seed": 42,
            "messages": messages,
            # OPT-012: ask the OpenAI-compatible endpoint for per-token logprobs so
            # we can compute the top-2 margin (a far better-calibrated confidence
            # signal than the model's verbalized number on a small local model).
            # LM Studio may ignore these — the response parse is defensive.
            "logprobs": True,
            "top_logprobs": 5,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "routing_classification",
                    "strict": True,
                    "schema": schema,
                },
            },
        }).encode()

        req = urllib.request.Request(
            ROUTER_URL,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        socket.setdefaulttimeout(ROUTER_TIMEOUT)
        with urllib.request.urlopen(req, timeout=ROUTER_TIMEOUT) as resp:
            body = json.loads(resp.read())

        choice = body["choices"][0]
        content = choice["message"]["content"]
        # OPT-012: parse the logprob margin if the server returned one; on absence
        # or any malformation _compute_logprob_margin returns None and we fall back
        # to the verbalized confidence downstream.
        margin = _compute_logprob_margin(choice.get("logprobs"))
        # AUDIT-5-router-cost: OpenAI-compatible servers report token usage at the
        # top level of the response body as "usage". Best-effort — absence or a
        # malformed block yields (None, None) via _extract_token_usage, never a raise.
        input_tokens, output_tokens = _extract_token_usage(body.get("usage"))
        return _wrap_result(
            json.loads(content),
            messages,
            confidence_margin=margin,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    except Exception as exc:
        # Benign (LM Studio down/slow) → silent fallthrough. Unexpected (bad
        # response shape, JSON corruption, programming error) → one stderr line
        # so a real router-path bug is not swallowed. Either way return None so
        # the caller falls through to Nexus's own routing.
        if not _is_benign_call_error(exc):
            print(f"[router] degraded: {type(exc).__name__}", file=sys.stderr)
        return None


# ============================================================================
# LANE_ROUTER (R4-T05, plan-13 N08) -- classify-and-act: conductor vs harness
# vs inline, folding NATIVE-13-4 (classifier confidence threshold + fallback-
# to-harness + misroute monitor writing telemetry) and NATIVE-33 (the
# out-of-family MoE seat -- the codex arm -- routed EXCLUSIVELY through the
# node-contract `executor` field: see apply_executor() below. LANES has
# exactly three members; "codex" is never a fourth one -- it is never a lane
# at all, only a node["executor"] value the conductor lane's dispatch switch
# (broker.conductor.dag.dispatch_node) already reads.
# ============================================================================

LANE_ROUTER_VERSION = "1.0"

# The three execution lanes a ready task shape classifies into.
LANE_CONDUCTOR = "conductor"
LANE_HARNESS = "harness"
LANE_INLINE = "inline"
LANES = (LANE_CONDUCTOR, LANE_HARNESS, LANE_INLINE)

# NATIVE-13-4: below this confidence the classifier's own pick is untrusted --
# route to the harness lane (never conductor/inline sight-unseen) and record
# the fallback as a misroute so the pattern is visible in aggregate telemetry,
# not just per-call. Overridable for calibration without a code change.
LANE_CONFIDENCE_THRESHOLD = float(os.environ.get("_HOOK_LANE_CONFIDENCE_THRESHOLD", "0.6"))

# .memory/log.py lives at the repo root; router_core.py lives at
# <root>/.claude/hooks/router_core.py -- three parents up reaches <root> in
# BOTH the live tree and the package twin (each ships its own .memory/log.py),
# so this constant needs no environment-specific override.
_LANE_ROUTER_LOG_PY = Path(__file__).parent.parent.parent / ".memory" / "log.py"


def _score_lane(shape: dict[str, Any]) -> tuple[str, float]:
    """Deterministic (lane, confidence) scoring from a task-shape descriptor.

    `shape` fields (all optional; every read is a defensive .get):
      dag_size               -- int, estimated node count for the task
      distinct_write_scopes  -- int, distinct write_scope surfaces touched
      parallelizable         -- bool, >=2 independent legs, no ordering edge
      indivisible             -- bool, a single leg that cannot be decomposed

    Mirrors the R4-T03 conductor's own scope (a DAG over a ready-set,
    work-stealing across >=2 workers) for the conductor lane, and the
    Article XIII.d ladder (>=2 independent -> Workflow, else a lone Agent)
    already codified in Skill parallel-first-check for harness vs inline.
    """
    dag_size = shape.get("dag_size") or 0
    write_scopes = shape.get("distinct_write_scopes") or 0
    parallelizable = bool(shape.get("parallelizable"))
    indivisible = bool(shape.get("indivisible"))

    if indivisible and dag_size <= 1 and not parallelizable:
        return (LANE_INLINE, 0.95)
    if dag_size >= 8 or write_scopes >= 3:
        return (LANE_CONDUCTOR, 0.9 if parallelizable else 0.65)
    if parallelizable or dag_size >= 2:
        return (LANE_HARNESS, 0.85)
    return (LANE_INLINE, 0.75)


def _write_misroute_telemetry(
    shape: dict[str, Any],
    classifier_pick: str,
    confidence: float,
    threshold: float,
    log_py_path: str | None = None,
    run: Any = None,
) -> None:
    """NATIVE-13-4 misroute monitor. Writes ONE dispatch_telemetry row via the
    existing `.memory/log.py dispatch record` CLI path -- the same mechanism
    `broker.conductor.dag.record_dispatch_telemetry` already uses for pool
    workers -- so a fallback is a real, queryable row, never an in-memory-only
    counter. Best-effort: a telemetry failure must NEVER break routing (same
    discipline as call_router_model's own defensive HTTP call above).
    """
    run = run or subprocess.run
    log_py = Path(log_py_path) if log_py_path else _LANE_ROUTER_LOG_PY
    marker = (
        f"misroute classifier_pick={classifier_pick} confidence={confidence:.3f} "
        f"threshold={threshold:.3f} fallback_lane={LANE_HARNESS}"
    )
    cmd = [
        sys.executable, str(log_py), "dispatch", "record",
        "--persona", "lane-router",
        "--marker", marker,
        "--tokens", "0", "--token-source", "approx",
        "--duration-ms", "0",
    ]
    # Telemetry is observability, not a routing dependency -- a failure here
    # must NEVER break routing (same discipline as call_router_model above).
    with contextlib.suppress(Exception):
        run(cmd, capture_output=True, timeout=10)


def classify_lane(
    shape: dict[str, Any],
    confidence_threshold: float | None = None,
    log_py_path: str | None = None,
    run: Any = None,
) -> dict[str, Any]:
    """NATIVE-13-4 classify-and-act: score `shape` into a lane, and when the
    classifier's own confidence falls below `confidence_threshold` (default
    LANE_CONFIDENCE_THRESHOLD), override the pick to LANE_HARNESS and write a
    misroute telemetry row -- the classifier's original pick is preserved in
    the return value (`classifier_pick`) so the override stays auditable.

    Returns {"lane", "confidence", "classifier_pick", "fallback"}.
    """
    threshold = LANE_CONFIDENCE_THRESHOLD if confidence_threshold is None else confidence_threshold
    picked_lane, confidence = _score_lane(shape)
    if confidence < threshold:
        if picked_lane != LANE_HARNESS:
            _write_misroute_telemetry(
                shape, picked_lane, confidence, threshold, log_py_path=log_py_path, run=run,
            )
        return {
            "lane": LANE_HARNESS, "confidence": confidence,
            "classifier_pick": picked_lane, "fallback": True,
        }
    return {
        "lane": picked_lane, "confidence": confidence,
        "classifier_pick": picked_lane, "fallback": False,
    }


def apply_executor(
    node: dict[str, Any], out_of_family: bool = False, executor_model: str | None = None,
) -> dict[str, Any]:
    """NATIVE-33: the ONLY entry point through which the out-of-family MoE
    seat (Codex) reaches a node -- setting node["executor"]. There is no
    "codex" member of LANES and this module never dispatches to Codex
    directly; a conductor-lane node MAY carry executor="codex" and
    broker.conductor.dag.dispatch_node (plans/11-codex-lane-design.md SS9.4)
    switches on exactly that field -- one source of truth, never a parallel
    routing mechanism.
    """
    result = dict(node)
    result["executor"] = "codex" if out_of_family else node.get("executor", "claude")
    if executor_model:
        result["executor_model"] = executor_model
    return result


# plans/12-dual-plan-deep-planning.md SS16.3 -- the dual-plan trigger table,
# T1-T5, is this router's classify rule for the PLANNING lane (SS16.5.4).
# Default posture (SS16.3): dual-plan on T1/T3/T5 always; T2/T4 at
# orchestrator discretion; everything else stays single-planner Opus.
PLANNING_TRIGGER_ALWAYS = ("T1", "T3", "T5")
PLANNING_TRIGGER_DISCRETIONARY = ("T2", "T4")


def classify_planning_lane(
    signals: dict[str, Any], orchestrator_discretion: bool = False,
) -> dict[str, Any]:
    """plans/12 SS16.3 T1-T5, evaluated deterministically from signals the
    orchestrator already has at intake triage:
      user_requested_deep_plan  -- bool                       (T1)
      any_leg_risk_tier_t2      -- bool                       (T2)
      any_leg_irreversible      -- bool                       (T3)
      estimated_dag_size        -- int                        (T4, part a)
      distinct_write_scopes     -- int                        (T4, part b)
      prior_plan_failed_gate    -- bool                       (T5)

    Returns {"dual_plan", "triggers", "always_triggers",
    "discretionary_triggers"}. T1/T3/T5 force dual-plan; T2/T4 alone only
    fire it when `orchestrator_discretion` is True (SS16.3's "at orchestrator
    discretion" clause) -- everything else is pure single-planner Opus.
    """
    triggers = []
    if signals.get("user_requested_deep_plan"):
        triggers.append("T1")
    if signals.get("any_leg_risk_tier_t2"):
        triggers.append("T2")
    if signals.get("any_leg_irreversible"):
        triggers.append("T3")
    dag_size = signals.get("estimated_dag_size") or 0
    write_scopes = signals.get("distinct_write_scopes") or 0
    if dag_size >= 8 or write_scopes >= 3:
        triggers.append("T4")
    if signals.get("prior_plan_failed_gate"):
        triggers.append("T5")

    always_triggers = [t for t in triggers if t in PLANNING_TRIGGER_ALWAYS]
    discretionary_triggers = [t for t in triggers if t in PLANNING_TRIGGER_DISCRETIONARY]
    dual_plan = bool(always_triggers) or (bool(discretionary_triggers) and orchestrator_discretion)
    return {
        "dual_plan": dual_plan,
        "triggers": triggers,
        "always_triggers": always_triggers,
        "discretionary_triggers": discretionary_triggers,
    }


# ============================================================================
# GATE_RUNNER INTEGRATION (R6-T06 / N35, plans/14 SS4): the pretooluse-
# dispatch wiring. gate_runner.py calls route_dispatch() for every Task/
# TeamCreate/Agent payload ONLY when .claude/lane-router.enabled exists (the
# flag check lives in gate_runner.py's run_event(), not here, so this module
# stays independently unit-testable without the flag file present).
# route_dispatch() derives a `shape` dict from the raw payload, classifies it
# via classify_lane() (defined above -- folding in the NATIVE-13-4
# confidence-threshold fallback + misroute monitor), and journals EVERY
# decision -- not just misroutes, which classify_lane's own
# _write_misroute_telemetry already covers as a SEPARATE dispatch_telemetry
# row -- to .memory/files/lane_router_decisions.jsonl (the SS5 registry
# probe's recency+well-formedness signal,
# nexus-redesign/activation/probes/lane_router.py).
# ============================================================================

LANE_ROUTER_DECISIONS_LOG = (
    Path(__file__).parent.parent.parent / ".memory" / "files" / "lane_router_decisions.jsonl"
)

# The ONE shape class plans/14 SS4 measured a conductor-lane result for
# (validation_log id 49, 50.08% wall-clock) -- see _is_verify_matrix_shape.
SHAPE_CLASS_VERIFY_MATRIX = "verify-matrix"
# R5-T14 (N72) rung-1 promotion: batch-audit is the NEXT tenant class walked
# up the RDEC-016 clause c ladder after verify-matrix -- headless,
# non-interactive audit fan-out (see _is_batch_audit_shape). Fixture-
# generation is the next candidate rung after this one (deferred this
# release); interactive/high-touch shapes are never promoted.
SHAPE_CLASS_BATCH_AUDIT = "batch-audit"
SHAPE_CLASS_OTHER = "other"

# The shape-class table classify_lane's shape derivation consults: every
# class in this set gets the conductor-calibrated constants in
# derive_dispatch_shape (dag_size/write_scopes tuned to trip the conductor
# threshold in _score_lane); everything else -- including fixture-generation
# (deferred) and interactive/high-touch shapes -- gets the harness-default
# constants. R5-T14 walks this table ONE rung at a time, each gated on the
# ladder's evidence bar (N routed runs, zero misroute-fallbacks, zero
# governance-parity deltas) before the next class is added here.
CONDUCTOR_DEFAULT_SHAPE_CLASSES: frozenset[str] = frozenset(
    {SHAPE_CLASS_VERIFY_MATRIX, SHAPE_CLASS_BATCH_AUDIT}
)


def _dispatch_brief(tool_input: dict[str, Any]) -> dict[str, Any]:
    """Best-effort brief extraction, mirroring broker-gate.py's own
    _extract_brief (Single-Home note: duplicated by necessity -- this module
    runs under the system-python hook environment with no cross-hook import,
    same reasoning as the CLASSIFIER_PERSONAS mirror at the top of this
    file). A brief may arrive as a fenced ```json block inside
    description/prompt, as a bare JSON string in either field, or not at all
    (returns {}).
    """
    import re

    for field in ("description", "prompt", "input"):
        raw = tool_input.get(field, "")
        if not isinstance(raw, str) or not raw.strip():
            continue
        for blockmatch in re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL):
            with contextlib.suppress(json.JSONDecodeError):
                return json.loads(blockmatch)
        with contextlib.suppress(json.JSONDecodeError):
            return json.loads(raw)
    return {}


def _is_verify_matrix_shape(tool_input: dict[str, Any], brief: dict[str, Any]) -> bool:
    """True iff this dispatch matches the ONE conductor-lane shape class
    plans/14 SS4 measured a result for -- the verify-matrix tenant. Signal:
    an explicit `tenant`/`conductor_tenant` field naming it (the N34
    conductor CLI's own tenant name), OR work_type 'verify' (matches the
    N37/N40 node-contract work_type) combined with a 'verify-matrix' mention
    in the goal/description/prompt -- deliberately narrow (never a fuzzy
    heuristic) so a random verify-tier dispatch never silently rides into the
    one lane class that was actually measured.
    """
    tenant = str(
        tool_input.get("tenant") or tool_input.get("conductor_tenant")
        or brief.get("tenant") or ""
    ).strip().lower()
    if tenant == SHAPE_CLASS_VERIFY_MATRIX:
        return True
    work_type = str(brief.get("work_type") or tool_input.get("work_type") or "").strip().lower()
    goal_text = " ".join(
        str(tool_input.get(k) or brief.get(k) or "") for k in ("description", "prompt", "goal")
    ).lower()
    return work_type == "verify" and SHAPE_CLASS_VERIFY_MATRIX in goal_text


def _is_batch_audit_shape(tool_input: dict[str, Any], brief: dict[str, Any]) -> bool:
    """True iff this dispatch matches the BATCH-AUDIT shape class -- the
    R5-T14 (N72) rung-1 conductor-lane promotion candidate: headless,
    non-interactive audit/review work fanned out over a batch (RDEC-016
    clause c's priority-ordered ladder: batch-audit first, fixture-
    generation next, interactive/high-touch never). Signal: an explicit
    `tenant`/`conductor_tenant` field naming it (mirrors
    _is_verify_matrix_shape's own convention), OR work_type 'audit'/'recon'
    combined with a 'batch-audit' mention in the goal/description/prompt --
    deliberately narrow (never a fuzzy heuristic) so a random audit-tier
    dispatch never silently rides into the promoted lane.
    """
    tenant = str(
        tool_input.get("tenant") or tool_input.get("conductor_tenant")
        or brief.get("tenant") or ""
    ).strip().lower()
    if tenant == SHAPE_CLASS_BATCH_AUDIT:
        return True
    work_type = str(brief.get("work_type") or tool_input.get("work_type") or "").strip().lower()
    goal_text = " ".join(
        str(tool_input.get(k) or brief.get(k) or "") for k in ("description", "prompt", "goal")
    ).lower()
    return work_type in ("audit", "recon") and SHAPE_CLASS_BATCH_AUDIT in goal_text


def derive_dispatch_shape(payload: dict[str, Any]) -> dict[str, Any]:
    """Derive a classify_lane() `shape` dict (plus a `shape_class` audit
    label) from a raw PreToolUse Task/TeamCreate/Agent payload. Mirrors
    broker-gate.py's own tool_input-nesting tolerance (tool_input/input/flat
    -- see its _dispatch_facts docstring for why a nested dict wins over
    top-level fields).

    Three shape classes exist today (plans/14 SS4's hybrid decision,
    extended by R5-T14/N72's promotion ladder): verify-matrix and
    batch-audit both get the conductor default via CONDUCTOR_DEFAULT_SHAPE_
    CLASSES; everything else (including the deferred fixture-generation
    candidate and interactive/high-touch shapes) defaults to harness --
    dag_size/distinct_write_scopes/parallelizable here are calibrated
    CONSTANTS per class, not per-call estimates, because a single
    PreToolUse Task/Agent call structurally cannot see the caller's whole DAG
    (that information lives with the conductor/Workflow orchestrating the
    call, not the individual dispatch this hook observes).
    """
    nested: dict[str, Any] | None = None
    for key in ("tool_input", "input"):
        candidate = payload.get(key)
        if isinstance(candidate, dict):
            nested = candidate
            break
    tool_input: dict[str, Any] = nested if nested is not None else payload
    brief = _dispatch_brief(tool_input)

    if _is_verify_matrix_shape(tool_input, brief):
        shape_class = SHAPE_CLASS_VERIFY_MATRIX
    elif _is_batch_audit_shape(tool_input, brief):
        shape_class = SHAPE_CLASS_BATCH_AUDIT
    else:
        shape_class = SHAPE_CLASS_OTHER

    if shape_class in CONDUCTOR_DEFAULT_SHAPE_CLASSES:
        return {
            "dag_size": 8, "distinct_write_scopes": 3,
            "parallelizable": True, "indivisible": False,
            "shape_class": shape_class,
        }
    return {
        "dag_size": 2, "distinct_write_scopes": 1,
        "parallelizable": True, "indivisible": False,
        "shape_class": shape_class,
    }


def _write_routing_journal(
    decision: dict[str, Any],
    shape_class: str,
    persona: str,
    journal_path: str | None = None,
) -> None:
    """Append ONE JSONL line per routing decision to
    .memory/files/lane_router_decisions.jsonl -- the SS5 registry probe's
    recency+well-formedness signal. Every decision is journaled (not just
    misroutes -- classify_lane's own _write_misroute_telemetry is the
    separate dispatch_telemetry row for that case, unaffected by this
    write). Best-effort: a journal-write failure must NEVER break routing
    (same discipline as _write_misroute_telemetry above).
    """
    import time

    path = Path(journal_path) if journal_path else LANE_ROUTER_DECISIONS_LOG
    line = {
        "ts": time.time(),
        "persona": persona,
        "shape_class": shape_class,
        "lane": decision.get("lane"),
        "classifier_pick": decision.get("classifier_pick"),
        "confidence": decision.get("confidence"),
        "fallback": decision.get("fallback"),
        "router_version": LANE_ROUTER_VERSION,
    }
    with contextlib.suppress(Exception):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(line) + "\n")


def route_dispatch(
    payload: dict[str, Any],
    journal_path: str | None = None,
    log_py_path: str | None = None,
    confidence_threshold: float | None = None,
    run: Any = None,
) -> dict[str, Any]:
    """gate_runner.py's pretooluse-dispatch entry point (R6-T06 / N35):
    derive the task shape from `payload`, classify_lane() it (folding in the
    NATIVE-13-4 confidence-threshold fallback + misroute monitor), journal
    the decision, and return it.

    ADVISORY ONLY: this never raises and gate_runner.py never denies on its
    result -- the actual conductor/harness dispatch mechanism (N34's
    `python -m broker.conductor run`, or the Workflow tool) is untouched by
    this call. Callers gate the WHOLE check behind
    .claude/lane-router.enabled -- this function itself performs no flag
    check, so it stays independently unit-testable.
    """
    try:
        shape = derive_dispatch_shape(payload)
        shape_class = shape.get("shape_class", SHAPE_CLASS_OTHER)
        decision = classify_lane(
            shape, confidence_threshold=confidence_threshold, log_py_path=log_py_path, run=run,
        )
        nested = payload.get("tool_input")
        nested = nested if isinstance(nested, dict) else payload
        persona = str(
            nested.get("subagent_type") or nested.get("agent_type") or ""
        ).strip().lower()
        _write_routing_journal(decision, shape_class, persona, journal_path=journal_path)
        decision = dict(decision)
        decision["shape_class"] = shape_class
        return decision
    except Exception:
        # Fail-open, silently -- routing is advisory; a crash here must never
        # surface as a gate error (mirrors call_router_model's own discipline).
        return {
            "lane": LANE_HARNESS, "confidence": 0.0,
            "classifier_pick": LANE_HARNESS, "fallback": True,
            "shape_class": "error",
        }
