#!/bin/bash
# PreToolUse hook: two enforcement modes.
#
# Mode 1 (existing): blocks grep/rg/find/ack/ag/fgrep/egrep at command position
# unless a SocratiCode discovery tool fired earlier in this session.
#
# Mode 2 (new): blocks Read on paths under app/, ingestion/src/, models/,
# docs/features/, or .claude/agents/ unless a SocratiCode discovery tool
# has fired in the session. Exception: paths explicitly cited in the task brief
# (passed via CLAUDE_TASK_DESCRIPTION env var or tool_input.description).
#
# Read-only-persona exemption (DEC-027): non-code-writing actors —
# orchestrator (plexus/nexus) + scout + lens + lens-fast + palette — are EXEMPT
# from BOTH modes (free grep + free Read). The impact-before-mutation rationale
# the gate enforces lives at the implementer tier; recon/orchestrator personas
# never mutate code, so the gate is pure ceremony for them. Code-writing personas
# (forge-*, pipeline-*, atlas, hermes, quill-*, *-pro) remain fully gated.
#
# Enforces CONSTITUTION Article III + CONTRACT Rule 2.

set -e

INPUT=$(cat)
CMD=$(printf '%s' "$INPUT" | jq -r '.tool_input.command // ""' 2>/dev/null || echo "")
TOOL_NAME=$(printf '%s' "$INPUT" | jq -r '.tool_name // ""' 2>/dev/null || echo "")
READ_PATH=$(printf '%s' "$INPUT" | jq -r '.tool_input.file_path // ""' 2>/dev/null || echo "")
SID=$(printf '%s' "$INPUT" | jq -r '.session_id // "unknown"' 2>/dev/null || echo unknown)
FLAG="${TMPDIR:-/tmp}/claude-socraticode-${SID}.flag"

# ── Persona-aware remediation (OPT-032) ───────────────────────────────────────
# The gate opens after ANY well-formed SocratiCode discovery call that RETURNS
# results (codebase_symbol / codebase_symbols / codebase_graph_query /
# codebase_impact / codebase_flow / codebase_context_search / codebase_search —
# see the socraticode-flag.sh PostToolUse matcher). The deny message MUST name a
# call the CURRENT actor is actually allowed to run. The orchestrator persona has
# codebase_search in disallowedTools, so pointing it there creates an unbreakable
# block loop — emit the persona-allowed openers verbatim instead. The param is
# name= / query= (NOT symbolName=), which is the most common opener mistake.
AGENT_TYPE="${CLAUDE_AGENT_TYPE:-}"
case "$AGENT_TYPE" in
  *orchestrator*|plexus|nexus)
    # codebase_search is DENIED to orchestrator personas — never name it here.
    OPENER_HINT='Open the gate with a SocratiCode discovery call you are allowed to run (NOT codebase_search — that is denied to the orchestrator). Copy-paste one of: codebase_symbol(name="<bareSymbol>")  or  codebase_symbols(query="<text>")  (also: codebase_graph_query / codebase_impact). The call must RETURN results to open the gate; param is name= / query= (NOT symbolName=).'
    ;;
  *)
    OPENER_HINT='Open the gate with a SocratiCode discovery call that RETURNS results. Copy-paste one of: codebase_symbol(name="<bareSymbol>")  or  codebase_symbols(query="<text>")  (also allowed: codebase_search / codebase_graph_query / codebase_impact). Param is name= / query= (NOT symbolName=).'
    ;;
esac

# ── Read-only-persona exemption (DEC-027) ─────────────────────────────────────
# Non-code-writing actors (orchestrator/scout/lens/lens-fast/palette) short-circuit
# BOTH modes: they never mutate code, so the SocratiCode-before-grep/Read gate is
# ceremony for them. Code-writing personas fall through to the block logic below.
#
# Empty/unset CLAUDE_AGENT_TYPE means the TOP-LEVEL ORCHESTRATOR LOOP — the harness
# only sets CLAUDE_AGENT_TYPE for Task-spawned sub-agents; the orchestrator session
# itself has it unset. Exempt it here so the orchestrator's own grep/Read is never
# blocked by this gate (DEC-027 extension, NATIVE-27-2).
case "$AGENT_TYPE" in
  ""|*orchestrator*|plexus|nexus|scout|lens|lens-fast|palette)
    exit 0 ;;
esac

