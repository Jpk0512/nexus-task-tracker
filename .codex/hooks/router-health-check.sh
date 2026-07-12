#!/usr/bin/env bash
# SessionStart hook: pings LM Studio and emits a nested hookSpecificOutput warning if unreachable.
# The router model + endpoint are read from the same env vars router_core.py uses:
#   _HOOK_ROUTER_MODEL (default: granite-4.1-3b) and _HOOK_QWEN_URL
#   (default: http://127.0.0.1:1234/v1/chat/completions). The models endpoint is
#   derived from the chat-completions URL by swapping the trailing path for /v1/models.
# Auth error shape: {"error":{"message":"...","type":"invalid_request_error","code":"..."}}

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/heartbeat-emitter.sh" 2>/dev/null || {
    # Fallback if heartbeat-emitter.sh is missing — define stubs so the rest of the script works.
    ms_now() { python3 -c "import time; print(int(time.time()*1000))" 2>/dev/null || echo 0; }
    emit_heartbeat() { :; }
}

# Derive the /v1/models probe URL from the configured chat-completions endpoint.
QWEN_URL="${_HOOK_QWEN_URL:-http://127.0.0.1:1234/v1/chat/completions}"
LM_STUDIO_URL="$(QWEN_URL="$QWEN_URL" python3 -c "
import os
from urllib.parse import urlsplit, urlunsplit
u = urlsplit(os.environ.get('QWEN_URL', ''))
print(urlunsplit((u.scheme, u.netloc, '/v1/models', '', '')))
")"
# Required models: the router chat model + the embedding model, both env-overridable.
ROUTER_MODEL="${_HOOK_ROUTER_MODEL:-granite-4.1-3b}"
EMBED_MODEL="${_HOOK_EMBED_MODEL:-text-embedding-nomic-embed-text-v1.5}"

HOOK_NAME="router-health-check"
EVENT="SessionStart"
START_MS=$(ms_now)

RESPONSE=$(curl -sf -m 3 "$LM_STUDIO_URL" 2>/dev/null) || {
    emit_heartbeat "$HOOK_NAME" "$EVENT" "warn" "$(($(ms_now) - START_MS))"
    python3 - "$LM_STUDIO_URL" <<'PY'
import json, sys
url = sys.argv[1]
msg = (f"[router-health-check] WARNING: LM Studio unreachable at {url}. "
       "Phase E router will fall through on all requests. "
       "Start with: lms server start --keep-alive 10m")
print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": msg}}))
PY
    exit 0
}

# Pass the raw LM Studio body to python via stdin (NOT substituted into a literal),
# and do NOT mask a parse failure with 2>/dev/null — a malformed body must surface.
MISSING=$(printf '%s' "$RESPONSE" | ROUTER_MODEL="$ROUTER_MODEL" EMBED_MODEL="$EMBED_MODEL" python3 -c "
import json, os, sys
required = [os.environ['ROUTER_MODEL'], os.environ['EMBED_MODEL']]
ids = [m['id'] for m in json.loads(sys.stdin.read()).get('data', [])]
missing = [r for r in required if r not in ids]
print(', '.join(missing))
")

if [ -n "$MISSING" ]; then
    emit_heartbeat "$HOOK_NAME" "$EVENT" "warn" "$(($(ms_now) - START_MS))"
    python3 - "$MISSING" <<'PY'
import json, sys
missing = sys.argv[1]
msg = (f"[router-health-check] WARNING: LM Studio reachable but missing models: {missing}. "
       "Load them in LM Studio before Phase E router runs.")
print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": msg}}))
PY
    exit 0
fi

emit_heartbeat "$HOOK_NAME" "$EVENT" "allow" "$(($(ms_now) - START_MS))"
exit 0
