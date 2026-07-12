#!/usr/bin/env bash
# PostToolUse hook (matcher: Task) — backing heuristic for the
# `parallel-first-check` skill (P6-04 / Art. XIII / XIII.b).
#
# Escalating advisory -> ask:
#   count=1 : first single dispatch in the window — record, stay silent.
#   count=2 : second consecutive single dispatch — emit additionalContext
#              advisory (auto-parallel-nudge). PERSIST count=2 (do NOT reset).
#   count>=3: third+ consecutive single dispatch — emit permissionDecision=ask
#              (NEVER deny). Reset counter only after ask fires or a Workflow
#              primitive is detected.
#
# Exemptions (fail-open, never block):
#   - Dispatch carries team_name (named serial pipeline, e.g. scout->impl->lens)
#   - Persona is in EXEMPT_PERSONAS (scout/lens/lens-fast/palette)
#   - Any parse error / unexpected shape => exit 0, no output
#
# Output modes:
#   advisory (count==2): hookSpecificOutput.additionalContext
#   ask (count>=3):      hookSpecificOutput.permissionDecision="ask" +
#                        hookSpecificOutput.additionalContext
#
# Wired via .claude/settings.json hooks.PostToolUse matcher "Task".
# Bash-only (no Python body). 3.9 import-safety not applicable (bash).

set -euo pipefail

INPUT=$(cat)

# Window within which two single dispatches are treated as "same planning loop".
WINDOW_SECS="${PARALLEL_FIRST_WINDOW_SECS:-90}"

# Personas that are exempt from the parallel-first heuristic.
# These route through named serial pipelines or are read-only verifiers.
EXEMPT_PERSONAS="^(scout|lens|lens-fast|palette)$"

# Single jq pass: pull session_id, tool_name, subagent_type, team_name.
# Failures fall through to silent exit 0.
PARSED=$(printf '%s' "$INPUT" | jq -r '
  def toolinput: (.input // .tool_input // .);
  (.session_id // "unknown") as $sid
  | (.tool_name // "") as $tool
  | (toolinput | if type == "object" then (.subagent_type // "") else "" end) as $persona
  | (toolinput | if type == "object" then (.team_name // "") else "" end) as $team
  | [
      $sid,
      $tool,
      (if ($persona | type) == "string" and ($persona | gsub("\\s";"") | length) > 0
       then $persona else "" end),
      $team
    ] | @tsv
' 2>/dev/null) || exit 0

[ -n "$PARSED" ] || exit 0

SID=$(printf '%s' "$PARSED" | cut -f1)
TOOL=$(printf '%s' "$PARSED" | cut -f2)
PERSONA=$(printf '%s' "$PARSED" | cut -f3)
TEAM=$(printf '%s' "$PARSED" | cut -f4)

[ -n "$SID" ] || SID="unknown"

STATE_DIR="${TMPDIR:-/tmp}"
STATE_FILE="${STATE_DIR}/claude-parallel-first-${SID}.state"  # "count epoch"

now=$(date +%s 2>/dev/null || echo 0)

# Workflow primitives => orchestrator authored a Workflow. Reset and exit:
# the parallel-first path was taken, nothing to nag about.
WORKFLOW_TOOL_RE='^(TeamCreate|TaskCreate|TaskUpdate|TeamDelete|SendMessage)$'
if printf '%s' "$TOOL" | grep -qE "$WORKFLOW_TOOL_RE"; then
  : > "$STATE_FILE" 2>/dev/null || true
  exit 0
fi

# Only raw single Task dispatches (a Task carrying a subagent_type) advance the
# heuristic. A Task without subagent_type is a Workflow-internal agent() shape —
# not a chat-thread single dispatch — leave state untouched and stay silent.
if [ "$TOOL" != "Task" ] || [ -z "$PERSONA" ]; then
  exit 0
fi

# Exempt: named serial pipeline (team_name present) or exempt persona.
if [ -n "$TEAM" ]; then
  exit 0
fi
if printf '%s' "$PERSONA" | grep -qE "$EXEMPT_PERSONAS"; then
  exit 0
fi

# Read existing state.
prev_count=0
prev_epoch=0
if [ -f "$STATE_FILE" ]; then
  read -r prev_count prev_epoch < "$STATE_FILE" 2>/dev/null || true
  case "$prev_count" in (*[!0-9]*|'') prev_count=0 ;; esac
  case "$prev_epoch" in (*[!0-9]*|'') prev_epoch=0 ;; esac
fi

gap=$((now - prev_epoch))
in_window=0
if [ "$gap" -ge 0 ] && [ "$gap" -le "$WINDOW_SECS" ] && [ "$prev_count" -ge 1 ]; then
  in_window=1
fi

if [ "$in_window" -eq 0 ]; then
  # First dispatch (or prior one aged out of window): record and stay silent.
  printf '%d %d\n' 1 "$now" > "$STATE_FILE" 2>/dev/null || true
  exit 0
fi

# Compute new count (cap at 99 to prevent unbounded growth).
new_count=$((prev_count + 1))
[ "$new_count" -le 99 ] || new_count=99

if [ "$new_count" -eq 2 ]; then
  # Second consecutive single dispatch: advisory only. PERSIST count=2 so the
  # next dispatch sees count=2 (not reset) and escalates to ask.
  printf '%d %d\n' 2 "$now" > "$STATE_FILE" 2>/dev/null || true
  cat <<'EOF'
{
  "hookSpecificOutput": {
    "hookEventName": "PostToolUse",
    "additionalContext": "[auto-parallel-nudge] Two consecutive single Task dispatches detected in one planning loop. If these subtasks are independent (no shared file scope, no read-after-write dependency), STOP firing sequential single dispatches — author a dynamic Workflow (TeamCreate + agent() teammates) so they run in parallel per Constitution Art. XIII / XIII.b. If a real serial dependency forces the order, name it in writing. Advisory only — not blocking."
  }
}
EOF
  exit 0
fi

# count >= 3: escalate to ask. Reset counter so a single valid justification
# the user provides clears the streak cleanly.
: > "$STATE_FILE" 2>/dev/null || true
cat <<'EOF'
{
  "hookSpecificOutput": {
    "hookEventName": "PostToolUse",
    "permissionDecision": "ask",
    "additionalContext": "[auto-parallel-nudge] Three or more consecutive single Task dispatches detected in this planning loop with no shared-dependency signal. Parallelism-by-default requires a dynamic Workflow for >=2 independent steps. Please confirm: are these dispatches truly sequentially dependent? If so, name the dependency. If not, stop and author a Workflow instead."
  }
}
EOF
exit 0
