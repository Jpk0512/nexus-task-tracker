#!/usr/bin/env bash
# PreToolUse hook: deny writes to secret / credential files BEFORE they land.
#
# Covers Write, Edit, MultiEdit, NotebookEdit.
# Write carries a top-level `file_path` field.
# Edit carries a top-level `file_path` OR `path` field.
# MultiEdit carries `edits[].file_path` (array of objects).
# NotebookEdit carries a top-level `notebook_path`.
#
# On any match: emit canonical deny JSON (exit 2).
# On no match: silent exit 0.

set -euo pipefail

HOOKS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=heartbeat-emitter.sh
# set -e (bash 3.2 on macOS) treats a failed `source` of a
# missing file as fatal even inside `|| { ... }` — guard with an
# explicit -f test instead so a missing heartbeat-emitter.sh never
# aborts the gate (best-effort telemetry must never break allow/deny).
if [ -f "${HOOKS_DIR}/heartbeat-emitter.sh" ]; then
    # shellcheck source=heartbeat-emitter.sh
    source "${HOOKS_DIR}/heartbeat-emitter.sh" 2>/dev/null || true
fi
# Belt-and-suspenders: even if the source succeeded but the file did not define
# both helpers (truncated/edited), guarantee they exist before first use.
command -v ms_now >/dev/null 2>&1 || ms_now() { python3 -c "import time; print(int(time.time()*1000))" 2>/dev/null || echo 0; }
command -v emit_heartbeat >/dev/null 2>&1 || emit_heartbeat() { :; }

_HB_START_MS=$(ms_now 2>/dev/null || echo 0)
_hb() {
  local decision="$1"
  local _elapsed=$(( $(ms_now 2>/dev/null || echo 0) - _HB_START_MS ))
  emit_heartbeat "secret-path-guard" "PreToolUse" "$decision" "$_elapsed" 2>/dev/null || true
}

INPUT=$(cat)

# ── Extract paths + check deny-list in one Python call ────────────────────────
# INPUT is passed via env var to avoid the two-heredoc stdin conflict.
# Prints the first matched secret path, or nothing on allow.
RESULT=$(HOOK_INPUT="$INPUT" python3 - <<'PY'
import json, os, sys, fnmatch

DENY_PATTERNS = [
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "id_rsa",
    "id_rsa.*",
    "id_ed25519",
    "id_ed25519.*",
    "id_ecdsa",
    "id_ecdsa.*",
    "*.p12",
    "*.pfx",
    "secrets.*",
    ".netrc",
    ".npmrc",
    "*.jks",
    "*.keystore",
]

raw = os.environ.get("HOOK_INPUT", "")
try:
    payload = json.loads(raw)
except Exception:
    sys.exit(0)

tool_input = payload.get("tool_input", {})
paths = []

# Write: top-level "file_path"
for key in ("file_path", "path", "notebook_path"):
    val = tool_input.get(key)
    if val:
        paths.append(val)

# MultiEdit: edits[].file_path
for edit in tool_input.get("edits", []):
    if isinstance(edit, dict) and "file_path" in edit:
        paths.append(edit["file_path"])

for raw_path in paths:
    if not raw_path:
        continue
    name = os.path.basename(raw_path.rstrip("/"))
    for pat in DENY_PATTERNS:
        if fnmatch.fnmatch(name, pat):
            print(raw_path)
            sys.exit(0)
PY
)

if [ -z "$RESULT" ]; then
    _hb allow
    exit 0
fi

REASON="[GATE:SECRET-PATH/WRITE-DENIED] Write to secret/credential file '${RESULT}' is blocked. Secret files (.env, .env.*, *.pem, *.key, id_rsa, id_ed25519, secrets.*, etc.) must never be written by an AI agent. Use environment variables, a secrets manager, or ask the user to write this file manually."
jq -cn --arg r "$REASON" \
    '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":$r}}'
printf '%s\n' "$REASON" >&2
_hb deny
# OPT-033-style best-effort telemetry — mirrors the canonical gate_deny() sink
# write exactly (same schema/path resolution), inlined here (not sourced) since
# this hook stays standalone-safe. Must NOT change exit/stdout/stderr.
{
    _sink="${NEXUS_GATE_BLOCKS_PATH:-}"
    if [ -z "$_sink" ]; then
        _sink="$(cd "${HOOKS_DIR}/../.." && pwd)/.memory/files/gate_blocks.jsonl"
    fi
    _ts="$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || echo "")"
    _reason_trunc="${REASON:0:200}"
    mkdir -p "$(dirname "$_sink")" 2>/dev/null
    jq -cn \
        --arg ts "$_ts" \
        --arg ev "PreToolUse" \
        --arg hk "secret-path-guard" \
        --arg cd "WRITE-DENIED" \
        --arg rs "$_reason_trunc" \
        '{"ts":$ts,"event":$ev,"hook":$hk,"code":$cd,"reason":$rs}' \
        >> "$_sink" 2>/dev/null
} 2>/dev/null || true
exit 2
