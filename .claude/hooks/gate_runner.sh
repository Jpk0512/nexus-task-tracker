#!/usr/bin/env bash
# gate_runner.sh — consolidated PURE-BASH gate runner (R3-T10 / N15 sibling
# of gate_runner.py).
#
# NOT CURRENTLY WIRED (R3-T10 N15 revise-cycle-2 / TASK-011): settings.json's
# pretooluse-bash/pretooluse-write matchers were reverted to direct
# multi-hook-entry wiring after bench_gate_runner.py, re-run 5x post-fix,
# consistently showed this wrapper still could not beat direct invocation for
# these short 2-3-check chains (avg ~-11% to -18%, down from the original
# ~-92%/-22% once a benchmark artifact + 3 avoidable external-process forks
# here were fixed — see notepad). The fixed cost of this wrapper's own
# process/indirection layer outweighs the fork/exec savings when there are
# only 2-3 checks to consolidate. Kept as tested (test_gate_runner.py),
# benchmarked (bench_gate_runner.py) infrastructure in case a real
# (non-sandboxed) harness run shows different economics — re-wire by pointing
# settings.json's matcher back at this file if so.
#
# gate_runner.py's SPEED win comes from bypassing _py.sh's per-call
# interpreter resolution for python-based checks — a win that only exists
# for events whose chain already paid that cost. `pretooluse-bash` and
# `pretooluse-write` never did: every one of their checks was ALREADY a
# direct bash-to-bash invocation with zero _py.sh/python involved. Routing
# those two chains through gate_runner.py would ADD a fresh python-
# interpreter startup this event never paid before — a net REGRESSION,
# confirmed by direct wall-clock benchmarking (see this leaf's notepad).
# This sibling keeps the SAME consolidation properties (one process spawn
# instead of N, real short-circuit, cheapest-first order) while staying
# 100% bash — no python process is ever started; it still lost to direct
# invocation, just by less (see NOT CURRENTLY WIRED above).
#
# Every check below is invoked as an UNCHANGED subprocess of its own file
# (byte-for-byte identical behavior to direct invocation, including for
# oracle-immutability-guard.sh — R1-T11, byte-for-byte-critical).
set -uo pipefail

# `${BASH_SOURCE[0]%/*}` (bash parameter expansion) instead of `dirname` (R3-T10
# N15 revise-cycle-2 / TASK-011): one fewer external-process fork at startup;
# BASH_SOURCE[0] here is always the settings.json-supplied absolute path, which
# always contains a `/` for the expansion to strip.
HOOKS_DIR="$(cd "${BASH_SOURCE[0]%/*}" && pwd)"
EVENT="${1:-}"
# Bash-builtin stdin slurp (R3-T10 N15 revise-cycle-2 / TASK-011): `cat` here
# forked an extra external process on every single invocation for no reason
# `read` can't do itself -- `read -r -d ''` reads to EOF into PAYLOAD with
# zero forks (measured ~7-12ms/call on this profiling run; see notepad).
PAYLOAD=""
IFS= read -r -d '' PAYLOAD || true

case "$EVENT" in
  pretooluse-bash)
    # Cheapest-first per N12 telemetry (plans/11-gate-enforcement-audit.md
    # section 1): worktree-guard 106.1ms, no-direct-push-to-main 106.8ms —
    # near-identical; original settings.json order preserved.
    CHECKS=("worktree-guard.sh" "no-direct-push-to-main.sh")
    ;;
  pretooluse-write)
    # secret-path-guard 75.9ms < edit-boundary-impact-gate (N14, new,
    # unmeasured, own <=50ms SPEED budget) < oracle-immutability-guard
    # 151-160ms (R1-T11, most expensive, byte-for-byte-critical, last).
    CHECKS=("secret-path-guard.sh" "edit-boundary-impact-gate.sh" "oracle-immutability-guard.sh")
    ;;
  *)
    printf '[gate-runner.sh] unknown event: %s\n' "$EVENT" >&2
    exit 0  # fail open — never block a tool call over a misconfigured runner
    ;;
esac

# No wrapper-level heartbeat here (R3-T10 N15 revise-cycle-1 / TASK-010):
# every check in CHECKS above already calls emit_heartbeat itself via the
# sourced heartbeat-emitter.sh (confirmed by direct source grep) -- a
# wrapper-level emit here on top of that doubled every row in
# hook_heartbeat.jsonl. This also drops the two `_ms_now` python3 subprocess
# spawns per check that existed ONLY to time that now-removed call. If a
# FUTURE check is added to CHECKS that does NOT already self-emit, give IT
# its own emit_heartbeat call (mirrors every other gate in this tree) rather
# than reviving a wrapper-level one here -- a single source of truth per
# check, matching gate_runner.py's sibling design (_SELF_EMITTING_CHECKS).

ADVISORY_PARTS=()
# Bash-native temp path (R3-T10 N15 revise-cycle-2 / TASK-011): `mktemp` forked
# an external process (~6ms measured) purely to generate a unique filename --
# $$ (this process's own PID) + $RANDOM is unique enough for a same-process
# ephemeral stderr-capture file that lives only for this invocation's lifetime.
ERR_TMP="${TMPDIR:-/tmp}/gate_runner_sh_stderr.$$.$RANDOM"

for c in "${CHECKS[@]}"; do
  OUT=$(printf '%s' "$PAYLOAD" | "$HOOKS_DIR/$c" 2>"$ERR_TMP")
  RC=$?
  # `$(<file)` is bash's builtin fast-path file read -- avoids forking `cat`
  # per check (measured ~1-2ms/call; see notepad).
  ERR="$(<"$ERR_TMP")" 2>/dev/null || ERR=""

  if [ "$RC" -eq 2 ]; then
    # Short-circuit: re-emit this check's own captured output VERBATIM —
    # byte-for-byte, the unchanged file's own bytes.
    [ -n "$OUT" ] && printf '%s\n' "$OUT"
    [ -n "$ERR" ] && printf '%s\n' "$ERR" >&2
    rm -f "$ERR_TMP" 2>/dev/null
    exit 2
  fi

  if [ "$RC" -ne 0 ]; then
    printf '[gate-runner.sh] %s exited %s unexpectedly (fail-open): %s\n' "$c" "$RC" "$ERR" >&2
    continue
  fi

  [ -n "$OUT" ] && ADVISORY_PARTS+=("$OUT")
done
rm -f "$ERR_TMP" 2>/dev/null

if [ "${#ADVISORY_PARTS[@]}" -gt 0 ]; then
  # Merge every check's additionalContext into ONE JSON blob (mirrors
  # gate_runner.py's _extract_advisory_context + combined emit) rather than
  # stacking multiple raw JSON objects from one process invocation.
  printf '%s\n' "${ADVISORY_PARTS[@]}" | python3 -c '
import json, sys
parts = []
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        continue
    ctx = obj.get("hookSpecificOutput", {}).get("additionalContext")
    if ctx:
        parts.append(ctx)
if parts:
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": "\n".join(parts)}}))
'
fi
exit 0
