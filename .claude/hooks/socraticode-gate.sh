#!/bin/bash
# PreToolUse hook: two enforcement modes.
#
# Mode 1 (existing): blocks grep/rg/find/ack/ag at command position
# unless a SocratiCode discovery tool fired earlier in this session.
#
# Mode 2 (new): blocks Read on paths under app/, ingestion/src/, models/,
# docs/features/, or .claude/agents/ unless a SocratiCode discovery tool
# has fired in the session. Exception: paths explicitly cited in the task brief
# (passed via CLAUDE_TASK_DESCRIPTION env var or tool_input.description).
#
# Enforces CONSTITUTION Article III + CONTRACT Rule 2.
#
# Env vars (with defaults):
#   REPO_ROOT            — absolute path to project root (default: cwd)

set -e

# Source env if present
HOOKS_DIR="$(cd "$(dirname "$0")" && pwd)"
if [ -f "$HOOKS_DIR/.env" ]; then
  # shellcheck disable=SC1091
  source "$HOOKS_DIR/.env"
fi

INPUT=$(cat)
CMD=$(printf '%s' "$INPUT" | jq -r '.tool_input.command // ""' 2>/dev/null || echo "")
TOOL_NAME=$(printf '%s' "$INPUT" | jq -r '.tool_name // ""' 2>/dev/null || echo "")
READ_PATH=$(printf '%s' "$INPUT" | jq -r '.tool_input.file_path // ""' 2>/dev/null || echo "")
SID=$(printf '%s' "$INPUT" | jq -r '.session_id // "unknown"' 2>/dev/null || echo unknown)
FLAG="${TMPDIR:-/tmp}/claude-socraticode-${SID}.flag"

# ── Mode 2: Read-block on unexplored paths ────────────────────────────────────
if [ "$TOOL_NAME" = "Read" ] && [ -n "$READ_PATH" ] && [ ! -f "$FLAG" ]; then
  BLOCK=$(python3 - <<'PY' "$READ_PATH"
import sys, os, re

path = sys.argv[1] if len(sys.argv) > 1 else ""
if not path:
    print("0"); sys.exit(0)

WATCHED_PREFIXES = (
    "/app/",
    "/ingestion/src/",
    "/models/",
    "/docs/features/",
    "/.claude/agents/",
)

# Normalize: strip repo prefix if present, ensure leading slash.
REPO = os.environ.get("REPO_ROOT", os.getcwd())
rel = path.replace(REPO, "")
if not rel.startswith("/"):
    rel = "/" + rel

if not any(rel.startswith(p) for p in WATCHED_PREFIXES):
    print("0"); sys.exit(0)

# Exception: check if the path appears verbatim in the task brief.
brief = os.environ.get("CLAUDE_TASK_DESCRIPTION", "")
# Also check the hook's own tool_input.description if available.
tool_desc = os.environ.get("_HOOK_TOOL_DESC", "")
combined = brief + " " + tool_desc

# Escape for literal substring match (not regex).
basename = path.rsplit("/", 1)[-1]
if basename in combined or path in combined or rel in combined:
    print("0"); sys.exit(0)

print("1")
PY
  )

  if [ "$BLOCK" = "1" ]; then
    REASON=$(printf '[socraticode-gate] BLOCK — Read of %s requires a prior codebase_search or codebase_symbol. Run mcp__plugin_socraticode_socraticode__codebase_search first, then retry. Exception: file paths already cited explicitly in your task brief.' "$READ_PATH")
    jq -n --arg r "$REASON" '{
      hookSpecificOutput: {
        hookEventName: "PreToolUse",
        permissionDecision: "deny",
        permissionDecisionReason: $r
      }
    }'
    exit 0
  fi
fi

