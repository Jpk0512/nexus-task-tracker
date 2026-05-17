#!/usr/bin/env bash
# SessionStart hook: pings LM Studio and emits hookSpecificOutput warning if unreachable.
# Expected: http://127.0.0.1:1234/v1/models returns 200 with qwen3.5-0.8b-intent-classification.
# Auth error shape: {"error":{"message":"...","type":"invalid_request_error","code":"..."}}

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/heartbeat-emitter.sh" 2>/dev/null || {
    # Fallback if heartbeat-emitter.sh is missing — define stubs so the rest of the script works.
    ms_now() { python3 -c "import time; print(int(time.time()*1000))" 2>/dev/null || echo 0; }
    emit_heartbeat() { :; }
}

LM_STUDIO_URL="http://127.0.0.1:1234/v1/models"
HOOK_NAME="router-health-check"
EVENT="SessionStart"
START_MS=$(ms_now)

RESPONSE=$(curl -sf -m 3 "$LM_STUDIO_URL" 2>/dev/null) || {
    emit_heartbeat "$HOOK_NAME" "$EVENT" "warn" "$(($(ms_now) - START_MS))"
    printf '{"hookSpecificOutput":"[router-health-check] WARNING: LM Studio unreachable at %s. Phase E router will fall through on all requests. Start with: lms server start --keep-alive 10m"}\n' "$LM_STUDIO_URL"
    exit 0
}

MISSING=$(python3 -c "
import json, sys
data = '''$RESPONSE'''
ids = [m['id'] for m in json.loads(data).get('data', [])]
missing = [r for r in ['qwen3.5-0.8b-intent-classification', 'text-embedding-nomic-embed-text-v1.5'] if r not in ids]
print(', '.join(missing))
" 2>/dev/null)

if [ -n "$MISSING" ]; then
    emit_heartbeat "$HOOK_NAME" "$EVENT" "warn" "$(($(ms_now) - START_MS))"
    printf '{"hookSpecificOutput":"[router-health-check] WARNING: LM Studio reachable but missing models: %s. Load them in LM Studio before Phase E router runs."}\n' "$MISSING"
    exit 0
fi

emit_heartbeat "$HOOK_NAME" "$EVENT" "allow" "$(($(ms_now) - START_MS))"
exit 0
