#!/usr/bin/env bash
# ensure-daemon.sh — SessionStart hook (N32, plans/14 SS6: hooks-side daemon
# activation, staged-flag mode).
#
# STAGED-FLAG SAFETY PROPERTY: registering this hook in .claude/settings.json
# is inert on its own. The body's very first act is checking for
# .claude/daemon.enabled; if that flag is absent, the hook exits 0 with zero
# side effects (no process spawned, nothing touched). ROLLBACK (one step):
# rm .claude/daemon.enabled.
#
# When the flag IS present, this hook fires the N31 broker-side
# `python -m broker.daemon ensure` entry (spawn-on-demand + health probe,
# reusing the daemon's existing double-fork spawn + stale-socket self-heal —
# see nexus-broker/src/broker/daemon/{ensure,client}.py) fully detached in
# the background and returns immediately. SessionStart is NEVER blocked, no
# matter how long — or whether — the daemon ever comes up: that is the
# actual safety property, not the timeout below it. An independent watchdog
# additionally bounds the background job to
# NEXUS_DAEMON_ENSURE_HOOK_TIMEOUT_S seconds (default 10s — comfortably
# above the ensure path's own ~5s worst-case budget: paths.py
# CONNECT_TIMEOUT_S=2s + SPAWN_WAIT_S=3s) so a genuinely hung spawn can never
# leak a process either. This is defense in depth on top of the
# fire-and-forget backgrounding, not a substitute for it.
#
# FAIL-OPEN BY CONTRACT: this hook NEVER exits nonzero and never emits
# anything the SessionStart transcript would read as an error. Any failure
# anywhere on the ensure path (missing uv, missing nexus-broker/, spawn
# failure) is swallowed — the session proceeds on whatever consumer-side
# fail-closed fallback already exists (broker.daemon.fallback). This hook
# adds a sanctioned spawn point, never a daemon-required invariant.
#
# HAND-RECONCILED TWIN: nexus-package/.claude/hooks/ensure-daemon.sh must
# stay byte-identical to this file (checked by
# .claude/hooks/tests/test_ensure_daemon.sh). Edit both together.

cd "$(dirname "$0")/../.." 2>/dev/null || exit 0   # project root

FLAG=".claude/daemon.enabled"
[ -f "$FLAG" ] || exit 0

PROJECT_ROOT="$PWD"
BROKER_DIR="$PROJECT_ROOT/nexus-broker"
HOOK_TIMEOUT_S="${NEXUS_DAEMON_ENSURE_HOOK_TIMEOUT_S:-10}"

# Everything below runs fully detached in a background subshell so the
# SessionStart hook process itself returns immediately regardless of what
# happens inside (missing uv, missing broker tree, or a genuinely hung
# ensure invocation).
(
    [ -d "$BROKER_DIR" ] || exit 0
    command -v uv >/dev/null 2>&1 || exit 0

    uv run --quiet --directory "$BROKER_DIR" python -m broker.daemon ensure \
        --project-path "$PROJECT_ROOT" >/dev/null 2>&1 &
    ENSURE_PID=$!

    (
        sleep "$HOOK_TIMEOUT_S"
        kill -0 "$ENSURE_PID" 2>/dev/null && kill -TERM "$ENSURE_PID" 2>/dev/null
        sleep 1
        kill -0 "$ENSURE_PID" 2>/dev/null && kill -KILL "$ENSURE_PID" 2>/dev/null
    ) >/dev/null 2>&1 &
    WATCHDOG_PID=$!

    wait "$ENSURE_PID" 2>/dev/null
    kill "$WATCHDOG_PID" 2>/dev/null
    wait "$WATCHDOG_PID" 2>/dev/null
) >/dev/null 2>&1 &

exit 0
