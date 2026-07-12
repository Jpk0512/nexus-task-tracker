#!/usr/bin/env bash
# PreToolUse hook: deny writes OUTSIDE the active task's write_scope
# allow-list BEFORE they land (R3-T09 / N14 — closes the DEC-011 follow-up
# of moving impact-checking from search-time to WRITE-time).
#
# This is the mirror-image check of oracle-immutability-guard.sh (R1-T11):
# that gate reads a do_not_touch DENY-list; this gate reads a write_scope
# ALLOW-list. Ported glob-matching semantics verbatim from that gate's
# _matches (do not reinvent) — trailing-slash directory-prefix globs,
# fnmatch, and bare directory-name subtree matching.
#
# Covers Write, Edit, MultiEdit, NotebookEdit — same extraction shape as
# oracle-immutability-guard.sh / secret-path-guard.sh:
#   Write / Edit        -> top-level `file_path` or `path`
#   NotebookEdit         -> top-level `notebook_path`
#   MultiEdit             -> `edits[].file_path` (array of objects)
#
# R1-T10 INCIDENT DISCIPLINE (hard constraint, never relax): this gate reads
# ONLY the tool call's `tool_input` for the write target path. It NEVER reads
# any persona/agent_type/subagent_type field from the envelope to decide
# scope — write_scope is a pure path allow-list already scoped to "the
# active task" by broker_state.json, so there is no persona-resolution step
# to get wrong. Structurally immune to the R1-T10 incident class, same as
# oracle-immutability-guard.sh is immune to the do_not_touch equivalent.
#
# Honors N12's typed override: a matching tool_input.override with
# gate="EDIT-BOUNDARY" and code="OUT-OF-SCOPE" (plus non-empty reason and
# authorized_by="user") allows the write through and logs a
# "decision":"override" row instead of a deny.
#
# No write_scope present (empty/absent/missing state) -> allow (silent) —
# a node-contract-less dispatch (flat brief, no accept-tier fields) must
# not be newly blocked by this gate; that's the Compatibility Note in
# docs/agents/CONTRACT.md's Node-Contract Schema v2 section.
#
# SPEED budget: pure glob-match against the already-loaded brief, no
# subprocess fan-out beyond the single python3 call already paid for by
# oracle-immutability-guard.sh's sibling design — target <=50ms added.
#
# On out-of-scope match: emit canonical deny JSON (exit 2), naming
# attempted_path.
# On in-scope match, override, or no active write_scope: silent exit 0.

set -euo pipefail

HOOKS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=heartbeat-emitter.sh
# set -e (bash 3.2 on macOS) treats a failed `source` of a missing file as
# fatal even inside `|| { ... }` — guard with an explicit -f test instead so
# a missing heartbeat-emitter.sh never aborts the gate (best-effort
# telemetry must never break allow/deny).
if [ -f "${HOOKS_DIR}/heartbeat-emitter.sh" ]; then
    # shellcheck source=heartbeat-emitter.sh
    source "${HOOKS_DIR}/heartbeat-emitter.sh" 2>/dev/null || true
fi
# Belt-and-suspenders: even if the source succeeded but the file did not
# define both helpers (truncated/edited), guarantee they exist before first use.
command -v ms_now >/dev/null 2>&1 || ms_now() { python3 -c "import time; print(int(time.time()*1000))" 2>/dev/null || echo 0; }
command -v emit_heartbeat >/dev/null 2>&1 || emit_heartbeat() { :; }

_HB_START_MS=$(ms_now 2>/dev/null || echo 0)
_hb() {
  local decision="$1"
  local _elapsed=$(( $(ms_now 2>/dev/null || echo 0) - _HB_START_MS ))
  emit_heartbeat "edit-boundary-impact-gate" "PreToolUse" "$decision" "$_elapsed" 2>/dev/null || true
}

INPUT=$(cat)

