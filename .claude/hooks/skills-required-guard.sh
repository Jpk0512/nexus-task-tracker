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

# Personas that MUST have non-empty skills_required in their brief.
CODE_WRITING_PERSONAS = frozenset({
    "forge-ui", "forge-ui-pro",
    "forge-wire", "forge-wire-pro",
    "pipeline-data", "pipeline-data-pro",
    "pipeline-async", "pipeline-async-pro",
    "atlas", "atlas-pro",
    "hermes", "hermes-pro",
})

REPO_ROOT = Path(os.environ.get("REPO_ROOT") or os.environ.get("_HOOK_REPO_ROOT") or os.getcwd())
SKILL_MAP_PATH = REPO_ROOT / "docs" / "agents" / "SKILL_MAP.md"


def _load_skill_map() -> dict[tuple[str, str], list[str]]:
    """Parse SKILL_MAP.md table into {(persona, work_type): [skills]}."""
    if not SKILL_MAP_PATH.exists():
        return {}
    result: dict[tuple[str, str], list[str]] = {}
    in_table = False
    for line in SKILL_MAP_PATH.read_text().splitlines():
        line = line.strip()
        if line.startswith("| persona") or line.startswith("|---"):
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


def _extract_brief(tool_input: dict) -> dict:
    """Try to parse the brief JSON from the task description or prompt field."""
    for field in ("description", "prompt", "input"):
        raw = tool_input.get(field, "")
        if not isinstance(raw, str) or not raw.strip():
            continue
        # The brief may be embedded in a markdown JSON block
        for block in re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL):
            try:
                return json.loads(block)
            except json.JSONDecodeError:
                continue
        # Or the whole field is JSON
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            continue
    return {}


def main() -> int:
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return 0  # fail open

    # Normalise: PreToolUse wraps tool input under 'input' key
    tool_input: dict = payload.get("input", payload)

    # Get the subagent_type
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

    # --- Gate 1: Block if code-writing persona has empty skills_required ---
    if subagent_type in CODE_WRITING_PERSONAS and not skills_required:
        block_msg = {
            "hookSpecificOutput": json.dumps({
                "decision": "block",
                "reason": (
                    f"skills_required is absent or empty for code-writing persona '{subagent_type}'. "
                    "Per CONTRACT R19, every brief for a code-writing persona MUST list explicit skills. "
                    "See docs/agents/SKILL_MAP.md for the minimum required skills per work_type."
                ),
            })
        }
        print(json.dumps(block_msg))
        return 0  # hookSpecificOutput carries the block decision; exit 0

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
                "hookSpecificOutput": json.dumps({
                    "decision": "warn",
                    "additionalContext": (
                        f"skills_required for '{subagent_type}' (work_type='{work_type}') "
                        f"is missing mandatory skills: {missing}. "
                        "Per SKILL_MAP.md these are required. "
                        "Add them to the brief unless this is intentionally a partial dispatch."
                    ),
                })
            }
            print(json.dumps(warn_msg))
            return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
