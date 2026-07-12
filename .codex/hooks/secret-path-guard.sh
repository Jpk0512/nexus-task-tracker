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
    exit 0
fi

REASON="[GATE:SECRET-PATH/WRITE-DENIED] Write to secret/credential file '${RESULT}' is blocked. Secret files (.env, .env.*, *.pem, *.key, id_rsa, id_ed25519, secrets.*, etc.) must never be written by an AI agent. Use environment variables, a secrets manager, or ask the user to write this file manually."
jq -cn --arg r "$REASON" \
    '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":$r}}'
printf '%s\n' "$REASON" >&2
exit 2