# ── Mode 2: Read-block on unexplored paths ────────────────────────────────────
if [ "$TOOL_NAME" = "Read" ] && [ -n "$READ_PATH" ] && [ ! -f "$FLAG" ]; then
  BLOCK=$(python3 - <<'PY' "$READ_PATH"
import sys, os, re

path = sys.argv[1] if len(sys.argv) > 1 else ""
if not path:
    print("0"); sys.exit(0)

# Install-time substitution renders /app/apps/, /app/packages/ / /Users/john.keeney/nexus-task-tracker.
# Tests (and a sanity check) can override via _HOOK_* env vars. If the token is
# still literal at runtime the install step was skipped: a security gate must
# NOT silently fail open, so a watched-looking path is DENIED loud (print "2").
WATCHED_RAW = os.environ.get("_HOOK_WATCHED_PREFIXES", "/app/apps/, /app/packages/")
REPO = os.environ.get("_HOOK_INSTALL_ROOT", "/Users/john.keeney/nexus-task-tracker")

WATCHED_PREFIXES = tuple(p.strip() for p in WATCHED_RAW.split(",") if p.strip())

# Normalize: guarded prefix strip, then ensure leading slash.
# Only strip the repo prefix when the path actually starts with it — avoids
# mangling unrelated paths that happen to contain the repo string elsewhere.
if not REPO.startswith("__") and path.startswith(REPO):
    rel = path[len(REPO):]
else:
    rel = path
if not rel.startswith("/"):
    rel = "/" + rel

if WATCHED_RAW.startswith("__") and WATCHED_RAW.endswith("__"):
    # Unrendered token — fail CLOSED rather than open-silent.
    print("2"); sys.exit(0)

if not WATCHED_PREFIXES:
    # Rendered but EMPTY (e.g. socraticode_watched_prefixes detected no dirs, so
    # /app/apps/, /app/packages/ rendered to ""). An empty prefix tuple makes the
    # any()-match below VACUOUSLY False for every path → the gate would silently
    # fail OPEN. A security gate must fail CLOSED on misconfig: deny LOUD.
    print("3"); sys.exit(0)

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

  if [ "$BLOCK" = "1" ] || [ "$BLOCK" = "2" ] || [ "$BLOCK" = "3" ]; then
    if [ "$BLOCK" = "2" ]; then
      REASON=$(printf '[socraticode-gate] BLOCK — Read of %s is denied because the install-time /app/apps/, /app/packages/ token was never rendered; a security gate fails CLOSED rather than open-silent. Re-run the Nexus install/render step (or set _HOOK_WATCHED_PREFIXES) so the gate knows which paths to guard, then retry.' "$READ_PATH")
    elif [ "$BLOCK" = "3" ]; then
      REASON=$(printf '[socraticode-gate] BLOCK — watched_prefixes is empty — gate misconfigured, failing closed. Read of %s is denied because the rendered watched-prefix list is EMPTY (socraticode_watched_prefixes detected no directories). An empty list would make the gate match nothing and silently fail OPEN. Fix the stack profile so socraticode_watched_prefixes is non-empty (or set _HOOK_WATCHED_PREFIXES), then retry.' "$READ_PATH")
    else
      REASON=$(printf '[socraticode-gate] BLOCK — Read of %s requires a prior SocratiCode discovery call. %s Exception: file paths already cited explicitly in your task brief.' "$READ_PATH" "$OPENER_HINT")
    fi
    jq -n --arg r "$REASON" '{
      hookSpecificOutput: {
        hookEventName: "PreToolUse",
        permissionDecision: "deny",
        permissionDecisionReason: $r
      }
    }'
    # Durable backstop: a security gate must not rely on the JSON channel alone.
    # exit 2 hard-blocks even if the harness ignores hookSpecificOutput.
    exit 2
  fi
fi

if [ ! -f "$FLAG" ]; then
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
    head_base = tokens[idx].rsplit("/", 1)[-1]
    # Skip past placeholder for handled subshells
    if head_base == "(_SUBSHELL_)":
        return False
    # Peel all leading wrappers (sudo env grep foo, rtk sudo grep ..., etc.)
    # Re-skip VAR=val assignments after each peel (env may carry FOO=bar).
    while head_base in WRAPPERS:
        idx += 1
        while idx < len(tokens) and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tokens[idx]):
            idx += 1
        if idx >= len(tokens):
            return False
        head_base = tokens[idx].rsplit("/", 1)[-1]
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
else
  is_violation=0
fi

if [ "$is_violation" = "1" ] && [ ! -f "$FLAG" ]; then
  REASON=$(printf 'SocratiCode-first rule violation (CONSTITUTION Article III + CONTRACT Rule 2). grep/rg/find/ack/ag/fgrep/egrep at command position requires a prior SocratiCode discovery call in this session. %s After that, search commands are permitted for the rest of the session.\n\nBlocked command: %s' "$OPENER_HINT" "$CMD")
  jq -n --arg r "$REASON" '{
    hookSpecificOutput: {
      hookEventName: "PreToolUse",
      permissionDecision: "deny",
      permissionDecisionReason: $r
    }
  }'
  # Durable backstop: a security gate must not rely on the JSON channel alone.
  # exit 2 hard-blocks even if the harness ignores hookSpecificOutput.
  exit 2
fi

exit 0
