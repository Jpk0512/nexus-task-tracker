#!/usr/bin/env bash
# SessionStart hook: inject the NEXUS INVARIANTS digest as additionalContext.
#
# Reads the SINGLE canonical digest .claude/INVARIANTS.md (never inline a copy)
# and emits the documented hookSpecificOutput object on stdout so the verbatim
# HARD RULES are pinned at the head of every new session (SOTA 3.6/3.7).
#
# The digest is read by python3 read_text() (NOT shell $(cat ...), which strips
# trailing newlines) so this SessionStart copy is BYTE-IDENTICAL to the
# UserPromptSubmit re-injection copy emitted by context-reset-monitor.py
# (NoLiMa 2502.05167: load-bearing tokens must never drift, not even by 1 byte).
#
# Fail safe: if the digest file is missing/unreadable, emit nothing and exit 0
# (a missing digest is a wiring bug, not a place to improvise a paraphrase).
set -euo pipefail

# Resolve repo root from this script's location (.claude/hooks/ -> .claude/).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INVARIANTS_FILE="${SCRIPT_DIR}/../INVARIANTS.md"

# Fail safe: no readable digest -> emit nothing, exit 0.
if [ ! -r "${INVARIANTS_FILE}" ]; then
  exit 0
fi

# Emit the verbatim digest as SessionStart additionalContext. python3 reads the
# file directly (read_text) and handles JSON string escaping; passing the PATH
# (not the contents) avoids any shell mangling of newlines/quotes.
python3 - "${INVARIANTS_FILE}" <<'PYEOF'
import json, sys
from pathlib import Path
try:
    digest = Path(sys.argv[1]).read_text(encoding="utf-8")
except OSError:
    sys.exit(0)
print(json.dumps({
    "hookSpecificOutput": {
        "hookEventName": "SessionStart",
        "additionalContext": digest,
    }
}))
PYEOF
