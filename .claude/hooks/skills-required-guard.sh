#!/usr/bin/env python3
# PreToolUse hook (matcher: Task) — enforces CONTRACT R19: brief-driven skill loading.
#
# Logic:
#   1. Parse brief JSON from the Task tool input (description or prompt field).
#   2. Extract subagent_type and skills_required from the brief.
#   3. If persona is a code-writing persona AND skills_required is absent/empty: BLOCK.
#   4. If skills_required is non-empty but missing mandatory skills per SKILL_MAP.md: WARN.
#   5. Fail open on any parse error or missing SKILL_MAP.md.
#
# Exit codes: 0 = pass/warn, 2 = block.

import json
import os
import re
import sys
from pathlib import Path

REPO_ROOT = Path(os.environ.get(
    "_HOOK_REPO_ROOT",
    "/Users/john.keeney/nexus-task-tracker",
))
SKILL_MAP_PATH = REPO_ROOT / "docs" / "agents" / "SKILL_MAP.md"
_DELIVERABLES_PATH = Path(__file__).parent / "deliverables.json"

# Fallback roster used when deliverables.json cannot be loaded.
_FALLBACK_CODE_WRITING_PERSONAS = frozenset({
    "forge-ui", "forge-ui-pro",
    "forge-wire", "forge-wire-pro",
    "pipeline-data", "pipeline-data-pro",
    "pipeline-async", "pipeline-async-pro",
    "atlas", "atlas-pro",
    "hermes", "hermes-pro",
})


def _load_code_writing_personas() -> frozenset:
    """Derive code-writing personas from deliverables.json.

    A persona is a code-writer when its entry does NOT have must_not_modify
    covering all paths (i.e., is not a read-only persona like scout/lens).
    Tombstone entries (_note contains "Tombstone") are excluded.
    Falls back to the hardcoded set on any load error.
    """
    try:
        manifest = json.loads(_DELIVERABLES_PATH.read_text())
    except Exception:
        return _FALLBACK_CODE_WRITING_PERSONAS
    result = set()
    for persona, cfg in manifest.items():
        if persona.startswith("_"):
            continue
        if not isinstance(cfg, dict):
            continue
        note = cfg.get("_note", "")
        if isinstance(note, str) and "Tombstone" in note:
            continue
        must_not = cfg.get("must_not_modify", [])
        if isinstance(must_not, list) and "**/*" in must_not:
            continue
        result.add(persona)
    return frozenset(result) if result else _FALLBACK_CODE_WRITING_PERSONAS


# Personas that MUST have non-empty skills_required in their brief.
# Derived from deliverables.json: all non-read-only, non-tombstone personas.
CODE_WRITING_PERSONAS = _load_code_writing_personas()


def _load_skill_map() -> dict[tuple[str, str], list[str]]:
    """Parse SKILL_MAP.md table into {(persona, work_type): [skills]}."""
    if not SKILL_MAP_PATH.exists():
        return {}
    result: dict[tuple[str, str], list[str]] = {}
    in_table = False
    for line in SKILL_MAP_PATH.read_text().splitlines():
        line = line.strip()
        if line.startswith(("| persona", "|---")):
            in_table = True
            continue
        if not in_table:
            continue
        if not line.startswith("|"):
            in_table = False
            continue
        parts = [p.strip() for p in line.strip("|").split("|")]
        if len(parts) < 3:
            continue
        persona, work_type, skills_raw = parts[0], parts[1], parts[2]
        if not persona or persona.startswith("-"):
            continue
        skills = [s.strip() for s in skills_raw.split(",") if s.strip()]
        result[(persona, work_type)] = skills
    return result


# Free-text 'skills_required: a, b, c' prose line. Matches a line whose key is
# skills_required (optionally quoted, with ':' or '=' separator) and whose value
# is a comma/space-separated list of skill tokens. This is the additive fallback
# for briefs written as PROSE rather than a fenced ```json block (the prior
# extractor saw ONLY JSON, so a prose skills_required line was silently ignored
# and a code-writing persona slipped past Gate 1).
_SKILLS_LINE_RE = re.compile(
    r"""['"]?skills_required['"]?\s*[:=]\s*(.+)""",
    re.IGNORECASE,
)


def _extract_skills_freetext(raw):
    """Scan raw prose for a 'skills_required: a, b' line; return list[str] or [].

    Only the value up to the end of the line is consumed. Tokens are split on
    commas (and whitespace as a secondary separator) and stripped of surrounding
    brackets/quotes so 'skills_required: forge-ui-conventions, rsc-boundary-rules'
    and a bare 'skills_required: forge-ui-conventions' both resolve.
    """
    if not isinstance(raw, str):
        return []
    for line in raw.splitlines():
        m = _SKILLS_LINE_RE.search(line.strip())
        if not m:
            continue
        value = m.group(1).strip().strip("[]")
        # Comma is the primary separator; fall back to whitespace if no commas.
        parts = value.split(",") if "," in value else value.split()
        skills = [p.strip().strip("'\"[]") for p in parts]
        skills = [s for s in skills if s]
        if skills:
            return skills
    return []