# ── Resolve repo root + state path, extract paths, match write_scope globs ──
# All in one Python call. INPUT is passed via env var to avoid stdin conflict.
RESULT=$(HOOK_INPUT="$INPUT" python3 - <<'PY'
import fnmatch
import json
import os
import sys
from pathlib import Path


def _repo_root():
    env = os.environ.get("_HOOK_REPO_ROOT")
    if env:
        return Path(env)
    here = Path(__file__).resolve()
    for candidate in [here, *here.parents]:
        if (candidate / ".memory").is_dir():
            return candidate
    return here.parent.parent.parent


def _state_path(repo_root):
    env = os.environ.get("NEXUS_BROKER_STATE_PATH")
    if env:
        return Path(env)
    return repo_root / ".memory" / "files" / "broker_state.json"


def _read_brief(state_path):
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(state, dict):
        return {}
    brief = state.get("approved_brief")
    if not isinstance(brief, dict):
        return {}
    return brief


def _read_write_scope(brief):
    globs = brief.get("write_scope")
    if not isinstance(globs, list):
        return []
    return [g for g in globs if isinstance(g, str) and g.strip()]


def _matches(path, glob):
    """Ported verbatim from oracle-immutability-guard.sh's _matches (itself
    ported from do-not-touch-guard.sh) — do not reinvent matching semantics."""
    normglob = glob.strip()
    if normglob.endswith("/"):
        prefix = normglob.rstrip("/")
        return path == prefix or path.startswith(prefix + "/")
    if fnmatch.fnmatch(path, normglob):
        return True
    if "*" not in normglob and "?" not in normglob and "[" not in normglob:
        return path == normglob or path.startswith(normglob + "/")
    return False


def _relativize(raw_path, repo_root):
    """Absolute in-repo paths must be compared to write_scope globs in their
    REPO-RELATIVE form, since globs are authored repo-relative. Paths outside
    repo_root degrade to the raw string unchanged (still safe to _matches
    against, just never able to match a repo-relative glob — no crash, no
    false positive)."""
    p = Path(raw_path)
    if not p.is_absolute():
        return raw_path
    try:
        return str(p.resolve().relative_to(repo_root.resolve()))
    except ValueError:
        return raw_path


raw = os.environ.get("HOOK_INPUT", "")
try:
    payload = json.loads(raw)
except Exception:
    sys.exit(0)

repo_root = _repo_root()
brief = _read_brief(_state_path(repo_root))
scope_globs = _read_write_scope(brief)
if not scope_globs:
    # No write_scope declared for the active task -> nothing to enforce.
    sys.exit(0)

# tool_input is the ONLY source read for the write target path (R1-T10
# incident discipline) — never any persona/agent_type field from elsewhere
# in the payload envelope.
tool_input = payload.get("tool_input", {})
paths = []

for key in ("file_path", "path", "notebook_path"):
    val = tool_input.get(key)
    if val:
        paths.append(val)

for edit in tool_input.get("edits", []):
    if isinstance(edit, dict) and "file_path" in edit:
        paths.append(edit["file_path"])

if not paths:
    sys.exit(0)

# Typed override (N12 design): tool_input.override with gate="EDIT-BOUNDARY"
# and code="OUT-OF-SCOPE", non-empty reason, authorized_by="user".
override = tool_input.get("override")
override_ok = (
    isinstance(override, dict)
    and override.get("gate") == "EDIT-BOUNDARY"
    and override.get("code") == "OUT-OF-SCOPE"
    and isinstance(override.get("reason"), str)
    and override.get("reason").strip()
    and override.get("authorized_by") == "user"
)

out_of_scope = []
for raw_path in paths:
    if not raw_path:
        continue
    rel_path = _relativize(raw_path, repo_root)
    if not any(_matches(rel_path, glob) for glob in scope_globs):
        out_of_scope.append(raw_path)

if not out_of_scope:
    sys.exit(0)

if override_ok:
    print(f"OVERRIDE\t{out_of_scope[0]}\t{override.get('reason', '')}")
    sys.exit(0)

print(f"DENY\t{out_of_scope[0]}")
sys.exit(0)
PY
)

if [ -z "$RESULT" ]; then
    _hb allow
    exit 0
fi

KIND="${RESULT%%$'\t'*}"
REST="${RESULT#*$'\t'}"

if [ "$KIND" = "OVERRIDE" ]; then
    TARGET_PATH="${REST%%$'\t'*}"
    OVERRIDE_REASON="${REST#*$'\t'}"
    _hb allow
    # Audit the honored override into the same gate_blocks.jsonl sink,
    # "decision":"override" per the N12 typed-override design (§3 Audit
    # logging) — a distinct bucket from "block", never merged into it.
    {
        _sink="${NEXUS_GATE_BLOCKS_PATH:-}"
        if [ -z "$_sink" ]; then
            _sink="$(cd "${HOOKS_DIR}/../.." && pwd)/.memory/files/gate_blocks.jsonl"
        fi
        _ts="$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || echo "")"
        mkdir -p "$(dirname "$_sink")" 2>/dev/null
        jq -cn \
            --arg ts "$_ts" \
            --arg ev "PreToolUse" \
            --arg hk "edit-boundary-impact-gate" \
            --arg cd "OUT-OF-SCOPE" \
            --arg rs "Override honored for '${TARGET_PATH}'" \
            --arg orr "$OVERRIDE_REASON" \
            '{"ts":$ts,"event":$ev,"hook":$hk,"code":$cd,"reason":$rs,"decision":"override","override_reason":$orr,"authorized_by":"user"}' \
            >> "$_sink" 2>/dev/null
    } 2>/dev/null || true
    exit 0
fi

TARGET_PATH="$REST"

REASON="[GATE:EDIT-BOUNDARY/OUT-OF-SCOPE] Write to '${TARGET_PATH}' is blocked: it falls outside this leaf's declared write_scope; if this edit is genuinely required, retry the same call with tool_input.override={gate:\"EDIT-BOUNDARY\",code:\"OUT-OF-SCOPE\",reason:\"<why>\",authorized_by:\"user\"} or return NEXUS:NEEDS-DECISION."
jq -cn --arg r "$REASON" \
    '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":$r}}'
printf '%s\n' "$REASON" >&2
_hb deny
# Best-effort telemetry — mirrors the canonical gate_deny() sink write
# exactly (same schema/path resolution), inlined here (not sourced) since
# this hook stays standalone-safe. Must NOT change exit/stdout/stderr.
{
    _sink="${NEXUS_GATE_BLOCKS_PATH:-}"
    if [ -z "$_sink" ]; then
        _sink="$(cd "${HOOKS_DIR}/../.." && pwd)/.memory/files/gate_blocks.jsonl"
    fi
    _ts="$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || echo "")"
    _reason_trunc="${REASON:0:200}"
    mkdir -p "$(dirname "$_sink")" 2>/dev/null
    jq -cn \
        --arg ts "$_ts" \
        --arg ev "PreToolUse" \
        --arg hk "edit-boundary-impact-gate" \
        --arg cd "OUT-OF-SCOPE" \
        --arg rs "$_reason_trunc" \
        '{"ts":$ts,"event":$ev,"hook":$hk,"code":$cd,"reason":$rs}' \
        >> "$_sink" 2>/dev/null
} 2>/dev/null || true
exit 2
