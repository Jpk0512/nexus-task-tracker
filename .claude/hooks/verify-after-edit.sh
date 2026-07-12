#!/bin/bash
# Verify-after-edit hook: runs tsc / ruff on changed TS/PY files and injects
# results as additionalContext.
#
# DUAL ENTRY:
#   (1) PostToolUse on Write|Edit|MultiEdit — runs against a single file from
#       .tool_input.file_path. Inform-only (mid-edit feedback).
#   (2) SubagentStop — defensive fallback: parses files_changed from the
#       sub-agent's response JSON and runs the check on every relevant file.
#       This catches the case where sub-agent Edit events don't bubble up
#       to the parent's PostToolUse (harness-version-specific behavior).
#       Also enforces the files_changed-vs-declared-scope invariant: when the
#       agent's files_changed include paths outside the brief's context_files
#       declared scope, a hard REVISE block is emitted (exit 2) before the
#       advisory lint pass runs.
#
#       TURN-SCOPING (TASK-049): the checked set is the agent-reported
#       files_changed INTERSECTED with the actual working-tree delta
#       (`git diff --name-only HEAD` UNION `git ls-files --others
#       --exclude-standard`). Neither signal alone is enough — files_changed
#       alone trusts a self-report with no floor, and the working-tree delta
#       alone can't tell a file created THIS turn from stale untracked cruft
#       left over from an earlier turn (both show as `??` in git status).
#       Intersecting the two means: a brand-new file the agent reports AND
#       that is actually untracked -> checked; a pre-existing untracked file
#       the agent never mentions -> excluded, even though `git status` still
#       lists it. Fail-open when PROJECT_ROOT isn't a git worktree: trust
#       files_changed as-is rather than silently checking nothing.
#
# Wired via .claude/settings.json hooks.PostToolUse "Write|Edit|MultiEdit"
# AND hooks.SubagentStop "".
#
# Skips: files under .claude/ or .memory/ (orchestration scaffolding).
# PostToolUse path: inform-only (exit 0 always).
# SubagentStop path: scope assertion is DENY-capable (exit 2 on violation).

set -e

INPUT=$(cat)

EVENT=$(printf '%s' "$INPUT" | jq -r '.hook_event_name // .event // ""' 2>/dev/null)

# The install-time token is overridable via env so the hook is testable and
# degrades LOUDLY (not silently) if the token was never rendered.
PROJECT_ROOT="${_HOOK_INSTALL_ROOT:-/Users/john.keeney/nexus-task-tracker}"
TS_CHECK_DIR="app/apps/dashboard"
PY_CHECK_DIR=""
INGESTION_DIR=""
# When the profile has no Python backend, py_check_dir is null → renders empty;
# fall back to the ingestion dir so the python check still runs against real code.
PY_CHECK_DIR="${PY_CHECK_DIR:-$INGESTION_DIR}"

# Unrendered install token → fail-open-silent: with PROJECT_ROOT still literal,
# every candidate path is skipped (it can never match "$PROJECT_ROOT"/*) and the
# hook exits 0 with no findings — a dead no-op invisible to the operator. This is
# an ADVISORY hook (never blocks), so "loud" = emit a nested additionalContext
# advisory announcing the inert gate rather than silently doing nothing.
case "$PROJECT_ROOT" in
  __*__)
    event_out=$(printf '%s' "$INPUT" | jq -r '.hook_event_name // .event // "PostToolUse"' 2>/dev/null)
    [ -z "$event_out" ] && event_out="PostToolUse"
    msg='[verify-after-edit] INSTALL NOT RENDERED — the /Users/john.keeney/nexus-task-tracker token was never substituted, so the post-change tsc/ruff check cannot resolve the project root and is INERT (no findings will ever surface). This is advisory-only, so edits are not blocked, but the safety net is OFF. Re-run the Nexus install/render step (or set _HOOK_INSTALL_ROOT) to restore post-edit checks.'
    jq -n --arg r "$msg" --arg ev "$event_out" '{
      hookSpecificOutput: {
        hookEventName: $ev,
        additionalContext: $r
      }
    }'
    exit 0
    ;;
esac

# Collect candidate file paths into the FILES array.
declare -a FILES=()

if [ -z "$EVENT" ] || [ "$EVENT" = "PostToolUse" ]; then
  fp=$(printf '%s' "$INPUT" | jq -r '.tool_input.file_path // .tool_response.filePath // ""' 2>/dev/null)
  if [ -n "$fp" ]; then
    FILES+=("$fp")
  fi
