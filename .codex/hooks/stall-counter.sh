#!/usr/bin/env bash
# PostToolUse/Task hook: tracks consecutive REVISE/BLOCKED markers per persona.
# Reads hook input JSON from stdin (Claude Code PostToolUse payload).
# Calls log.py task stall --task-id X --persona Y --marker Z.
# stall_count == 2 → hookSpecificOutput forcing Quill RCA + -pro variant.
# stall_count >= 3 → block + AskUserQuestion injection.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/heartbeat-emitter.sh" 2>/dev/null || {
    # Fallback if heartbeat-emitter.sh is missing/unsourceable — define stubs so
    # the rest of the script (ms_now / emit_heartbeat) never errors on an
    # undefined function. Mirrors router-health-check.sh.
    ms_now() { python3 -c "import time; print(int(time.time()*1000))" 2>/dev/null || echo 0; }
    emit_heartbeat() { :; }
}
# Belt-and-suspenders: even if the source succeeded but the file did not define
# both helpers (truncated/edited), guarantee they exist before first use.
command -v ms_now >/dev/null 2>&1 || ms_now() { python3 -c "import time; print(int(time.time()*1000))" 2>/dev/null || echo 0; }
command -v emit_heartbeat >/dev/null 2>&1 || emit_heartbeat() { :; }

HOOK_NAME="stall-counter"
EVENT="PostToolUse"
START_MS=$(ms_now)

# Read hook input JSON from stdin.
INPUT=$(cat)

