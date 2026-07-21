#!/usr/bin/env bash
# Codex → Nexus hook adapter (ADVISORY / class-F3 — DEC-102 DEC-2).
#
# Codex CLI uses a JSON stdin/stdout hook contract very close to Cursor's, but —
# unlike Claude Code's PreToolUse gates and Cursor's failClosed:true events —
# Codex has NO fail-closed hook contract this adapter can rely on to BLOCK an
# action. So this adapter deliberately runs the Nexus gates in ADVISORY mode:
#
#   1. reads Codex's stdin JSON (one buffered read),
#   2. translates it into the Claude-Code-shaped JSON each target .claude/hooks
#      gate expects (mirrors cursor-bridge.sh's build_claude_payload),
#   3. runs each target Nexus gate in order and captures its verdict,
#   4. LOGS every DENY verdict (to stderr + best-effort .memory/files/codex-adapter.log)
#      so the gate decision is AUDITABLE,
#   5. ALWAYS emits an ALLOW envelope and exits 0 — it NEVER fails closed, NEVER
#      exits non-zero to block the Codex action.
#
# This is the honest enforcement ceiling for Codex (DEC-102): the SAME gate logic
# runs and is logged, but a malformed brief / unsafe push / retired persona is
# NOT hard-blocked the way it is under Claude Code (F1). The downgrade is
# documented in .codex/rules/nexus-enforcement.mdc. Do NOT "fix" this adapter to
# exit 2 — the advisory-only contract is intentional and asserted by
# .codex/hooks/tests/test_codex_adapter.py.
#
# Usage (invoked by .codex/hooks.json):
#   .codex/hooks/codex-adapter.sh <codex_event> <nexus-hook> [<nexus-hook> ...]
#
#   <codex_event> ∈ beforeShellExecution | beforeReadFile | afterFileEdit |
#                   beforeSubmitPrompt | stop | sessionStart | subagentStart |
#                   subagentStop
#   <nexus-hook>  filename UNDER .claude/hooks/ (e.g. worktree-guard.sh)
#                 OR a literal shell snippet prefixed with "sh:" (for the python
#                 log.py lines that have no dedicated script).
#
# PROJECT_ROOT resolution: Codex sets cwd to the workspace root when invoking
# hooks, so $PWD is the project root. We resolve from there — no hardcoded
# absolute paths appear in this file; it is portable across any Nexus-installed
# project.

set -uo pipefail

CODEX_EVENT="${1:-}"
shift || true
TARGETS=("$@")

# Codex sets the cwd to the workspace root for hook invocations.
PROJECT_ROOT="${PWD}"
HOOKS_DIR="${PROJECT_ROOT}/.claude/hooks"
VERDICT_LOG="${PROJECT_ROOT}/.memory/files/codex-adapter.log"

# ── Capability class for this event (drives the allow emitter). ───────────────
# permission        → beforeShellExecution / subagentStart: {permission:allow}.
# permission_narrow → beforeReadFile: {permission:allow}.
# continue          → beforeSubmitPrompt: {continue:true} (NOT a permission key).
# "" (empty)        → observational event (afterFileEdit/stop/sessionStart/
#                     subagentStop): bare exit 0, no stdout envelope.
CAP=""
case "$CODEX_EVENT" in
  beforeShellExecution) CAP=permission ;;
  beforeReadFile)       CAP=permission_narrow ;;
  beforeSubmitPrompt)   CAP=continue ;;
  subagentStart)        CAP=permission ;;
esac

# Emit the event-appropriate ALLOW envelope on stdout for a block-capable event.
# For observational events (CAP="") this prints NOTHING (silent allow by exit 0).
emit_allow() {
  case "$CAP" in
    permission|permission_narrow)
      printf '{"permission":"allow"}'
      ;;
    continue)
      printf '{"continue":true}'
      ;;
  esac
}

# Record an ADVISORY deny verdict: log it (stderr + best-effort file) but do NOT
# block. This is the class-F3 contract — the verdict is captured, the action
# proceeds.
log_advisory_verdict() {
  local target="$1" reason="$2"
  local line
  line="[codex-adapter] ADVISORY (class-F3): ${CODEX_EVENT} gate ${target} → DENY: ${reason} — Codex has no fail-closed PreToolUse contract (DEC-102); NOT blocking."
  printf '%s\n' "$line" >&2
  # Best-effort durable capture; never fail the adapter if the dir is absent.
  if [[ -d "${PROJECT_ROOT}/.memory/files" ]]; then
    printf '%s\n' "$line" >> "$VERDICT_LOG" 2>/dev/null || true
  fi
}

