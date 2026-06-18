#!/usr/bin/env bash
# PreToolUse hook (matcher: Task) — Article XIII / XIII.b parallel-first nudge.
#
# Design choice (v1.2.0):
#   This hook is an OBSERVABILITY + NUDGE mechanism, NOT a blocker.
#   It always exits 0 after emitting the reminder. A future enhancement
#   ("mechanical block") may upgrade this to a hard gate that inspects the
#   prior tool-call window and refuses a serial dispatch when an independent
#   sibling is detectable. For 1.2.0 we ship the nudge only.
#
# Behaviour:
#   1. Read the PreToolUse stdin JSON payload.
#   2. If tool_name != "Task", exit 0 silently (defensive — settings.json
#      already scopes matcher to Task).
#   3. Emit a short reminder paragraph to stderr citing Article XIII / XIII.b.
#   4. Best-effort: append one line per Task dispatch to .memory/nexus-dispatch.log
#      so the user can audit parallel ratios after the fact. Failures here
#      are swallowed.
#   5. Exit 0.

set -e

INPUT=$(cat)
TOOL_NAME=$(printf '%s' "$INPUT" | jq -r '.tool_name // ""' 2>/dev/null || echo "")

if [ "$TOOL_NAME" != "Task" ]; then
  exit 0
fi

SUBAGENT=$(printf '%s' "$INPUT" | jq -r '.tool_input.subagent_type // ""' 2>/dev/null || echo "")
SID=$(printf '%s' "$INPUT" | jq -r '.session_id // "unknown"' 2>/dev/null || echo unknown)

printf '[parallel-first-check] Article XIII / XIII.b: before this Task dispatch, have you confirmed there is no other independent persona that could run in parallel in the same message block? If yes, abort and re-dispatch as a parallel block.\n' 1>&2

LOG_FILE=".memory/nexus-dispatch.log"
if [ -d ".memory" ]; then
  TS=$(date -u +"%Y-%m-%dT%H:%M:%SZ" 2>/dev/null || echo "unknown")
  printf '%s\tsession=%s\tsubagent=%s\n' "$TS" "$SID" "$SUBAGENT" >> "$LOG_FILE" 2>/dev/null || true
fi

exit 0