# Extract marker from tool_response text (last NEXUS:* marker).
MARKER=$(printf '%s' "$INPUT" | python3 -c "
import json, sys, re
try:
    d = json.load(sys.stdin)
    # Tool response text lives in tool_response.content or tool_result
    text = ''
    for key in ('tool_response', 'tool_result', 'output'):
        v = d.get(key, '')
        if isinstance(v, str):
            text = v
            break
        if isinstance(v, list):
            text = ' '.join(str(item.get('text','')) for item in v if isinstance(item, dict))
            break
    m = re.findall(r'##\s*NEXUS:(REVISE|BLOCKED)', text)
    print(m[-1] if m else '')
except Exception:
    print('')
" 2>/dev/null)

# No REVISE/BLOCKED in output — nothing to count.
if [ -z "$MARKER" ]; then
    emit_heartbeat "$HOOK_NAME" "$EVENT" "noop" "$(($(ms_now) - START_MS))"
    exit 0
fi

# Extract task_id from brief input (look for task_id, TASK-NNN, or task: field).
TASK_ID=$(printf '%s' "$INPUT" | python3 -c "
import json, sys, re
try:
    d = json.load(sys.stdin)
    # Brief is typically in tool_input.value or tool_input.description
    text = ''
    ti = d.get('tool_input', {})
    if isinstance(ti, dict):
        text = ti.get('value', '') or ti.get('description', '') or json.dumps(ti)
    else:
        text = str(ti)
    # Try explicit task_id field first
    m = re.search(r'\"task_id\"\s*:\s*\"(TASK-\d+)\"', text)
    if m:
        print(m.group(1)); sys.exit(0)
    # Fall back to any TASK-NNN occurrence
    m = re.search(r'\b(TASK-\d+)\b', text)
    if m:
        print(m.group(1)); sys.exit(0)
    print('')
except Exception:
    print('')
" 2>/dev/null)

# Extract persona. Read BOTH the Task shape (subagent_type) AND the Agent/Team
# shape (agent_type) — whichever the harness presents (P6-01 / DW-02..05). A
# team-spawned teammate completion carries agent_type; without this a stalling
# teammate would never be counted toward the 3-strike escalation. A plain
# TaskUpdate/TaskCreate bookkeeping event carries neither field (and no
# REVISE/BLOCKED marker), so this resolves empty and the hook has already
# exited 0 above on the empty-marker check — task bookkeeping is never gated.
PERSONA=$(printf '%s' "$INPUT" | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    ti = d.get('tool_input', {})
    if isinstance(ti, dict):
        val = ti.get('value', '')
        if isinstance(val, str):
            import json as j2
            try:
                inner = j2.loads(val)
                p = inner.get('subagent_type', '') or inner.get('agent_type', '')
                if p: print(p); exit()
            except Exception:
                pass
        print(
            ti.get('subagent_type', '')
            or ti.get('agent_type', '')
            or d.get('subagent_type', '')
            or d.get('agent_type', '')
        )
    else:
        print('')
except Exception:
    print('')
" 2>/dev/null)

# If we can't identify task_id or persona, emit heartbeat and exit clean.
if [ -z "$TASK_ID" ] || [ -z "$PERSONA" ]; then
    emit_heartbeat "$HOOK_NAME" "$EVENT" "skip-no-context" "$(($(ms_now) - START_MS))"
    exit 0
fi

# Resolve log.py from THIS script's repo root by walking parents for .memory
# (mirrors broker-gate.py:_repo_root). The old `dirname(SCRIPT_DIR)/.memory`
# guess broke whenever the hook tree was not exactly two levels under the root,
# and the `${REPO_ROOT:-.}` fallback resolved to ./.memory/log.py — a path that
# does not exist when the hook fires from any CWD other than the repo root.
resolve_log_py() {
    local dir="$SCRIPT_DIR"
    while [ "$dir" != "/" ]; do
        if [ -f "$dir/.memory/log.py" ]; then
            printf '%s' "$dir/.memory/log.py"
            return 0
        fi
        dir="$(dirname "$dir")"
    done
    return 1
}

LOG_PY="$(resolve_log_py)"
if [ -z "$LOG_PY" ] || [ ! -f "$LOG_PY" ]; then
    # Cannot find the memory CLI — this is a real failure, not a no-op. Say so
    # LOUDLY on stderr and surface it as additionalContext, then exit clean so a
    # broken install does not wedge every Task return.
    emit_heartbeat "$HOOK_NAME" "$EVENT" "error-no-logpy" "$(($(ms_now) - START_MS))"
    printf '[stall-counter] ERROR: cannot locate .memory/log.py from %s — stall tracking DISABLED this turn.\n' "$SCRIPT_DIR" >&2
    printf '{"hookSpecificOutput":{"hookEventName":"PostToolUse","additionalContext":"[stall-counter] ERROR: .memory/log.py not found from %s. Stall escalation is DISABLED. Repair the install before relying on the 3-strike guard."}}\n' "$SCRIPT_DIR"
    exit 0
fi

# Run the stall increment. Capture stdout, stderr, and the exit code separately
# so a failed call (unknown task, bad marker, DB error) is NEVER silently
# coerced to stall_count=0 — that would defeat the 3-strike escalation.
STALL_ERR_FILE="$(mktemp 2>/dev/null || printf '/tmp/stall-counter.%s.err' "$$")"
RESULT=$(python3 "$LOG_PY" task stall \
    --task-id "$TASK_ID" \
    --persona "$PERSONA" \
    --marker "$MARKER" 2>"$STALL_ERR_FILE")
STALL_RC=$?
STALL_ERR=$(cat "$STALL_ERR_FILE" 2>/dev/null)
rm -f "$STALL_ERR_FILE" 2>/dev/null

LATENCY=$(($(ms_now) - START_MS))

# Parse stall_count out of the JSON. Print a sentinel ("NaN") — NOT 0 — when the
# field is missing or the payload is not JSON, so we can tell "the call failed"
# apart from "the call ran and reported count 0".
STALL_COUNT=$(printf '%s' "$RESULT" | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    c = d.get('stall_count')
    print(c if isinstance(c, int) else 'NaN')
except Exception:
    print('NaN')
" 2>/dev/null)

# A non-zero exit OR an unparseable stall_count means the stall call FAILED.
# Surface it loudly (stderr + additionalContext) and exit clean WITHOUT counting
# it as a stall. Distinguishing this from a real count is the whole point.
if [ "$STALL_RC" -ne 0 ] || [ "$STALL_COUNT" = "NaN" ]; then
    emit_heartbeat "$HOOK_NAME" "$EVENT" "error-stall-call" "$LATENCY"
    printf '[stall-counter] ERROR: log.py task stall failed (rc=%s) for %s/%s marker=%s: %s\n' \
        "$STALL_RC" "$TASK_ID" "$PERSONA" "$MARKER" "$STALL_ERR" >&2
    printf '{"hookSpecificOutput":{"hookEventName":"PostToolUse","additionalContext":"[stall-counter] WARNING: stall increment FAILED (rc=%s) for task %s persona %s. The 3-strike escalation did NOT advance this turn. Cause: %s"}}\n' \
        "$STALL_RC" "$TASK_ID" "$PERSONA" "$(printf '%s' "$STALL_ERR" | tr '\n' ' ' | sed 's/"/'"'"'/g')"
    exit 0
fi

# STALL_COUNT is guaranteed a non-negative integer here (the rc!=0 / NaN paths
# exited above), so the numeric comparisons no longer mask their stderr — a
# malformed value would now surface instead of silently taking the else branch.
if [ "$STALL_COUNT" -ge 3 ]; then
    emit_heartbeat "$HOOK_NAME" "$EVENT" "block" "$LATENCY"
    printf '{"decision":"block","reason":"stall_count=%s for persona %s on %s. Three consecutive %s markers. See additionalContext for escalation options.","hookSpecificOutput":{"hookEventName":"PostToolUse","additionalContext":"[stall-counter] ESCALATION: Task %s has stalled %s times with persona %s returning %s. ACTION REQUIRED: (1) force a Quill root-cause analysis, (2) escalate to the -pro variant, or (3) abort this task."}}\n' \
        "$STALL_COUNT" "$PERSONA" "$TASK_ID" "$MARKER" \
        "$TASK_ID" "$STALL_COUNT" "$PERSONA" "$MARKER"
    exit 2
elif [ "$STALL_COUNT" -eq 2 ]; then
    emit_heartbeat "$HOOK_NAME" "$EVENT" "warn" "$LATENCY"
    case "$PERSONA" in
        *py*) QUILL_SUFFIX="py" ;;
        *ts*) QUILL_SUFFIX="ts" ;;
        *)    QUILL_SUFFIX="ts" ;;
    esac
    DE_PRO_PERSONA="${PERSONA%-pro}"
    printf '{"hookSpecificOutput":{"hookEventName":"PostToolUse","additionalContext":"[stall-counter] %s stall_count=2 for %s/%s. REQUIRED: (1) Spawn quill-%s for root-cause analysis before retry. (2) Use %s-pro variant (Opus/xhigh) for next dispatch."}}\n' \
        "$MARKER" "$TASK_ID" "$PERSONA" \
        "$QUILL_SUFFIX" \
        "$DE_PRO_PERSONA"
    exit 0
else
    emit_heartbeat "$HOOK_NAME" "$EVENT" "allow" "$LATENCY"
    exit 0
fi
