#!/bin/bash
# PostToolUse hook: sets a session-scoped flag when a SocratiCode discovery
# tool fires. The flag tells socraticode-gate.sh that grep/rg/find is now
# permitted for the remainder of this session.
#
# Wired via .claude/settings.json hooks.PostToolUse matcher on the SocratiCode
# MCP tool names.

set -e

INPUT=$(cat)
SID=$(printf '%s' "$INPUT" | jq -r '.session_id // "unknown"' 2>/dev/null || echo unknown)
FLAG="${TMPDIR:-/tmp}/claude-socraticode-${SID}.flag"

touch "$FLAG"
exit 0