# Extract a single clean human reason from a Nexus gate's captured output ($1),
# handling the three shapes Nexus gates emit (pretty JSON, text-then-JSON,
# compact JSON). Ported from cursor-bridge.sh's extract_clean_reason.
extract_clean_reason() {
  local raw="$1" reason=""
  if command -v python3 >/dev/null 2>&1; then
    reason=$(printf '%s' "$raw" | python3 -c '
import json, sys
raw = sys.stdin.read()
dec = json.JSONDecoder()
objs = []
i, n = 0, len(raw)
while i < n:
    if raw[i] in "{[":
        try:
            val, end = dec.raw_decode(raw, i)
            objs.append(val)
            i = end
            continue
        except ValueError:
            pass
    i += 1
def reason_of(o):
    if not isinstance(o, dict):
        return ""
    hso = o.get("hookSpecificOutput")
    if isinstance(hso, dict) and hso.get("permissionDecisionReason"):
        return str(hso["permissionDecisionReason"])
    for k in ("permissionDecisionReason", "user_message"):
        if o.get(k):
            return str(o[k])
    return ""
for o in reversed(objs):
    r = reason_of(o)
    if r:
        sys.stdout.write(r)
        sys.exit(0)
for line in raw.splitlines():
    s = line.strip()
    if s and s not in ("{", "}", "[", "]"):
        sys.stdout.write(s)
        break
' 2>/dev/null || true)
    printf '%s' "$reason"
    return 0
  fi
  # Fallback (no python3): first non-empty, non-brace text line.
  local line
  while IFS= read -r line; do
    [[ -z "$line" ]] && continue
    if [[ "$line" != "{" && "$line" != "}" ]]; then
      printf '%s' "$line"
      return 0
    fi
  done <<< "$raw"
}

# Extract the permissionDecision from a gate's captured output ($1). Prints
# "deny"/"allow"/"" (last object with a decision wins). Ported from cursor-bridge.
extract_decision() {
  local raw="$1" decision=""
  if command -v python3 >/dev/null 2>&1; then
    decision=$(printf '%s' "$raw" | python3 -c '
import json, sys
raw = sys.stdin.read()
dec = json.JSONDecoder()
objs = []
i, n = 0, len(raw)
while i < n:
    if raw[i] in "{[":
        try:
            val, end = dec.raw_decode(raw, i)
            objs.append(val)
            i = end
            continue
        except ValueError:
            pass
    i += 1
def decision_of(o):
    if not isinstance(o, dict):
        return ""
    hso = o.get("hookSpecificOutput")
    if isinstance(hso, dict) and hso.get("permissionDecision"):
        return str(hso["permissionDecision"])
    if o.get("permissionDecision"):
        return str(o["permissionDecision"])
    return ""
for o in reversed(objs):
    d = decision_of(o)
    if d:
        sys.stdout.write(d)
        sys.exit(0)
' 2>/dev/null || true)
    printf '%s' "$decision"
    return 0
  fi
}

INPUT=$(cat)

# ── Degraded path: jq missing. ────────────────────────────────────────────────
# Without jq we cannot build the translated payload. Advisory contract stands:
# emit an explicit allow envelope and exit 0 (never block, never empty output).
if ! command -v jq >/dev/null 2>&1; then
  emit_allow
  exit 0
fi

# session_id ← conversation_id (fallback to session_id, then "unknown").
SID=$(printf '%s' "$INPUT" | jq -r '.conversation_id // .session_id // "unknown"' 2>/dev/null || echo unknown)

# ── Translate Codex stdin → Claude-Code-shaped JSON for the target gates. ─────
build_claude_payload() {
  case "$CODEX_EVENT" in
    beforeShellExecution)
      local cmd
      cmd=$(printf '%s' "$INPUT" | jq -r '.command // ""')
      jq -n --arg cmd "$cmd" --arg sid "$SID" '{
        tool_name: "Bash",
        tool_input: { command: $cmd },
        session_id: $sid,
        hook_event_name: "PreToolUse"
      }'
      ;;
    beforeReadFile)
      local fp content
      fp=$(printf '%s' "$INPUT" | jq -r '.file_path // ""')
      content=$(printf '%s' "$INPUT" | jq -r '.content // ""')
      jq -n --arg fp "$fp" --arg content "$content" --arg sid "$SID" '{
        tool_name: "Read",
        tool_input: { file_path: $fp },
        tool_response: { text: $content },
        session_id: $sid,
        hook_event_name: "PreToolUse"
      }'
      ;;
    afterFileEdit)
      local fp edits
      fp=$(printf '%s' "$INPUT" | jq -r '.file_path // ""')
      edits=$(printf '%s' "$INPUT" | jq -c '.edits // []')
      jq -n --arg fp "$fp" --argjson edits "$edits" --arg sid "$SID" '{
        tool_name: "Edit",
        tool_input: { file_path: $fp, edits: $edits },
        session_id: $sid,
        hook_event_name: "PostToolUse"
      }'
      ;;
    beforeSubmitPrompt)
      jq -n --arg sid "$SID" '{ session_id: $sid, hook_event_name: "UserPromptSubmit" }'
      ;;
    stop)
      jq -n --arg sid "$SID" '{ session_id: $sid, hook_event_name: "Stop" }'
      ;;
    sessionStart)
      jq -n --arg sid "$SID" '{ session_id: $sid, hook_event_name: "SessionStart" }'
      ;;
    subagentStart)
      # Codex subagentStart input mirrors Cursor's: {subagent_type, task,
      # is_parallel_worker, git_branch, ...}. Map → the Claude PreToolUse:Task
      # shape the dispatch gates read; emit tool_input + .input mirror +
      # top-level subagent_type so every gate's extraction path sees it.
      local subtype task pw gbranch
      subtype=$(printf '%s' "$INPUT" | jq -r '.subagent_type // ""')
      task=$(printf '%s' "$INPUT" | jq -r '.task // .prompt // .instructions // .description // ""')
      pw=$(printf '%s' "$INPUT" | jq -r '.is_parallel_worker // false')
      gbranch=$(printf '%s' "$INPUT" | jq -r '.git_branch // ""')
      jq -n --arg st "$subtype" --arg task "$task" --argjson pw "$pw" \
            --arg gb "$gbranch" --arg sid "$SID" '{
        tool_name: "Task",
        tool_input: { subagent_type: $st, description: $task, prompt: $task },
        input: { subagent_type: $st, description: $task, prompt: $task },
        subagent_type: $st,
        is_parallel_worker: $pw,
        git_branch: $gb,
        session_id: $sid,
        hook_event_name: "PreToolUse"
      }'
      ;;
    subagentStop)
      local summary modified status subtype
      summary=$(printf '%s' "$INPUT" | jq -r '.summary // ""')
      modified=$(printf '%s' "$INPUT" | jq -c '.modified_files // []')
      status=$(printf '%s' "$INPUT" | jq -r '.status // ""')
      subtype=$(printf '%s' "$INPUT" | jq -r '.subagent_type // ""')
      jq -n --arg s "$summary" --argjson mod "$modified" \
            --arg status "$status" --arg subtype "$subtype" --arg sid "$SID" '{
        last_assistant_message: $s,
        response: { text: $s },
        modified_files: $mod,
        status: $status,
        subagent_type: $subtype,
        session_id: $sid,
        hook_event_name: "SubagentStop"
      }'
      ;;
    *)
      printf '{}' ;;
  esac
}