is_violation=$(python3 - <<'PY' "$CMD"
import re, shlex, sys

cmd = sys.argv[1] if len(sys.argv) > 1 else ""
if not cmd.strip():
    print("0"); sys.exit(0)

BANNED = {"grep", "rg", "find", "ack", "ag", "fgrep", "egrep"}
WRAPPERS = {"rtk", "sudo", "env", "time", "nice", "ionice", "exec", "command", "builtin"}

def strip_heredocs(s: str) -> str:
    """Replace heredoc bodies with empty string so we don't treat their
    contents as commands. Handles `<<DELIM`, `<<-DELIM`, quoted delimiters."""
    out = []
    lines = s.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        out.append(line)
        m = re.search(r"<<-?\s*([\"']?)([A-Za-z_][A-Za-z0-9_]*)\1", line)
        if m:
            delim = m.group(2)
            strip_tabs = "<<-" in line
            j = i + 1
            while j < len(lines):
                test = lines[j].lstrip("\t") if strip_tabs else lines[j]
                if test == delim:
                    out.append(lines[j])
                    break
                j += 1
            i = j + 1
            continue
        i += 1
    return "\n".join(out)

def expand_subshells(s: str) -> list:
    """Return list of (segment_text) including each $(...) body, ${...} is
    parameter expansion not subshell so leave it alone, backtick `...` is
    a subshell. We yield the outer command (with subshell text replaced by
    a placeholder) and each subshell body as its own segment."""
    parts = []
    cur = []
    i, n = 0, len(s)
    in_single = in_double = False
    while i < n:
        ch = s[i]
        if ch == "\\" and i + 1 < n:
            cur.append(ch); cur.append(s[i+1]); i += 2; continue
        if ch == "'" and not in_double:
            in_single = not in_single
            cur.append(ch); i += 1; continue
        if ch == '"' and not in_single:
            in_double = not in_double
            cur.append(ch); i += 1; continue
        if not in_single:
            # $( ... )
            if ch == "$" and i + 1 < n and s[i+1] == "(" and (i + 2 >= n or s[i+2] != "("):
                depth = 1
                j = i + 2
                ssi = j
                in_s2 = in_d2 = False
                while j < n and depth > 0:
                    c = s[j]
                    if c == "\\" and j + 1 < n:
                        j += 2; continue
                    if c == "'" and not in_d2:
                        in_s2 = not in_s2; j += 1; continue
                    if c == '"' and not in_s2:
                        in_d2 = not in_d2; j += 1; continue
                    if not in_s2 and not in_d2:
                        if c == "(":
                            depth += 1
                        elif c == ")":
                            depth -= 1
                            if depth == 0:
                                parts.append(s[ssi:j])
                                cur.append("(_SUBSHELL_)")
                                j += 1
                                break
                    j += 1
                i = j
                continue
            # Backtick
            if ch == "`":
                j = i + 1
                ssi = j
                while j < n and s[j] != "`":
                    if s[j] == "\\" and j + 1 < n:
                        j += 2; continue
                    j += 1
                if j < n:
                    parts.append(s[ssi:j])
                    cur.append("(_SUBSHELL_)")
                    i = j + 1
                    continue
        cur.append(ch); i += 1
    parts.insert(0, "".join(cur))
    return parts

def split_top_level_segments(s: str) -> list:
    """Split on top-level ;, |, &, &&, ||, ;;, newline — respecting quotes
    and parens. Excludes subshells (already handled separately)."""
    segs, cur = [], []
    i, n = 0, len(s)
    in_single = in_double = False
    paren = 0
    while i < n:
        ch = s[i]
        if ch == "\\" and i + 1 < n:
            cur.append(ch); cur.append(s[i+1]); i += 2; continue
        if ch == "'" and not in_double:
            in_single = not in_single
            cur.append(ch); i += 1; continue
        if ch == '"' and not in_single:
            in_double = not in_double
            cur.append(ch); i += 1; continue
        if not in_single and not in_double:
            if ch == "(":
                paren += 1; cur.append(ch); i += 1; continue
            if ch == ")":
                paren = max(0, paren - 1); cur.append(ch); i += 1; continue
            if paren == 0:
                two = s[i:i+2]
                if two in ("&&", "||", ";;"):
                    segs.append("".join(cur)); cur = []; i += 2; continue
                if ch in (";", "|", "&", "\n"):
                    segs.append("".join(cur)); cur = []; i += 1; continue
        cur.append(ch); i += 1
    if cur:
        segs.append("".join(cur))
    return segs

def is_banned_command(segment: str) -> bool:
    seg = segment.strip().lstrip("(").strip()
    if not seg:
        return False
    try:
        tokens = shlex.split(seg, comments=False, posix=True)
    except ValueError:
        return False
    if not tokens:
        return False
    # Skip env-var assignments at start (FOO=bar BAR=baz cmd)
    idx = 0
    while idx < len(tokens) and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tokens[idx]):
        idx += 1
    if idx >= len(tokens):
        return False
    head = tokens[idx]
    head_base = head.rsplit("/", 1)[-1]
    # Skip past placeholder for handled subshells
    if head_base == "(_SUBSHELL_)":
        return False
    # Unwrap a single wrapper
    if head_base in WRAPPERS and idx + 1 < len(tokens):
        head_base = tokens[idx + 1].rsplit("/", 1)[-1]
    return head_base in BANNED

cmd = strip_heredocs(cmd)
parts = expand_subshells(cmd)  # parts[0] is outer; rest are subshell bodies
for part in parts:
    for seg in split_top_level_segments(part):
        if is_banned_command(seg):
            print("1"); sys.exit(0)

print("0")
PY
)

if [ "$is_violation" = "1" ] && [ ! -f "$FLAG" ]; then
  REASON=$(printf 'SocratiCode-first rule violation (CONSTITUTION Article III + CONTRACT Rule 2). grep/rg/find/ack/ag/fgrep/egrep at command position requires a prior SocratiCode discovery call in this session. Run mcp__plugin_socraticode_socraticode__codebase_search (or codebase_symbol / codebase_graph_query / codebase_impact) first. After that, search commands are permitted for the rest of the session.\n\nBlocked command: %s' "$CMD")
  jq -n --arg r "$REASON" '{
    hookSpecificOutput: {
      hookEventName: "PreToolUse",
      permissionDecision: "deny",
      permissionDecisionReason: $r
    }
  }'
fi

exit 0
