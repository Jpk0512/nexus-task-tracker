#!/usr/bin/env bash
# Library — NOT a registered hook. Source this from other hooks:
#   source "$(dirname "$0")/heartbeat-emitter.sh" 2>/dev/null || true
#
# Provides: emit_heartbeat HOOK_NAME EVENT DECISION LATENCY_MS
# Appends one JSON line to .memory/files/hook_heartbeat.jsonl.
# Fails silently if the file is not writable (never blocks the parent hook).

# Resolve the repo root from THIS library's own location (walk parents for
# .memory) so the heartbeat path is correct regardless of the caller's CWD.
# ${REPO_ROOT:-.} fell back to "." — a hook fired from /tmp wrote (and silently
# failed to write) ./.memory/... instead of the real repo. BASH_SOURCE is the
# path to this sourced file even when it is sourced, not executed.
_heartbeat_repo_root() {
  local src="${BASH_SOURCE[0]:-$0}"
  local dir
  dir="$(cd "$(dirname "$src")" && pwd)"
  while [ "$dir" != "/" ]; do
    if [ -d "$dir/.memory" ]; then
      printf '%s' "$dir"
      return 0
    fi
    dir="$(dirname "$dir")"
  done
  # No .memory ancestor found — fall back to the directory two levels up from
  # this library (.claude/hooks/ -> repo root) rather than CWD.
  (cd "$(dirname "$src")/../.." && pwd)
}

_HEARTBEAT_FILE="$(_heartbeat_repo_root)/.memory/files/hook_heartbeat.jsonl"

emit_heartbeat() {
  local hook_name="$1"
  local event="$2"
  local decision="$3"
  local latency_ms="${4:-0}"
  local ts
  ts=$(date -u +"%Y-%m-%dT%H:%M:%SZ" 2>/dev/null || echo "1970-01-01T00:00:00Z")
  printf '{"ts":"%s","hook":"%s","event":"%s","decision":"%s","latency_ms":%s}\n' \
    "$ts" "$hook_name" "$event" "$decision" "$latency_ms" \
    >> "$_HEARTBEAT_FILE" 2>/dev/null || true
}

# macOS-safe millisecond timer.
ms_now() {
  python3 -c "import time; print(int(time.time()*1000))" 2>/dev/null || echo 0
}
