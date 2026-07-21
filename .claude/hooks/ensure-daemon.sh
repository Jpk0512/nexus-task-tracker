#!/usr/bin/env bash
# ensure-daemon.sh — SessionStart hook (N32 staged-flag daemon activation;
# TASK-105 fail-out-loud rewrite).
#
# STAGED-FLAG SAFETY PROPERTY: registering this hook in .claude/settings.json
# is inert on its own. If .claude/daemon.enabled is absent the hook exits 0
# with zero side effects — that is the ONE legitimate silent exit. ROLLBACK
# (one step): rm .claude/daemon.enabled.
#
# TASK-105 RCA this rewrite fixes: GUI-launched app sessions carry a minimal
# PATH (no /opt/homebrew/bin, where uv lives), so the old `command -v uv ||
# exit 0` guard silently no-opped EVERY spawn — 13k+ no-socket RPC misses in
# one evening while a manual ensure worked instantly. Two changes:
#   1. uv is resolved robustly (command -v, /opt/homebrew/bin/uv,
#      ~/.local/bin/uv) instead of trusting the hook env PATH.
#   2. When the flag IS present there are NO silent failure exits: every
#      failure path emits one loud "[DAEMON-DOWN] <cause> - <fix>" line to
#      stdout (SessionStart stdout reaches the session as context) AND
#      appends a timestamped line to .memory/files/daemon-ensure-failures.log
#      (the only channel left once the ensure has been detached).
#
# The detach/timeout design is unchanged: the ensure runs fully detached in a
# background subshell so SessionStart is NEVER blocked, and an independent
# watchdog bounds it to NEXUS_DAEMON_ENSURE_HOOK_TIMEOUT_S seconds (default
# 10s — above the ensure path's own worst-case CONNECT_TIMEOUT_S +
# SPAWN_WAIT_S budget). A watchdog kill and a nonzero ensure exit are both
# reported to the failures log. The hook itself still ALWAYS exits 0.
#
# HAND-RECONCILED TWIN: nexus-package/.claude/hooks/ensure-daemon.sh must
# stay byte-identical to this file (checked by
# .claude/hooks/tests/test_ensure_daemon.sh). Edit both together.

cd "$(dirname "$0")/../.." 2>/dev/null || {
    echo "[DAEMON-DOWN] cannot cd to project root from hook dir $(dirname "$0") - reinstall Nexus hooks"
    exit 0
}

FLAG=".claude/daemon.enabled"
[ -f "$FLAG" ] || exit 0

PROJECT_ROOT="$PWD"
BROKER_DIR="$PROJECT_ROOT/nexus-broker"
HOOK_TIMEOUT_S="${NEXUS_DAEMON_ENSURE_HOOK_TIMEOUT_S:-10}"
FAIL_LOG="$PROJECT_ROOT/.memory/files/daemon-ensure-failures.log"

report_down() {
    echo "[DAEMON-DOWN] $1"
    {
        mkdir -p "${FAIL_LOG%/*}" &&
            printf '%s [DAEMON-DOWN] %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$1" >> "$FAIL_LOG"
    } 2>/dev/null
}

if [ ! -d "$BROKER_DIR" ]; then
    report_down "nexus-broker/ missing at $BROKER_DIR - reinstall Nexus or rm .claude/daemon.enabled"
    exit 0
fi

UV_BIN=""
for candidate in "$(command -v uv 2>/dev/null)" /opt/homebrew/bin/uv "$HOME/.local/bin/uv"; do
    if [ -n "$candidate" ] && [ -x "$candidate" ]; then
        UV_BIN="$candidate"
        break
    fi
done
if [ -z "$UV_BIN" ]; then
    report_down "uv not found (PATH=$PATH; also tried /opt/homebrew/bin/uv and ~/.local/bin/uv) - install uv or extend the hook environment PATH"
    exit 0
fi

MANUAL_FIX="run manually: $UV_BIN run --directory $BROKER_DIR python -m broker.daemon ensure --project-path $PROJECT_ROOT"

(
    "$UV_BIN" run --quiet --directory "$BROKER_DIR" python -m broker.daemon ensure \
        --project-path "$PROJECT_ROOT" >/dev/null 2>&1 &
    ENSURE_PID=$!

    (
        sleep "$HOOK_TIMEOUT_S"
        if kill -0 "$ENSURE_PID" 2>/dev/null; then
            kill -TERM "$ENSURE_PID" 2>/dev/null
            sleep 1
            kill -0 "$ENSURE_PID" 2>/dev/null && kill -KILL "$ENSURE_PID" 2>/dev/null
            report_down "ensure timed out after ${HOOK_TIMEOUT_S}s and was killed by the hook watchdog - $MANUAL_FIX"
        fi
    ) >/dev/null 2>&1 &
    WATCHDOG_PID=$!

    wait "$ENSURE_PID" 2>/dev/null
    ENSURE_RC=$?
    kill "$WATCHDOG_PID" 2>/dev/null
    wait "$WATCHDOG_PID" 2>/dev/null
    if [ "$ENSURE_RC" -ne 0 ]; then
        report_down "ensure exited rc=$ENSURE_RC - $MANUAL_FIX"
    fi
) >/dev/null 2>&1 &

exit 0
