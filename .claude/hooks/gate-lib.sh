#!/usr/bin/env bash
# gate-lib.sh — OPT-030 canonical structured-denial emitters.
# Source this file in bash gate hooks. Emits bytes identical to _gate_deny.py.
#
# IMPORTANT: this file must NOT declare set -u at top level — doing so leaks
# the option into the sourcing caller's shell and turns unbound-variable
# references in the caller into fatal errors (fail-open regression).
#
# gate_deny <event> <code> <reason> [--exit N] [--no-stderr]
#   Emits canonical permissionDecision:deny JSON to stdout.
#   Default: exit 2, reason echoed to stderr.
#   socraticode variant: --exit 0 --no-stderr
#   NOTE: gate_deny calls exit — it terminates the caller process. Do NOT
#   invoke it inside $(...) / backticks / pipes or exit won't propagate.
#
# gate_advise <event> <code> <msg> [--stderr]
#   Emits canonical additionalContext JSON to stdout.
#   Default: exit 0 (NOT called here — caller keeps its own exit 0), no stderr.
#   broker-gate / worktree escape-hatch variant: --stderr
#   NOTE: gate_advise does NOT exit — advisory paths fall through to the
#   caller's own exit 0. Do NOT delete the trailing exit 0 at the call site.

gate_deny() {
    local event="$1" code="$2" reason="$3"
    shift 3
    local exit_code=2
    local want_stderr=1
    while [ $# -gt 0 ]; do
        case "$1" in
            --exit)    exit_code="$2"; shift 2 ;;
            --no-stderr) want_stderr=0; shift ;;
            *) shift ;;
        esac
    done
    local full="[GATE:${code}] ${reason}"
    # jq -cn preserves object-construction key order and emits compact JSON
    # (no spaces after : or ,) — byte-identical to _gate_deny.py with
    # separators=(',',':').
    jq -cn --arg e "$event" --arg r "$full" \
        '{"hookSpecificOutput":{"hookEventName":$e,"permissionDecision":"deny","permissionDecisionReason":$r}}'
    if [ "$want_stderr" = "1" ]; then
        printf '%s\n' "$full" >&2
    fi
    # OPT-033 best-effort telemetry — must NOT change exit or stdout/stderr bytes.
    {
        local _sink
        if [ -n "${NEXUS_GATE_BLOCKS_PATH:-}" ]; then
            _sink="$NEXUS_GATE_BLOCKS_PATH"
        else
            # repo-root = two dirs up from this file's directory (.claude/hooks → repo-root)
            _sink="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/.memory/files/gate_blocks.jsonl"
        fi
        local _hook _code_part
        case "$code" in
            */*) _hook="${code%%/*}"; _code_part="${code#*/}" ;;
            *)   _hook="$code"; _code_part="" ;;
        esac
        local _ts _reason_trunc
        _ts="$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || echo "")"
        _reason_trunc="${reason:0:200}"
        mkdir -p "$(dirname "$_sink")" 2>/dev/null
        jq -cn \
            --arg ts "$_ts" \
            --arg ev "$event" \
            --arg hk "$_hook" \
            --arg cd "$_code_part" \
            --arg rs "$_reason_trunc" \
            '{"ts":$ts,"event":$ev,"hook":$hk,"code":$cd,"reason":$rs}' \
            >> "$_sink" 2>/dev/null
    } 2>/dev/null || true
    exit "$exit_code"
}

gate_advise() {
    local event="$1" code="$2" msg="$3"
    shift 3
    local want_stderr=0
    while [ $# -gt 0 ]; do
        case "$1" in
            --stderr) want_stderr=1; shift ;;
            *) shift ;;
        esac
    done
    local full="[GATE:${code}] ${msg}"
    jq -cn --arg e "$event" --arg m "$full" \
        '{"hookSpecificOutput":{"hookEventName":$e,"additionalContext":$m}}'
    if [ "$want_stderr" = "1" ]; then
        printf '%s\n' "$full" >&2
    fi
    # NOTE: gate_advise does NOT exit — caller's exit 0 remains in effect.
}
