#!/bin/bash
# PostToolUse hook (OD-7) — counts consecutive read-class tool calls without
# producing an Edit/Write/Bash side-effect. After 5 in a row, injects a STOP
# reminder into the next-turn context via the hook's JSON output channel.
#
# Read-class: Read, Grep, Glob, mcp__plugin_socraticode_socraticode__codebase_*
# Action-class: Edit, Write, MultiEdit, NotebookEdit, Bash (any), Task (any spawn)
#
# Session-scoped state under $TMPDIR (or /tmp). Per-session counter file.
# Wired via .claude/settings.json hooks.PostToolUse with matcher covering the
# read-class tool name regex.
#
# Output: when threshold tripped, prints a JSON object on stdout per the
# Claude Code hook spec injecting an additionalContext field. Otherwise silent.

set -e

INPUT=$(cat)

SID=$(printf '%s' "$INPUT" | jq -r '.session_id // "unknown"' 2>/dev/null || echo unknown)
TOOL=$(printf '%s' "$INPUT" | jq -r '.tool_name // ""' 2>/dev/null || echo "")

STATE_DIR="${TMPDIR:-/tmp}"
COUNT_FILE="${STATE_DIR}/claude-paralysis-${SID}.count"
POLL_CMD_FILE="${STATE_DIR}/claude-paralysis-${SID}.pollcmd"
POLL_COUNT_FILE="${STATE_DIR}/claude-paralysis-${SID}.pollcount"

ACTION_TOOL_RE='^(Edit|Write|MultiEdit|NotebookEdit|Bash|Task)$'
READ_TOOL_RE='^(Read|Grep|Glob|mcp__plugin_socraticode_socraticode__codebase_)'

# Bash poll-loop counter — INDEPENDENT of the read-class counter below. Keyed on
# the literal Bash command string (trimmed): a run of identical commands is the
# "agent polls the same thing over and over" loop the read-class counter (which
# every Bash resets) cannot see. Fail-open: any parse error => silent exit 0.
if [[ "$TOOL" == "Bash" ]]; then
  CMD=$(printf '%s' "$INPUT" | jq -r '(.tool_input.command // .input.command // .command) // ""' 2>/dev/null || echo "")
  # Trim leading/trailing whitespace so cosmetic spacing does not reset the run.
  CMD="${CMD#"${CMD%%[![:space:]]*}"}"
  CMD="${CMD%"${CMD##*[![:space:]]}"}"
  if [[ -n "$CMD" ]]; then
    PREV=""
    if [[ -f "$POLL_CMD_FILE" ]]; then
      PREV=$(cat "$POLL_CMD_FILE" 2>/dev/null || echo "")
    fi
    pollcount=0
    if [[ -f "$POLL_COUNT_FILE" ]]; then
      pollcount=$(cat "$POLL_COUNT_FILE" 2>/dev/null || echo 0)
    fi
    if [[ "$CMD" == "$PREV" ]]; then
      pollcount=$((pollcount + 1))
    else
      pollcount=1
    fi
    printf '%s' "$CMD" > "$POLL_CMD_FILE" 2>/dev/null || true
    printf '%d' "$pollcount" > "$POLL_COUNT_FILE" 2>/dev/null || true
    if (( pollcount >= 4 )); then
      # Emit-once-then-reset (mirrors the read-class emit) so a long poll run does
      # not spam every subsequent identical call.
      printf '0' > "$POLL_COUNT_FILE" 2>/dev/null || true
      : > "$POLL_CMD_FILE" 2>/dev/null || true
      jq -n --arg n "$pollcount" '{
        hookSpecificOutput: {
          hookEventName: "PostToolUse",
          additionalContext: ("[analysis-paralysis-guard] You have run this exact command " + $n + "x with no new result. Stop polling — wait once and read, or pivot. If waiting on async state, use Monitor (poll-with-stop-condition) instead of a busy loop.")
        }
      }' 2>/dev/null || true
    fi
  fi
fi

# Action-class tool — reset counter and exit silently
if printf '%s' "$TOOL" | grep -qE "$ACTION_TOOL_RE"; then
  printf '0' > "$COUNT_FILE" 2>/dev/null || true
  exit 0
fi

# Read-class tool — increment counter
if printf '%s' "$TOOL" | grep -qE "$READ_TOOL_RE"; then
  current=0
  if [[ -f "$COUNT_FILE" ]]; then
    current=$(cat "$COUNT_FILE" 2>/dev/null || echo 0)
  fi
  current=$((current + 1))
  printf '%d' "$current" > "$COUNT_FILE" 2>/dev/null || true

  if (( current >= 5 )); then
    # Emit reminder once at threshold, then reset so we don't spam every turn after
    printf '0' > "$COUNT_FILE" 2>/dev/null || true
    cat <<'EOF'
{
  "hookSpecificOutput": {
    "hookEventName": "PostToolUse",
    "additionalContext": "[analysis-paralysis-guard] 5 consecutive read-class tool calls without an action. STOP. State in one sentence why no progress yet, then either: (a) commit to the findings JSON / decision with what you have, OR (b) return ## NEXUS:BLOCKED with the specific missing information. Do not run more discovery calls until you have taken a side-effecting action OR escalated."
  }
}
EOF
  fi
fi

exit 0