CLAUDE_PAYLOAD=$(build_claude_payload)

# ── Run each target gate; LOG deny verdicts; never block. ─────────────────────
for target in "${TARGETS[@]}"; do
  out=""
  rc=0
  if [[ "$target" == sh:* ]]; then
    snippet="${target#sh:}"
    out=$( cd "$PROJECT_ROOT" && printf '%s' "$CLAUDE_PAYLOAD" | bash -c "$snippet" 2>&1 ) || rc=$?
  else
    hook_path="${HOOKS_DIR}/${target}"
    if [[ ! -x "$hook_path" ]]; then
      # Missing or non-executable: skip (advisory — never block).
      continue
    fi
    out=$( cd "$PROJECT_ROOT" && printf '%s' "$CLAUDE_PAYLOAD" | "$hook_path" 2>&1 ) || rc=$?
  fi

  # A Nexus gate signals a block two ways: exit 2, OR exit 0 + a nested
  # permissionDecision == "deny". In ADVISORY mode we treat BOTH as a logged
  # verdict, never a block.
  decision=$(extract_decision "$out")
  if [[ "$rc" -eq 2 || "$decision" == "deny" ]]; then
    clean=$(extract_clean_reason "$out")
    [[ -z "$clean" ]] && clean="${target} returned a DENY verdict (exit ${rc})."
    log_advisory_verdict "$target" "$clean"
  fi
done

# ── Emit the ALLOW envelope and exit 0 — ALWAYS. ──────────────────────────────
# Block-capable events emit an explicit allow envelope; observational events
# succeed silently. Under NO circumstance does this adapter exit non-zero to
# block a Codex action (class-F3 advisory contract).
if [[ -n "$CAP" ]]; then
  emit_allow
fi
exit 0
