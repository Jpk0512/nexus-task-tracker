#!/usr/bin/env bash
# PreToolUse hook: block sub-agents from pushing directly to main.
#
# Enforces CLAUDE.md Worktree Protocol: feature work must land on feat/<slug>
# and go through a PR. Only the nexus-orchestrator (or the user) may push to main.
#
# Detection patterns (any match triggers evaluation):
#   - git push.*\bmain\b
#   - git push.*--force.*main  (force-push variant)
#   - git push.*\borigin main\b
#   - git checkout main.*&&.*push  (chained)
#
# Allow conditions (any one is sufficient):
#   - CLAUDE_AGENT_TYPE is "nexus-orchestrator" or unset/empty (user session)
#   - Command contains the bypass token: # BYPASS:USER-APPROVED-PUSH-TO-MAIN
#
# Exit code 2 on block (hard deny). Exit 0 on allow.

set -euo pipefail

INPUT=$(cat)
CMD=$(printf '%s' "$INPUT" | jq -r '.tool_input.command // ""' 2>/dev/null || true)
AGENT_TYPE="${CLAUDE_AGENT_TYPE:-}"

# Nothing to evaluate — pass through.
if [ -z "$CMD" ]; then
    exit 0
fi

# ── Step 1: does the command touch main via push? ─────────────────────────────
is_push_to_main=$(python3 - <<'PY' "$CMD"
import re, sys

cmd = sys.argv[1] if len(sys.argv) > 1 else ""

PATTERNS = [
    r'git\s+push\b.*\bmain\b',
    r'git\s+push\b.*--force\b.*\bmain\b',
    r'git\s+push\b.*\borigin\s+main\b',
    r'git\s+checkout\s+main\b.*&&.*\bgit\s+push\b',
]

for pat in PATTERNS:
    if re.search(pat, cmd):
        print("1")
        sys.exit(0)

print("0")
PY
)

if [ "$is_push_to_main" != "1" ]; then
    exit 0
fi

# ── Step 2: bypass token present? ────────────────────────────────────────────
if printf '%s' "$CMD" | grep -qF '# BYPASS:USER-APPROVED-PUSH-TO-MAIN'; then
    exit 0
fi

# ── Step 3: is caller nexus-orchestrator or the user (unset)? ────────────────
if [ -z "$AGENT_TYPE" ] || [ "$AGENT_TYPE" = "nexus-orchestrator" ]; then
    exit 0
fi

# ── Block ─────────────────────────────────────────────────────────────────────
MSG="[no-direct-push-to-main] BLOCK — Sub-agents must NOT push to main (CLAUDE.md Worktree Protocol). Push to your feature branch instead, then the orchestrator opens a PR. If this push is user-authorized, include the bypass token in your command."

jq -n --arg msg "$MSG" '{
    hookSpecificOutput: {
        hookEventName: "PreToolUse",
        permissionDecision: "deny",
        permissionDecisionReason: $msg
    }
}' >&1

printf '%s\n' "$MSG" >&2

exit 2
