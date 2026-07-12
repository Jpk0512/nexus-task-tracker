#!/usr/bin/env bash
# PreToolUse hook (matcher: Task) — enforces base-name retirement.
# The base names forge / pipeline / quill are RETIRED (not dispatch targets);
# the broker registry omits them (see nexus-broker/src/broker/registry.py).
# This hook is the dispatch-time half of that contract: a bare base name is
# either redirected to its split persona (when the brief carries scope hints) or
# DENIED with exit 2 (when it cannot be resolved) — it is never let through as
# itself. Permanent enforcement, not a temporary shim. Agreement with the broker
# is locked by nexus-broker/tests/test_base_name_retirement.py.
#
# R2-T03 FIX-4: forge-ui-pro / forge-wire-pro / pipeline-data-pro /
# pipeline-async-pro are ALSO retired dispatch NAMES — each base/pro pair
# merged into one tier-parameterized source. Unlike the bare base names above,
# these always redirect unconditionally (no brief-scope resolution needed: the
# merged target is unambiguous from the name itself) via additionalContext,
# never a bare deny. Agreement with the broker is locked by
# nexus-broker/tests/test_pro_variant_retirement.py.
# Fails open if JSON parse fails or subagent_type is absent.

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
  emit_heartbeat "persona-alias-resolver" "PreToolUse" "$decision" "$_elapsed" 2>/dev/null || true
}

INPUT=$(cat)

# Extract the persona from tool input JSON. Read BOTH the Task shape
# (subagent_type) AND the Agent/Team shape (agent_type) — whichever spawn
# surface the harness presents (P6-01 / DW-02..05). A plain TaskCreate/TaskUpdate
# payload carries NEITHER field, so this resolves to empty and the hook exits 0
# below (silent pass) — alias-rewrite must never gate task bookkeeping.
SUBAGENT_TYPE=$(echo "$INPUT" | python3 -c "
import json, sys
try:
    data = json.load(sys.stdin)
    # Tool input may be nested under 'input' key in PreToolUse event
    tool_input = data.get('tool_input', data.get('input', data))
    print(
        tool_input.get('subagent_type', '')
        or tool_input.get('agent_type', '')
        or data.get('subagent_type', '')
        or data.get('agent_type', '')
    )
except Exception:
    print('')
" 2>/dev/null || true)

if [[ -z "$SUBAGENT_TYPE" ]]; then
    _hb allow
    exit 0
fi

# Extract brief text for routing hint. Same brief fields carry on both the Task
# and the Agent/Team teammate shape, so description/prompt cover both.
BRIEF=$(echo "$INPUT" | python3 -c "
import json, sys
try:
    data = json.load(sys.stdin)
    tool_input = data.get('tool_input', data.get('input', data))
    print(tool_input.get('description', '') + ' ' + str(tool_input.get('prompt', '')))
except Exception:
    print('')
" 2>/dev/null || true)

BRIEF_LOWER=$(echo "$BRIEF" | tr '[:upper:]' '[:lower:]')

case "$SUBAGENT_TYPE" in
    forge-ui-pro)
        CANONICAL="forge-ui"
        REASON="forge-ui-pro merged into forge-ui (tier=pro) — R2-T03 FIX-4"
        ;;
    forge-wire-pro)
        CANONICAL="forge-wire"
        REASON="forge-wire-pro merged into forge-wire (tier=pro) — R2-T03 FIX-4"
        ;;
    pipeline-data-pro)
        CANONICAL="pipeline-data"
        REASON="pipeline-data-pro merged into pipeline-data (tier=pro) — R2-T03 FIX-4"
        ;;
    pipeline-async-pro)
        CANONICAL="pipeline-async"
        REASON="pipeline-async-pro merged into pipeline-async (tier=pro) — R2-T03 FIX-4"
        ;;
    forge)
        if echo "$BRIEF_LOWER" | grep -qE 'app/components|app/\(routes\)|tremor|tailwind|rsc page|ui component'; then
            CANONICAL="forge-ui"
            REASON="brief references app/components or RSC page work — maps to forge-ui"
        elif echo "$BRIEF_LOWER" | grep -qE 'app/api|app/actions|server action|ai sdk|duckdb read'; then
            CANONICAL="forge-wire"
            REASON="brief references app/api or server action work — maps to forge-wire"
        else
            _hb deny
            gate_deny PreToolUse "PERSONA/STALE-FORGE" 'Stale persona name "forge" — cannot resolve to forge-ui or forge-wire from brief. Add explicit scope to the brief (mention app/components / RSC page for forge-ui, or app/api / server action for forge-wire) or dispatch the correct split persona directly. NEXUS:NEEDS-DECISION: brief does not mention app/components, app/api, or server actions — cannot auto-route.'
        fi
        ;;
    pipeline)
        if echo "$BRIEF_LOWER" | grep -qE 'transforms|writers|embeddings|polars|duckdb write'; then
            CANONICAL="pipeline-data"
            REASON="brief references transforms/writers/embeddings — maps to pipeline-data"
        elif echo "$BRIEF_LOWER" | grep -qE 'workers|dramatiq|tableau|redis|async|clients'; then
            CANONICAL="pipeline-async"
            REASON="brief references workers/dramatiq/tableau — maps to pipeline-async"
        else
            _hb deny
            gate_deny PreToolUse "PERSONA/STALE-PIPELINE" 'Stale persona name "pipeline" — cannot resolve to pipeline-data or pipeline-async from brief. Add explicit scope (transforms / writers / embeddings for pipeline-data, or workers / dramatiq / clients for pipeline-async) or dispatch the split persona directly. NEXUS:NEEDS-DECISION: brief does not mention transforms, writers, workers, or dramatiq.'
        fi
        ;;
    quill)
        if echo "$BRIEF_LOWER" | grep -qE '\.ts|\.tsx|vitest|react testing|typescript'; then
            CANONICAL="quill-ts"
            REASON="brief references .ts/.tsx or vitest — maps to quill-ts"
        elif echo "$BRIEF_LOWER" | grep -qE '\.py|pytest|polars fixture|python'; then
            CANONICAL="quill-py"
            REASON="brief references .py or pytest — maps to quill-py"
        else
            _hb deny
            gate_deny PreToolUse "PERSONA/STALE-QUILL" 'Stale persona name "quill" — cannot resolve to quill-ts or quill-py from brief. Add explicit scope (.ts/.tsx / vitest for quill-ts, or .py / pytest for quill-py) or dispatch the split persona directly. NEXUS:NEEDS-DECISION: brief does not mention .ts/.tsx or .py file extensions.'
        fi
        ;;
    *)
        # Not a stale name — pass through
        _hb allow
        exit 0
        ;;
esac

_hb allow

python3 -c "
import json, sys
canonical = sys.argv[1]
reason = sys.argv[2]
out = {
    'hookSpecificOutput': {
        'hookEventName': 'PreToolUse',
        'additionalContext': (
            'persona-alias-resolver: stale name maps to \"' + canonical + '\" ('
            + reason + '). A hook cannot rewrite subagent_type — re-dispatch '
            'this Task with subagent_type=\"' + canonical + '\" directly.'
        ),
    }
}
print(json.dumps(out))
" "$CANONICAL" "$REASON"
