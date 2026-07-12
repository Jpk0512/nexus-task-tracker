#!/usr/bin/env bash
# PreToolUse hook (matcher: Task) — announces EVERY dispatch on the happy path.
#
# Inverts the prior failure-only visibility: today persona/goal metadata only
# surfaced when a gate rejected or stalled a dispatch. This hook emits a
# `[dispatch] persona=<X> goal=<…>` banner into additionalContext on every
# Task dispatch so the user always sees WHO was dispatched and WHY.
#
# Persona is read from subagent_type (Task shape) OR agent_type (Agent/Team
# shape), so every dispatch flavour announces.
#
# Non-blocking and fail-open: if the payload cannot be parsed, or neither persona
# field is present, the hook exits 0 with no output. It NEVER denies a dispatch —
# it is purely advisory (additionalContext, never permissionDecision).
#
# Wired via .claude/settings.json hooks.PreToolUse matcher "Task" (settings owner
# adds the command entry). P5-02 of the Plexus remediation plan (VIS-03).

set -euo pipefail

INPUT=$(cat)

# Single Python pass: parse the payload, derive persona + goal, and emit the
# additionalContext object. The harness nests the tool input under "input" for
# PreToolUse, but some shapes pass it flat — handle both (mirrors
# persona-alias-resolver.sh). All failure paths print nothing and exit 0.
echo "$INPUT" | python3 -c '
import json, sys

GOAL_MAX = 80

try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(0)

if not isinstance(data, dict):
    sys.exit(0)

# Tool input may be nested under "tool_input" (real harness envelope),
# "input" (older shape), or passed flat. Top-level "agent_type" is the
# CALLER identity — never fall back to it for the dispatch target.
tool_input = None
for _k in ("tool_input", "input"):
    _v = data.get(_k)
    if isinstance(_v, dict):
        tool_input = _v
        break
if tool_input is None:
    tool_input = data if isinstance(data, dict) else {}

persona = tool_input.get("subagent_type")
if not isinstance(persona, str) or not persona.strip():
    # Agent/Team-shaped dispatches carry the persona under "agent_type"
    # instead of "subagent_type" — fall back to it so they announce too.
    persona = tool_input.get("agent_type")
if not isinstance(persona, str) or not persona.strip():
    # No persona to announce — stay silent (robust pass).
    sys.exit(0)
persona = persona.strip()

# Goal text: prefer the human-readable description, fall back to the prompt.
goal_raw = tool_input.get("description")
if not isinstance(goal_raw, str) or not goal_raw.strip():
    goal_raw = tool_input.get("prompt")
if not isinstance(goal_raw, str):
    goal_raw = ""

# Collapse whitespace so a multi-line brief renders as a single clean line.
goal = " ".join(goal_raw.split())
if len(goal) > GOAL_MAX:
    goal = goal[: GOAL_MAX - 1].rstrip() + "…"  # ellipsis
if not goal:
    goal = "(no description)"

banner = "[dispatch] persona={} goal={}".format(persona, goal)

out = {
    "hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "additionalContext": banner,
    }
}
print(json.dumps(out))
' 2>/dev/null || true

exit 0
