#!/usr/bin/env bash
# PreToolUse hook (matcher: Task) — enforces base-name retirement.
# The base names forge / pipeline / quill are RETIRED (not dispatch targets);
# the broker registry omits them (see nexus-broker/src/broker/registry.py).
# This hook is the dispatch-time half of that contract: a bare base name is
# either redirected to its split persona (when the brief carries scope hints) or
# DENIED with exit 2 (when it cannot be resolved) — it is never let through as
# itself. Permanent enforcement, not a temporary shim. Agreement with the broker
# is locked by nexus-broker/tests/test_base_name_retirement.py.
# Fails open if JSON parse fails or subagent_type is absent.

set -euo pipefail

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
    forge)
        if echo "$BRIEF_LOWER" | grep -qE 'app/components|app/\(routes\)|tremor|tailwind|rsc page|ui component'; then
            CANONICAL="forge-ui"
            REASON="brief references app/components or RSC page work — maps to forge-ui"
        elif echo "$BRIEF_LOWER" | grep -qE 'app/api|app/actions|server action|ai sdk|duckdb read'; then
            CANONICAL="forge-wire"
            REASON="brief references app/api or server action work — maps to forge-wire"
        else
            python3 -c "
import json, sys
reason = ('Stale persona name \"forge\" — cannot resolve to forge-ui or '
          'forge-wire from brief. Add explicit scope to the brief (mention '
          'app/components / RSC page for forge-ui, or app/api / server action '
          'for forge-wire) or dispatch the correct split persona directly. '
          'NEXUS:NEEDS-DECISION: brief does not mention app/components, '
          'app/api, or server actions — cannot auto-route.')
out = {
    'hookSpecificOutput': {
        'hookEventName': 'PreToolUse',
        'permissionDecision': 'deny',
        'permissionDecisionReason': reason,
    }
}
print(json.dumps(out))
sys.stderr.write(reason + '\n')
"
            exit 2
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
            python3 -c "
import json, sys
reason = ('Stale persona name \"pipeline\" — cannot resolve to pipeline-data '
          'or pipeline-async from brief. Add explicit scope (transforms / '
          'writers / embeddings for pipeline-data, or workers / dramatiq / '
          'clients for pipeline-async) or dispatch the split persona directly. '
          'NEXUS:NEEDS-DECISION: brief does not mention transforms, writers, '
          'workers, or dramatiq.')
out = {
    'hookSpecificOutput': {
        'hookEventName': 'PreToolUse',
        'permissionDecision': 'deny',
        'permissionDecisionReason': reason,
    }
}
print(json.dumps(out))
sys.stderr.write(reason + '\n')
"
            exit 2
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
            python3 -c "
import json, sys
reason = ('Stale persona name \"quill\" — cannot resolve to quill-ts or '
          'quill-py from brief. Add explicit scope (.ts/.tsx / vitest for '
          'quill-ts, or .py / pytest for quill-py) or dispatch the split '
          'persona directly. NEXUS:NEEDS-DECISION: brief does not mention '
          '.ts/.tsx or .py file extensions.')
out = {
    'hookSpecificOutput': {
        'hookEventName': 'PreToolUse',
        'permissionDecision': 'deny',
        'permissionDecisionReason': reason,
    }
}
print(json.dumps(out))
sys.stderr.write(reason + '\n')
"
            exit 2
        fi
        ;;
    *)
        # Not a stale name — pass through
        exit 0
        ;;
esac

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
