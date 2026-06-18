#!/bin/bash
# PostToolUse hook: sets a session-scoped flag ONLY when a SocratiCode discovery
# tool actually RETURNS indexed results. The flag tells socraticode-gate.sh that
# grep/rg/find is now permitted for the remainder of this session.
#
# Contract (CLAUDE.md "Codebase Search"): the grep gate opens only after a
# discovery call that RETURNS indexed results. A call that errors, returns
# "No ... matching", or returns an empty/unindexed response must NOT open the
# gate. Fail-safe: if results cannot be CONFIRMED, the flag is NOT set (the gate
# stays closed) — a false-open would let grep run before the index is warm,
# which is the security-relevant failure mode this hook exists to prevent.
#
# Wired via .claude/settings.json hooks.PostToolUse matcher on the SocratiCode
# discovery MCP tool names (codebase_symbol, codebase_symbols,
# codebase_search, codebase_context_search, codebase_graph_query,
# codebase_impact, codebase_flow).

set -e

INPUT=$(cat)
SID=$(printf '%s' "$INPUT" | jq -r '.session_id // "unknown"' 2>/dev/null || echo unknown)
TOOL_NAME=$(printf '%s' "$INPUT" | jq -r '.tool_name // ""' 2>/dev/null || echo "")
FLAG="${TMPDIR:-/tmp}/claude-socraticode-${SID}.flag"

# Resolve the project root for the imperative instruction block.
# Priority: tool_input.projectPath -> git rev-parse from $PWD -> $PWD as last resort.
# $PWD is the project root when Claude Code invokes this hook (the command is
# wired as "./.claude/hooks/socraticode-flag.sh" relative to the project root).
PROJECT_ROOT=$(printf '%s' "$INPUT" | jq -r '.tool_input.projectPath // ""' 2>/dev/null || true)
if [ -z "$PROJECT_ROOT" ]; then
  PROJECT_ROOT=$(git -C "$PWD" rev-parse --show-toplevel 2>/dev/null || echo "")
fi
if [ -z "$PROJECT_ROOT" ]; then
  PROJECT_ROOT="$PWD"
fi

# Decide whether the tool_response actually carries indexed results. Print "1"
# only when results are CONFIRMED; print "0" otherwise (fail-safe default).
# Also prints "unindexed" when the response signals the project is not indexed.
VERDICT=$(printf '%s' "$INPUT" | python3 -c '
import json, re, sys

def collect_text(node, out):
    """Flatten any string content reachable in a tool_response into out."""
    if isinstance(node, str):
        out.append(node)
    elif isinstance(node, dict):
        # MCP content blocks: {"type":"text","text":"..."}
        if isinstance(node.get("text"), str):
            out.append(node["text"])
        for k, v in node.items():
            if k == "text":
                continue
            collect_text(v, out)
    elif isinstance(node, list):
        for item in node:
            collect_text(item, out)

try:
    d = json.load(sys.stdin)
except Exception:
    print("none"); sys.exit(0)

resp = d.get("tool_response", d.get("tool_result", d.get("output", "")))
parts = []
collect_text(resp, parts)
text = "\n".join(p for p in parts if p)
low = text.lower().strip()

# Empty response -> no results -> gate stays closed.
if not low:
    print("none"); sys.exit(0)

# Unindexed signals -> never open the gate; bash side emits imperative instruction.
UNINDEXED = (
    "no context artifacts configured",
    "no context artifacts",
    "not indexed",
    "project not indexed",
    "no index found",
    "index not found",
    "please index",
    "index this project",
    "run codebase_index",
    "run mcp__plugin_socraticode_socraticode__codebase_index",
    "no graph built",
    "graph not built",
    "run codebase_graph_build",
    "create a .socraticodecontextartifacts.json",
)
if any(s in low for s in UNINDEXED):
    print("unindexed"); sys.exit(0)

# Explicit negative / error / unindexed signals -> never open the gate.
NEGATIVES = (
    "no symbols matching",
    "no matches",
    "no results",
    "no matching",
    "0 results",
    "(0)",
    "found 0",
    "nothing found",
    "no symbols found",
)
if any(s in low for s in NEGATIVES):
    print("none"); sys.exit(0)

# An error-shaped response must not open the gate.
if isinstance(resp, dict) and (resp.get("isError") or resp.get("is_error") or resp.get("error")):
    print("none"); sys.exit(0)
if low.startswith("error") or "traceback (most recent call last)" in low or "mcp error" in low:
    print("none"); sys.exit(0)

# Positive: a "matching ... (N):" / "matches (N)" header with N >= 1.
# Examples: "Symbols matching '\''read_state'\'' (3):", "Matches (12):".
for m in re.finditer(r"\((\d+)\)", text):
    if int(m.group(1)) >= 1:
        print("1"); sys.exit(0)

# Positive: explicit "found N" with N >= 1.
m = re.search(r"found\s+(\d+)\b", low)
if m and int(m.group(1)) >= 1:
    print("1"); sys.exit(0)

# Positive: result rows. Discovery tools render hits as indented / bulleted /
# location-bearing lines (e.g. "  - foo  (path/to/file.py:42)"). A response with
# such rows and no negative signal is a confirmed result set.
for line in text.splitlines():
    s = line.strip()
    if not s:
        continue
    if re.search(r":\d+", s):              # path:line location
        print("1"); sys.exit(0)
    if s[0] in "-*\xe2\x80\xa2" and len(s) > 2:  # bullet row with content
        print("1"); sys.exit(0)

# Could not confirm results -> fail-safe: leave the gate closed.
print("none")
' 2>/dev/null || echo "none")

case "$VERDICT" in
  1)
    # Real indexed result — open the gate.
    touch "$FLAG"
    ;;
  unindexed)
    # Do NOT touch the flag. Emit a system reminder with exact tool calls.
    REASON=$(printf '[socraticode-flag] %s returned an unindexed response. The grep/rg/find gate will DENY until the index is warm. Do NOT fall back to grep.\n\nRun these steps in order:\n  1. mcp__plugin_socraticode_socraticode__codebase_index(projectPath="%s")\n  2. Poll mcp__plugin_socraticode_socraticode__codebase_status(projectPath="%s") until progress reaches 100%%\n  3. Re-run the original discovery call that triggered this message\n\nFor graph queries use codebase_graph_build instead of step 1; for context-artifact queries use codebase_context_index instead of step 1.' \
      "$TOOL_NAME" "$PROJECT_ROOT" "$PROJECT_ROOT")
    jq -n --arg r "$REASON" '{
      hookSpecificOutput: {
        hookEventName: "PostToolUse",
        additionalContext: $r
      }
    }'
    ;;
  *)
    # Fail-safe: no confirmed results — gate stays closed, no output.
    ;;
esac
exit 0