fi

if [ "$EVENT" = "SubagentStop" ] || [ -z "$EVENT" ]; then
  # Best-effort parse of files_changed from the agent's last assistant message
  text=$(printf '%s' "$INPUT" | jq -r '
    .last_assistant_message //
    .response.text //
    .tool_response.text //
    ""
  ' 2>/dev/null)
  reported_paths=""
  if [ -n "$text" ]; then
    reported_paths=$(printf '%s' "$text" | python3 -c '
import sys, re, json
text = sys.stdin.read()
out = []
for block in re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL):
    try:
        obj = json.loads(block)
    except json.JSONDecodeError:
        continue
    fc = obj.get("files_changed")
    if isinstance(fc, list):
        for p in fc:
            if isinstance(p, str):
                out.append(p)
        break
for p in out:
    print(p)
' 2>/dev/null)
  fi

  if [ -n "$reported_paths" ]; then
    is_repo=$(cd "$PROJECT_ROOT" 2>/dev/null && git rev-parse --is-inside-work-tree 2>/dev/null) || true
    if [ "$is_repo" = "true" ]; then
      # THIS TURN's working-tree delta: tracked modifications vs HEAD, union
      # untracked files. Used only to FLOOR the agent's self-report (see
      # TURN-SCOPING note above) — never as the sole source of the file set.
      delta=$(cd "$PROJECT_ROOT" && { git diff --name-only HEAD 2>/dev/null; git ls-files --others --exclude-standard 2>/dev/null; }) || true
      norm_delta=$(printf '%s\n' "$delta" | sed -e 's#^\./##')
      while IFS= read -r p; do
        [ -z "$p" ] && continue
        norm_p="${p#./}"; norm_p="${norm_p#/}"
        if printf '%s\n' "$norm_delta" | grep -qxF "$norm_p"; then
          FILES+=("$p")
        fi
      done <<< "$reported_paths"
    else
      # Not a git worktree — fail open, trust the agent's report as-is.
      while IFS= read -r p; do
        [ -n "$p" ] && FILES+=("$p")
      done <<< "$reported_paths"
    fi
  fi
fi

if [ ${#FILES[@]} -eq 0 ]; then exit 0; fi

# ---------------------------------------------------------------------------
# SCOPE GUARD (SubagentStop only) — files_changed-vs-declared-scope check.
# When the brief's tool_input carries context_files (the declared write scope),
# every file in files_changed must resolve to a path that is covered by (i.e.
# is equal to or under) at least one declared scope entry. A violation emits a
# hard NEXUS:REVISE block (permissionDecision=deny, exit 2).
#
# Fail-open: if context_files is absent or unparseable, no block is emitted
# (the scope is unknown; lint still runs). The deny is reserved for the case
# where scope is EXPLICITLY declared and EXPLICITLY violated.
# ---------------------------------------------------------------------------
if [ "$EVENT" = "SubagentStop" ]; then
  scope_result=$(NEXUS_SCOPE_INPUT="$INPUT" python3 - <<'PYEOF'
import sys, re, json, os

payload = json.loads(os.environ.get("NEXUS_SCOPE_INPUT", "{}"))

# --- 1. Extract files_changed from the response text ---
text = (
    payload.get("last_assistant_message")
    or payload.get("response", {}).get("text")
    or payload.get("tool_response", {}).get("text")
    or ""
)
files_changed = []
for block in re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL):
    try:
        obj = json.loads(block)
    except json.JSONDecodeError:
        continue
    fc = obj.get("files_changed")
    if isinstance(fc, list) and all(isinstance(x, str) for x in fc):
        files_changed = fc
        break

if not files_changed:
    print("OK:NO_FILES_CHANGED")
    sys.exit(0)

# --- 2. Extract context_files (declared scope) from tool_input ---
context_files = (
    payload.get("tool_input", {}).get("context_files")
    or payload.get("context_files")
    or []
)
if not isinstance(context_files, list) or not context_files:
    print("OK:NO_SCOPE_DECLARED")
    sys.exit(0)

# Repo-root wildcard: "." means no restriction — any file is in scope.
if context_files == ["."]:
    print("OK:WILDCARD_SCOPE")
    sys.exit(0)

# --- 3. Normalise: strip leading ./ or / without mangling dotfile prefixes ---
def normalise(f):
    if f.startswith("./"):
        return f[2:]
    if f.startswith("/"):
        return f[1:]
    return f

norm_scope = [normalise(s) for s in context_files]
norm_changed = [(p, normalise(p)) for p in files_changed]

# --- 4. Coverage check ---
violations = []
for (orig, norm) in norm_changed:
    covered = False
    for s in norm_scope:
        if norm == s or norm.startswith(s.rstrip("/") + "/"):
            covered = True
            break
    if not covered:
        violations.append(orig)

if violations:
    detail = json.dumps({"violations": violations, "declared_scope": context_files})
    print("VIOLATION:" + detail)
else:
    print("OK:IN_SCOPE")
PYEOF
  )
  scope_status="${scope_result%%:*}"
  if [ "$scope_status" = "VIOLATION" ]; then
    scope_detail="${scope_result#*:}"
    reason="[verify-after-edit/SCOPE] files_changed includes paths outside declared scope. Detail: ${scope_detail}. Return a NEXUS:REVISE with the corrected files_changed limited to the brief's context_files. Failure to keep writes within declared scope violates the Output-Dir STRICT contract."
    HOOKS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    event_out="${EVENT:-SubagentStop}"
    python3 "$HOOKS_DIR/_gate_deny.py" deny "$event_out" "verify-after-edit/SCOPE" "$reason"
    exit 2
  fi
fi

# Per-file check accumulator
accumulator=""

for FILE_PATH in "${FILES[@]}"; do
  # Normalize relative paths to absolute under project root
  case "$FILE_PATH" in
    /*) ;;
    *) FILE_PATH="$PROJECT_ROOT/$FILE_PATH" ;;
  esac

  # Skip files outside the project root
  case "$FILE_PATH" in
    "$PROJECT_ROOT"/*) ;;
    *) continue ;;
  esac

  # Skip orchestration scaffolding
  case "$FILE_PATH" in
    */.claude/*|*/.memory/*) continue ;;
  esac

  # Determine check kind
  case "$FILE_PATH" in
    *.ts|*.tsx)
      check_kind="ts"
      ;;
    *.sh)
      check_kind="sh"
      ;;
    *.py)
      check_kind="py"
      ;;
    *)
      continue
      ;;
  esac

  if [ ! -f "$FILE_PATH" ]; then continue; fi

  result=""
  if [ "$check_kind" = "ts" ]; then
    cd "$PROJECT_ROOT/$TS_CHECK_DIR" 2>/dev/null || cd "$PROJECT_ROOT"
    raw=$(rtk tsc --noEmit --skipLibCheck 2>&1 | head -40)
    if [ -n "$raw" ]; then
      result="rtk tsc on $FILE_PATH:\n$raw"
    fi
  elif [ "$check_kind" = "sh" ]; then
    # Check the shebang: if the first line names python, the file has a python
    # body despite a .sh extension (e.g. lens-gate.sh, no-deferral-gate.sh).
    # Run py_compile in that case; otherwise use bash -n.
    first_line=$(head -1 "$FILE_PATH" 2>/dev/null)
    if printf '%s' "$first_line" | grep -q 'python'; then
      comp=$(python3 -m py_compile "$FILE_PATH" 2>&1 | head -40)
      if [ -n "$comp" ]; then
        result="python3 -m py_compile $FILE_PATH:\n$comp"
      fi
    else
      # bash -n: parse-only syntax check; prints to stderr, silent on success.
      syn=$(bash -n "$FILE_PATH" 2>&1 | head -40)
      if [ -n "$syn" ]; then
        result="bash -n $FILE_PATH:\n$syn"
      fi
    fi
  elif [ "$check_kind" = "py" ]; then
    cd "$PROJECT_ROOT/$PY_CHECK_DIR" 2>/dev/null || cd "$PROJECT_ROOT"
    raw=$(uv run ruff check "$FILE_PATH" 2>&1 | head -40)
    if [ -n "$raw" ] && ! echo "$raw" | grep -q "^All checks passed"; then
      result="uv run ruff check $FILE_PATH:\n$raw"
    fi
  fi

  if [ -n "$result" ]; then
    accumulator="${accumulator}\n${result}"
  fi
done

if [ -n "$accumulator" ]; then
  full="[verify-after-edit] post-change check findings:${accumulator}"
  event_out="${EVENT:-PostToolUse}"
  jq -n --arg r "$full" --arg ev "$event_out" '{
    hookSpecificOutput: {
      hookEventName: $ev,
      additionalContext: $r
    }
  }'
fi

exit 0
