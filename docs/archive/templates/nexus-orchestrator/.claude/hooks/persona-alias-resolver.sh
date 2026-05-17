#!/usr/bin/env bash
# PreToolUse hook (matcher: Task) — rewrites stale persona names to split variants.
# Fails open if JSON parse fails or subagent_type is absent.
# Remove after 30 days post-Phase-B when all briefs reference split names directly.

set -euo pipefail

INPUT=$(cat)

# Extract subagent_type from tool input JSON
SUBAGENT_TYPE=$(echo "$INPUT" | python3 -c "
import json, sys
try:
    data = json.load(sys.stdin)
    # Tool input may be nested under 'input' key in PreToolUse event
    tool_input = data.get('input', data)
    print(tool_input.get('subagent_type', ''))
except Exception:
    print('')
" 2>/dev/null || true)

if [[ -z "$SUBAGENT_TYPE" ]]; then
    exit 0
fi

# Extract brief text for routing hint
BRIEF=$(echo "$INPUT" | python3 -c "
import json, sys
try:
    data = json.load(sys.stdin)
    tool_input = data.get('input', data)
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
out = {
    'hookSpecificOutput': json.dumps({
        'decision': 'block',
        'reason': 'Stale persona name \"forge\" — cannot resolve to forge-ui or forge-wire from brief. Add explicit scope to brief or use the correct split persona. See docs/agents/PERSONA_BOUNDARIES.md.',
        'additionalContext': 'NEXUS:NEEDS-DECISION: brief does not mention app/components, app/api, or server actions — cannot auto-route.'
    })
}
print(json.dumps(out))
"
            exit 0
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
out = {
    'hookSpecificOutput': json.dumps({
        'decision': 'block',
        'reason': 'Stale persona name \"pipeline\" — cannot resolve to pipeline-data or pipeline-async from brief. See docs/agents/PERSONA_BOUNDARIES.md.',
        'additionalContext': 'NEXUS:NEEDS-DECISION: brief does not mention transforms, writers, workers, or dramatiq.'
    })
}
print(json.dumps(out))
"
            exit 0
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
out = {
    'hookSpecificOutput': json.dumps({
        'decision': 'block',
        'reason': 'Stale persona name \"quill\" — cannot resolve to quill-ts or quill-py from brief. See docs/agents/PERSONA_BOUNDARIES.md.',
        'additionalContext': 'NEXUS:NEEDS-DECISION: brief does not mention .ts/.tsx or .py file extensions.'
    })
}
print(json.dumps(out))
"
            exit 0
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
    'hookSpecificOutput': json.dumps({
        'decision': 'warn',
        'rewritten_subagent_type': canonical,
        'additionalContext': 'persona-alias-resolver rewrote stale name: ' + reason + '. Update your brief to use \"' + canonical + '\" directly.'
    })
}
print(json.dumps(out))
" "$CANONICAL" "$REASON"
