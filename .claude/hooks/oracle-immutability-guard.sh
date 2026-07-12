#!/usr/bin/env bash
# PreToolUse hook: deny writes to paths protected by the active task's
# do_not_touch oracle-immutability boundary BEFORE they land.
#
# Reads approved_brief.do_not_touch globs from broker_state.json (same state
# file / path resolution as do-not-touch-guard.sh) and cross-checks the WRITE
# TARGET PATH(s) of this call against them, using the exact glob-matching
# semantics of do-not-touch-guard.sh's _matches (ported faithfully, not
# reinvented): trailing-slash directory-prefix globs, fnmatch, and bare
# directory-name subtree matching.
#
# Covers Write, Edit, MultiEdit, NotebookEdit — same extraction shape as
# secret-path-guard.sh:
#   Write / Edit        -> top-level `file_path` or `path`
#   NotebookEdit         -> top-level `notebook_path`
#   MultiEdit             -> `edits[].file_path` (array of objects)
#
# This gate needs ZERO persona resolution — it only compares the write
# target path against do_not_touch globs, so it is structurally immune to
# the agent_type/subagent_type incident class entirely.
#
# IMPORTANT (defense-in-depth, do not remove do-not-touch-guard.sh): this
# PreToolUse gate only sees Write/Edit/MultiEdit/NotebookEdit tool calls, so
# it CANNOT catch a do_not_touch violation made via Bash (sed -i, cat >,
# git apply, etc.). do-not-touch-guard.sh's SubagentStop advisory check is
# what still catches THAT case after the fact — it stays as a backstop.
#
# On any match: emit canonical deny JSON (exit 2).
# On no match, or no active approved brief: silent exit 0.

set -euo pipefail

HOOKS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=heartbeat-emitter.sh
# set -e (bash 3.2 on macOS) treats a failed `source` of a
# missing file as fatal even inside `|| { ... }` — guard with an
# explicit -f test instead so a missing heartbeat-emitter.sh never
# aborts the gate (best-effort telemetry must never break allow/deny).
if [ -f "${HOOKS_DIR}/heartbeat-emitter.sh" ]; then
    # shellcheck source=heartbeat-emitter.sh
    source "${HOOKS_DIR}/heartbeat-emitter.sh" 2>/dev/null || true
fi
# Belt-and-suspenders: even if the source succeeded but the file did not define
# both helpers (truncated/edited), guarantee they exist before first use.
command -v ms_now >/dev/null 2>&1 || ms_now() { python3 -c "import time; print(int(time.time()*1000))" 2>/dev/null || echo 0; }
command -v emit_heartbeat >/dev/null 2>&1 || emit_heartbeat() { :; }

_HB_START_MS=$(ms_now 2>/dev/null || echo 0)
_hb() {
  local decision="$1"
  local _elapsed=$(( $(ms_now 2>/dev/null || echo 0) - _HB_START_MS ))
  emit_heartbeat "oracle-immutability-guard" "PreToolUse" "$decision" "$_elapsed" 2>/dev/null || true
}

INPUT=$(cat)

# ── Resolve repo root + state path, extract paths, match do_not_touch globs ──
# All in one Python call. INPUT/repo-root override / state-path override are
# passed via env vars to avoid the two-heredoc stdin conflict.
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


def _read_do_not_touch(state_path):
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(state, dict):
        return []
    brief = state.get("approved_brief")
    if not isinstance(brief, dict):
        return []
    globs = brief.get("do_not_touch")
    if not isinstance(globs, list):
        return []
    return [g for g in globs if isinstance(g, str) and g.strip()]


def _matches(path, glob):
    """Ported verbatim from do-not-touch-guard.sh's _matches — do not
    reinvent matching semantics."""
    normglob = glob.strip()
    if normglob.endswith("/"):
        prefix = normglob.rstrip("/")
        return path == prefix or path.startswith(prefix + "/")
    if fnmatch.fnmatch(path, normglob):
        return True
    if "*" not in normglob and "?" not in normglob and "[" not in normglob:
        return path == normglob or path.startswith(normglob + "/")
    return False


raw = os.environ.get("HOOK_INPUT", "")
try:
    payload = json.loads(raw)
except Exception:
    sys.exit(0)

repo_root = _repo_root()
globs = _read_do_not_touch(_state_path(repo_root))
if not globs:
    sys.exit(0)

tool_input = payload.get("tool_input", {})
paths = []

# Write / Edit: top-level "file_path" or "path"; NotebookEdit: "notebook_path"
for key in ("file_path", "path", "notebook_path"):
    val = tool_input.get(key)
    if val:
        paths.append(val)

# MultiEdit: edits[].file_path
for edit in tool_input.get("edits", []):
    if isinstance(edit, dict) and "file_path" in edit:
        paths.append(edit["file_path"])

def _relativize(raw_path, repo_root):
    """Absolute in-repo paths must be compared to do_not_touch globs in their
    REPO-RELATIVE form, since globs are authored repo-relative (e.g.
    "nexus-package/") and would never match an absolute path via _matches'
    prefix/fnmatch logic otherwise. Paths outside repo_root degrade to the
    raw string unchanged (still safe to _matches against, just never able to
    match a repo-relative glob — no crash, no false positive)."""
    p = Path(raw_path)
    if not p.is_absolute():
        return raw_path
    try:
        return str(p.resolve().relative_to(repo_root.resolve()))
    except ValueError:
        return raw_path


for raw_path in paths:
    if not raw_path:
        continue
    rel_path = _relativize(raw_path, repo_root)
    for glob in globs:
        if _matches(rel_path, glob):
            print(f"{raw_path}\t{glob}")
            sys.exit(0)
PY
)

if [ -z "$RESULT" ]; then
    _hb allow
    exit 0
fi

TARGET_PATH="${RESULT%%$'\t'*}"
MATCHED_GLOB="${RESULT#*$'\t'}"

REASON="[GATE:ORACLE-IMMUTABILITY/WRITE-DENIED] Write to '${TARGET_PATH}' is blocked: it matches do_not_touch glob '${MATCHED_GLOB}', protected by the active task's do_not_touch oracle-immutability boundary; if this edit is genuinely required, escalate via NEXUS:NEEDS-DECISION rather than editing the oracle directly."
jq -cn --arg r "$REASON" \
    '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":$r}}'
printf '%s\n' "$REASON" >&2
_hb deny
# OPT-033-style best-effort telemetry — mirrors the canonical gate_deny() sink
# write exactly (same schema/path resolution), inlined here (not sourced) since
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
        --arg hk "oracle-immutability-guard" \
        --arg cd "WRITE-DENIED" \
        --arg rs "$_reason_trunc" \
        '{"ts":$ts,"event":$ev,"hook":$hk,"code":$cd,"reason":$rs}' \
        >> "$_sink" 2>/dev/null
} 2>/dev/null || true
exit 2
