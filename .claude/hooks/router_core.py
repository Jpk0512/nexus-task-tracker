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

import hashlib
import json
import os
import socket
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
#   = the personas the router classifier may emit. This DELIBERATELY INCLUDES the
#     four `-pro` escalation variants (audit OPT-062: the classifier must be ABLE
#     to escalate complex / low-confidence work) and DELIBERATELY EXCLUDES
#     orchestrator-only mechanism personas like `lens-fast` (dispatched as the
#     fixed parallel sibling of `lens` after an implementer NEXUS:DONE, never
#     selected from a user prompt). build_persona_enum renders exactly this set
#     plus the synthetic 'meta' no-dispatch route.
RETIRED_BASE_PERSONAS: frozenset[str] = frozenset({"forge", "pipeline", "quill"})
CLASSIFIER_PERSONAS: frozenset[str] = frozenset(
    {
        "atlas",
        "forge-ui",
        "forge-ui-pro",
        "forge-wire",
        "forge-wire-pro",
        "hermes",
        "lens",
        "palette",
        "pipeline-async",
        "pipeline-async-pro",
        "pipeline-data",
        "pipeline-data-pro",
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


def _wrap_result(
    parsed: dict[str, Any],
    messages: list[dict[str, str]],
    confidence_margin: float | None = None,
) -> dict[str, Any]:
    """
    Wrap a parsed classification with the exact model input so the capture log
    records a reproducible (input, output) pair. system_prompt_sha256 hashes the
    rendered system message verbatim — it pins the OPT-001-buggy prompt.

    confidence_margin (OPT-012) is the logprob-derived top-2 margin when the server
    returned logprobs, else None (verbalized-confidence fallback). It rides alongside
    the classification so router.py can record it AND prefer it where available.
    """
    system_content = next(
        (m["content"] for m in messages if m.get("role") == "system"), ""
    )
    return {
        "classification": _normalize_confidence(parsed),
        "messages": messages,
        "model": ROUTER_MODEL,
        "confidence_margin": confidence_margin,
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
                                    "logprobs": {...}} to exercise the OPT-012 margin
                                    parse + graceful fallback.
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
    # fallback are exercisable without a live server.
    mock_resp = os.environ.get("_MOCK_ROUTER_RESPONSE")
    if mock_resp is not None:
        try:
            raw = json.loads(mock_resp)
        except json.JSONDecodeError:
            return None
        if isinstance(raw, dict) and "classification" in raw:
            parsed = raw["classification"]
            margin = _compute_logprob_margin(raw.get("logprobs"))
        else:
            parsed = raw
            margin = None
        messages = _build_messages(user_msg, resolved_agents_dir, resolved_skills_dir)
        return _wrap_result(parsed, messages, confidence_margin=margin)

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
        return _wrap_result(json.loads(content), messages, confidence_margin=margin)

    except Exception as exc:
        # Benign (LM Studio down/slow) → silent fallthrough. Unexpected (bad
        # response shape, JSON corruption, programming error) → one stderr line
        # so a real router-path bug is not swallowed. Either way return None so
        # the caller falls through to Nexus's own routing.
        if not _is_benign_call_error(exc):
            print(f"[router] degraded: {type(exc).__name__}", file=sys.stderr)
        return None
