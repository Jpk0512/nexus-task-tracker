#!/usr/bin/env bash
# PostToolUse/Task hook: tracks consecutive REVISE/BLOCKED markers per persona.
# Reads hook input JSON from stdin (Claude Code PostToolUse payload).
# Calls log.py task stall --task-id X --persona Y --marker Z.
# stall_count == 2 → hookSpecificOutput forcing Quill RCA + -pro variant.
# stall_count >= 3 → nested permissionDecision=deny + exit 2; escalation prompt in permissionDecisionReason.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/heartbeat-emitter.sh" 2>/dev/null || true

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

# Extract persona (subagent_type field in brief).
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
                p = inner.get('subagent_type', '')
                if p: print(p); exit()
            except Exception:
                pass
        print(ti.get('subagent_type', ''))
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

# Call log.py stall increment (compare-and-swap).
LOG_PY="$(cd "$(dirname "$SCRIPT_DIR")/.memory" 2>/dev/null && pwd)/log.py"
if [ ! -f "$LOG_PY" ]; then
    LOG_PY="${REPO_ROOT:-.}/.memory/log.py"
fi

RESULT=$(python3 "$LOG_PY" task stall \
    --task-id "$TASK_ID" \
    --persona "$PERSONA" \
    --marker "$MARKER" 2>/dev/null)

STALL_COUNT=$(printf '%s' "$RESULT" | python3 -c "
import json,sys
try: print(json.load(sys.stdin).get('stall_count',0))
except: print(0)
" 2>/dev/null)

LATENCY=$(($(ms_now) - START_MS))

if [ "$STALL_COUNT" -ge 3 ] 2>/dev/null; then
    emit_heartbeat "$HOOK_NAME" "$EVENT" "block" "$LATENCY"
    REASON=$(printf 'stall_count=%s for persona %s on %s. Three consecutive %s markers. Escalating to user — please investigate root cause before retrying. Task %s has stalled %s times with persona %s returning %s. Choose one: (1) force a Quill root-cause analysis, (2) escalate to the -pro variant, or (3) abort this task.' \
        "$STALL_COUNT" "$PERSONA" "$TASK_ID" "$MARKER" \
        "$TASK_ID" "$STALL_COUNT" "$PERSONA" "$MARKER")
    jq -n --arg r "$REASON" '{
        hookSpecificOutput: {
            hookEventName: "PostToolUse",
            permissionDecision: "deny",
            permissionDecisionReason: $r
        }
    }'
    # Durable backstop: the escalation must not rely on the JSON channel alone.
    # exit 2 hard-blocks even if the harness ignores hookSpecificOutput.
    exit 2
elif [ "$STALL_COUNT" -eq 2 ] 2>/dev/null; then
    emit_heartbeat "$HOOK_NAME" "$EVENT" "warn" "$LATENCY"
    case "$PERSONA" in
        *py*) QUILL_SUFFIX="py" ;;
        *ts*) QUILL_SUFFIX="ts" ;;
        *)    QUILL_SUFFIX="ts" ;;
    esac
    DE_PRO_PERSONA="${PERSONA%-pro}"
    jq -n \
        --arg marker "$MARKER" \
        --arg task_id "$TASK_ID" \
        --arg persona "$PERSONA" \
        --arg quill "$QUILL_SUFFIX" \
        --arg depro "$DE_PRO_PERSONA" \
        '{hookSpecificOutput: {hookEventName: "PostToolUse", additionalContext: ("[stall-counter] " + $marker + " stall_count=2 for " + $task_id + "/" + $persona + ". REQUIRED: (1) Spawn quill-" + $quill + " for root-cause analysis before retry. (2) Use " + $depro + "-pro variant (Opus/xhigh) for next dispatch.")}}'
    exit 0
else
    emit_heartbeat "$HOOK_NAME" "$EVENT" "allow" "$LATENCY"
    exit 0
fi
