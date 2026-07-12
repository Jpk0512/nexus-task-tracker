#!/usr/bin/env bash
# PreToolUse hook (intended matcher: Task|TeamCreate|Agent, LAST in that group) —
# default-deny backstop for the dispatch-shape parsing contract (R1-T10 /
# NATIVE-3-10). broker-gate.py, skills-required-guard.sh,
# persona-alias-resolver.sh and dispatch-announce.sh each independently parse
# subagent_type / agent_type out of the PreToolUse payload; if the harness ever
# renames those fields, EVERY one of those parsers silently returns "" and every
# one of them fails OPEN (empty persona -> "not a dispatch, exit 0" in each). This
# hook is the single chokepoint that turns that silent-empty case into a loud
# deny instead of letting an ungoverned dispatch through.
#
# NOTE: the live settings.json matcher group for this hook (and its four
# siblings) is already wired as "Task|TeamCreate|Agent" — the Wire phase
# widened it from the earlier "Task|TeamCreate"-only gap (NATIVE-3-10), so
# this hook now fires on real Agent-tool dispatches too, not just Task/TeamCreate.
#
# Persona-resolution discipline mirrors broker-gate.py's _dispatch_facts
# EXACTLY (lines ~198-253 there): nested tool_input/input dict found ->
# subagent_type-or-agent_type from THAT nested dict, falling back only to
# top-level subagent_type (NEVER top-level agent_type — that field is always
# the CALLING agent's own identity, present on every PreToolUse event, and
# reading it as a dispatch target is what bricked a prior session). No nested
# dict found (flat/legacy/test payload) -> fall back to top-level
# subagent_type/agent_type too.
#
# Fails CLOSED (unlike persona-alias-resolver.sh, which fails open on absent
# subagent_type) — but ONLY for payloads whose tool_name is actually Task,
# TeamCreate, or Agent. Those tool names are BY CONSTRUCTION always a real
# agent-spawn attempt (TaskCreate/TaskUpdate are separate tool names,
# structurally excluded from this matcher, so there is no bookkeeping case to
# protect there) — so a Task/TeamCreate/Agent payload that cannot yield a
# persona must deny, not pass through. Any OTHER tool_name (Bash, Read,
# TaskList, an MCP tool, ...) is a silent pass no matter what fields it
# happens to carry — this is the literal regression guard against the prior
# incident, where a top-level `agent_type` (the CALLER's own identity, present
# on every PreToolUse event) was misread as a dispatch target.

set -euo pipefail

HOOKS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=gate-lib.sh
source "${HOOKS_DIR}/gate-lib.sh"
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
  emit_heartbeat "dispatch-shape-guard" "PreToolUse" "$decision" "$_elapsed" 2>/dev/null || true
}

INPUT=$(cat)

# ── Canonical dispatchable-persona roster ───────────────────────────────────
# Copied (not imported) from nexus-broker/src/broker/registry.py
# DISPATCHABLE_PERSONAS as of R1-T10. broker-gate.py keeps its own separate
# CODE_WRITING_PERSONAS list for the same cross-process-import-avoidance
# reason (a PreToolUse hook cannot rely on `broker` being on sys.path) — mirror
# that precedent here rather than importing registry.py.
# MUST BE KEPT IN SYNC WITH nexus-broker/src/broker/registry.py
# PERSONA_INTENTS keys whenever that dict changes.
PERSONA_JSON='["scout","forge-wire","forge-wire-pro","forge-ui","forge-ui-pro","pipeline-data","pipeline-data-pro","pipeline-async","pipeline-async-pro","atlas","hermes","lens","lens-fast","quill-ts","quill-py","palette"]'

RESULT=$(echo "$INPUT" | python3 -c "
import json, sys

try:
    payload = json.load(sys.stdin)
except Exception:
    print('DENY|<unparseable JSON>|unrecognized/renamed dispatch shape — cannot parse a target persona from this payload; refusing to let an ungoverned dispatch through.')
    sys.exit(0)

# The matcher wiring (Task|TeamCreate|Agent) is what actually gates whether
# this hook fires in production, but the hook body must be independently
# defensive: only tool_name in {Task, TeamCreate, Agent} is, by construction,
# a real agent-spawn attempt. Any other tool_name (Bash, Read, TaskList, an
# MCP tool, ...) carries a top-level agent_type that is ALWAYS the CALLING
# agent's own identity, never a dispatch target — reading it as one is the
# exact incident this hook exists to never repeat. So a non-dispatch
# tool_name is a silent pass regardless of what fields the payload happens
# to carry.
tool_name = str(payload.get('tool_name', '') or '')
if tool_name not in ('Task', 'TeamCreate', 'Agent'):
    print('ALLOW||')
    sys.exit(0)

# Mirror broker-gate.py._dispatch_facts EXACTLY: nested tool_input/input dict
# found first; top-level agent_type is NEVER read as the dispatch target (it
# is always the CALLING agent's own identity on every PreToolUse event).
nested = None
for key in ('tool_input', 'input'):
    candidate = payload.get(key)
    if isinstance(candidate, dict):
        nested = candidate
        break
tool_input = nested if nested is not None else payload

if nested is not None:
    persona = (
        tool_input.get('subagent_type', '')
        or tool_input.get('agent_type', '')
        or payload.get('subagent_type', '')
    )
    team_name = str(tool_input.get('team_name', '') or payload.get('team_name', '')).strip()
else:
    persona = (
        tool_input.get('subagent_type', '')
        or tool_input.get('agent_type', '')
        or payload.get('subagent_type', '')
        or payload.get('agent_type', '')
    )
    team_name = str(tool_input.get('team_name', '') or payload.get('team_name', '')).strip()

persona = str(persona or '').lower().strip()

if not persona and not team_name:
    print('DENY|<none>|unrecognized/renamed dispatch shape — cannot parse a target persona from this payload; refusing to let an ungoverned dispatch through.')
    sys.exit(0)

if persona and persona not in json.loads('$PERSONA_JSON'):
    print(f'DENY|{persona}|unregistered persona \"{persona}\" is not in the broker dispatchable-persona roster (nexus-broker/src/broker/registry.py DISPATCHABLE_PERSONAS) — refusing to dispatch to an unknown/typo\'d persona.')
    sys.exit(0)

print('ALLOW||')
")

STATUS="${RESULT%%|*}"

if [[ "$STATUS" == "ALLOW" ]]; then
    _hb allow
    exit 0
fi

# STATUS == "DENY" — remaining fields are persona|reason (persona may be empty).
REST="${RESULT#*|}"
REASON="${REST#*|}"

_hb deny
gate_deny PreToolUse "DISPATCH-SHAPE/UNRECOGNIZED" "$REASON"