def _extract_brief(tool_input: dict) -> dict:
    """Try to parse the brief JSON from the task description or prompt field.

    Order: fenced ```json block, then whole-field JSON, then — additively — a
    free-text 'skills_required: a, b' prose line. The free-text path NEVER
    overrides a JSON brief that already carried skills_required; it only supplies
    a brief when no JSON parsed, OR backfills skills_required when JSON parsed but
    omitted it.
    """
    freetext_skills: list = []
    for field in ("description", "prompt", "input"):
        raw = tool_input.get(field, "")
        if not isinstance(raw, str) or not raw.strip():
            continue
        # The brief may be embedded in a markdown JSON block
        for block in re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL):
            try:
                parsed = json.loads(block)
            except json.JSONDecodeError:
                continue
            if "skills_required" not in parsed:
                backfill = _extract_skills_freetext(raw)
                if backfill:
                    parsed["skills_required"] = backfill
            return parsed
        # Or the whole field is JSON
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            if "skills_required" not in parsed:
                backfill = _extract_skills_freetext(raw)
                if backfill:
                    parsed["skills_required"] = backfill
            return parsed
        # No JSON in this field — remember any prose skills_required line so a
        # purely free-text brief still surfaces its skills.
        if not freetext_skills:
            freetext_skills = _extract_skills_freetext(raw)
    if freetext_skills:
        return {"skills_required": freetext_skills}
    return {}


def main() -> int:
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return 0  # fail open

    # Normalise where the tool payload lives. Claude's PreToolUse:Task nests the
    # arguments under '.tool_input'; some surfaces use '.input'; a few pass them at
    # top level. Resolve in that order — '.tool_input' FIRST — matching the sibling
    # gate persona-alias-resolver.sh exactly. (Reading '.input'/top-level only made
    # this gate silently fail open on real Claude Task dispatches, where
    # subagent_type lives under '.tool_input'.)
    tool_input: dict = payload.get(
        "tool_input", payload.get("input", payload)
    )
    if not isinstance(tool_input, dict):
        tool_input = {}

    # Get the subagent_type (resolved tool payload first, then top level).
    subagent_type: str = (
        tool_input.get("subagent_type", "")
        or payload.get("subagent_type", "")
    ).lower().strip()

    if not subagent_type:
        return 0  # not a subagent dispatch we can inspect

    # Parse the brief JSON from description/prompt
    brief = _extract_brief(tool_input)

    # skills_required from brief (may be absent, None, or a list)
    skills_required = brief.get("skills_required")
    if skills_required is None:
        # Also check if it's a top-level field in tool_input
        skills_required = tool_input.get("skills_required")

    # Normalise to list
    if isinstance(skills_required, str):
        skills_required = [s.strip() for s in skills_required.split(",") if s.strip()]
    elif not isinstance(skills_required, list):
        skills_required = []

    skills_required_set = {s.lower() for s in skills_required}

    # --- Gate 1: Deny if code-writing persona has empty skills_required ---
    if subagent_type in CODE_WRITING_PERSONAS and not skills_required:
        reason = (
            f"skills_required is absent or empty for code-writing persona '{subagent_type}'. "
            "Per CONTRACT R19, every brief for a code-writing persona MUST list explicit skills. "
            "See docs/agents/SKILL_MAP.md for the minimum required skills per work_type."
        )
        deny_msg = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        }
        print(json.dumps(deny_msg))
        print(reason, file=sys.stderr)
        return 2  # nested permissionDecision deny + exit 2 actually blocks

    # --- Gate 2: Warn if mandatory skills are missing ---
    if skills_required:
        work_type: str = brief.get("work_type", "").lower().strip()
        skill_map = _load_skill_map()

        # Find matching row(s) — try exact match, then persona-only
        mandatory: list[str] = []
        if work_type:
            mandatory = skill_map.get((subagent_type, work_type), [])
        if not mandatory:
            # Collect all mandatory skills for this persona across all work_types
            mandatory = []
            for (p, _wt), skills in skill_map.items():
                if p == subagent_type:
                    mandatory.extend(skills)

        missing = [s for s in mandatory if s.lower() not in skills_required_set]
        if missing:
            warn_msg = {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "additionalContext": (
                        f"skills_required for '{subagent_type}' (work_type='{work_type}') "
                        f"is missing mandatory skills: {missing}. "
                        "Per SKILL_MAP.md these are required. "
                        "Add them to the brief unless this is intentionally a partial dispatch."
                    ),
                }
            }
            print(json.dumps(warn_msg))
            return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
