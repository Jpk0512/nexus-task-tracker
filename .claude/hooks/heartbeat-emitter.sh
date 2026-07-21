#!/usr/bin/env bash
# Library — NOT a registered hook. Source this from other hooks:
#   source "$(dirname "$0")/heartbeat-emitter.sh" 2>/dev/null || true
#
# Provides: emit_heartbeat HOOK_NAME EVENT DECISION LATENCY_MS
# Appends one JSON line to .memory/files/hook_heartbeat.jsonl.
# Fails silently if the file is not writable (never blocks the parent hook).
#
# DAEMON SHIM (Tranche 2, nexus-redesign/audits/daemon-hook-plan-2026-07-12.md
# §C) — when `_heartbeat.py` is present alongside this file, emit_heartbeat()
# delegates to its CLI entry rather than re-implementing the JSONL append
# (and, now, the daemon-RPC-with-fallback logic) a second time in bash:
# _heartbeat.py is the single home of "try the daemon socket, else append
# inline", so bash never has to speak the unix-socket JSON-RPC protocol
# itself. Args are passed as normal argv tokens (never shell-interpolated
# into a string), so no escaping concern. NEXUS_HEARTBEAT_PATH (test
# isolation) is read by _heartbeat.py itself from the inherited environment.
#
# GRACEFUL DEGRADATION — several existing hook tests copy ONLY
# heartbeat-emitter.sh (+gate-lib.sh) into a scratch two-file `.claude/hooks/`
# directory, with no `_heartbeat.py`/`_daemon_rpc.py` alongside it (that
# pattern predates this shim). Hard-requiring the Python sibling there would
# silently produce ZERO heartbeat rows (a `2>/dev/null || true` swallow, not
# a visible failure) — a real regression this file shipped with once and
# fixed inline. So: delegate to `_heartbeat.py` ONLY when it actually exists
# next to this script; otherwise fall back to the original self-contained
# bash JSONL append (which knows nothing about the daemon, but always works).

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

# NEXUS_HEARTBEAT_PATH overrides the sink (test isolation) -- mirrors
# _heartbeat.py:_default_heartbeat_path's identical env-var precedence
# (R3-T10 N15 revise-cycle-1 / TASK-010). Before this fix, this library
# ignored the override and always wrote to the real repo path regardless of
# what a caller set, so a caller redirecting heartbeat writes for isolation
# (e.g. a test, or gate_runner.sh's own now-removed wrapper heartbeat) split
# telemetry for the SAME check across two different sinks/keys instead of
# landing in one place.
_HEARTBEAT_FILE="${NEXUS_HEARTBEAT_PATH:-$(_heartbeat_repo_root)/.memory/files/hook_heartbeat.jsonl}"

_HEARTBEAT_HOOKS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"

emit_heartbeat() {
  local hook_name="$1"
  local event="$2"
  local decision="$3"
  local latency_ms="${4:-0}"
  if [ -f "$_HEARTBEAT_HOOKS_DIR/_heartbeat.py" ]; then
    python3 "$_HEARTBEAT_HOOKS_DIR/_heartbeat.py" \
      "$hook_name" "$event" "$decision" "$latency_ms" 2>/dev/null && return 0
  fi
  # Self-contained fallback — no _heartbeat.py sibling (scratch/partial hook
  # copy) or the python3 delegate itself failed. No daemon awareness here by
  # design: this path exists purely so emit_heartbeat() NEVER silently no-ops.
  local ts
  ts=$(date -u +"%Y-%m-%dT%H:%M:%SZ" 2>/dev/null || echo "1970-01-01T00:00:00Z")
  mkdir -p "$(dirname "$_HEARTBEAT_FILE")" 2>/dev/null
  printf '{"ts":"%s","hook":"%s","event":"%s","decision":"%s","latency_ms":%s}\n' \
    "$ts" "$hook_name" "$event" "$decision" "$latency_ms" \
    >> "$_HEARTBEAT_FILE" 2>/dev/null || true
}

# macOS-safe millisecond timer.
ms_now() {
  python3 -c "import time; print(int(time.time()*1000))" 2>/dev/null || echo 0
}
