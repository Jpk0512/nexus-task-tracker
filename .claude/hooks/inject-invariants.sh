#!/usr/bin/env bash
# SessionStart hook: inject the INVARIANTS digest as additionalContext.
#
# Reads the SINGLE canonical digest .claude/INVARIANTS.md (never inline a copy)
# and emits the documented hookSpecificOutput object on stdout so the verbatim
# HARD RULES are pinned at the head of every new session (SOTA 3.6/3.7).
#
# The digest is read by python3 read_text() (NOT shell $(cat ...), which strips
# trailing newlines) so this SessionStart copy is BYTE-IDENTICAL to the
# UserPromptSubmit re-injection copy emitted by context-reset-monitor.py
# (NoLiMa 2502.05167: load-bearing tokens must never drift, not even by 1 byte)
# — WHEN .claude/sessionstart-cap.enabled is ABSENT.
#
# CAPPED MODE (R5/N45): when the flag is present, this SessionStart copy emits
# a <=1000-token DIGEST OF THE DIGEST instead. The live (Plexus) and package
# (Nexus) INVARIANTS.md files have DIFFERENT section names/structure (hand-
# reconciled docs, not twins) — this hook is byte-identical across both trees,
# so the selection rule is deliberately GENERIC rather than hardcoded to
# either doc's section names:
#   - ALWAYS keep the opening "===...===" header, the "IDENTITY:" paragraph,
#     the "COMPLETION MARKERS" paragraph, and the closing "=== remember...==="
#     line — the 4 anchors present, by convention, in every INVARIANTS.md
#     variant.
#   - Fill the remaining token budget with the OTHER paragraphs, in their
#     original document order (each doc already orders its own content by
#     priority), until the budget is spent.
#   - Whatever doesn't fit is named (not silently dropped) in a trailing
#     pointer at the full file + the skills that already restate it.
# Flag ABSENT => byte-for-byte the original full-file injection (no-op merge
# to main until the flag is created separately). context-reset-monitor.py's
# mid-session reinjection is intentionally OUT OF SCOPE here (this task caps
# "session-start injection" only) — full unification of the two copies is
# N47's broker JIT surface, not this node.
#
# Fail safe: if the digest file is missing/unreadable, emit nothing and exit 0
# (a missing digest is a wiring bug, not a place to improvise a paraphrase).
set -euo pipefail

# Resolve repo root from this script's location (.claude/hooks/ -> .claude/).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INVARIANTS_FILE="${SCRIPT_DIR}/../INVARIANTS.md"
# REPO_ROOT is overridable (tests) purely for locating the cap flag; the
# digest file itself always resolves from this script's own location above.
REPO_ROOT="${REPO_ROOT:-$(cd "${SCRIPT_DIR}/../.." && pwd)}"
CAP_FLAG="${NEXUS_SESSIONSTART_CAP_FLAG:-${REPO_ROOT}/.claude/sessionstart-cap.enabled}"

# Fail safe: no readable digest -> emit nothing, exit 0.
if [ ! -r "${INVARIANTS_FILE}" ]; then
  exit 0
fi

CAPPED=0
[ -f "${CAP_FLAG}" ] && CAPPED=1

# Emit as SessionStart additionalContext. python3 reads the file directly
# (read_text) and handles JSON string escaping; passing the PATH (not the
# contents) avoids any shell mangling of newlines/quotes.
CAPPED="$CAPPED" python3 - "${INVARIANTS_FILE}" <<'PYEOF'
import json, os, sys
from pathlib import Path

try:
    digest = Path(sys.argv[1]).read_text(encoding="utf-8")
except OSError:
    sys.exit(0)

capped = os.environ.get("CAPPED") == "1"

# Optional-paragraph fill budget in chars (chars/3.6 estimator, matching
# tools/context_budget.py) — reserved so the ALWAYS-kept anchors + the
# trailing pointer sentence stay comfortably under the 1000-token ceiling.
CONTENT_BUDGET_CHARS = 3000


def _keep_always(head):
    return (
        head.startswith("===")
        or head.startswith("IDENTITY:")
        or head.startswith("COMPLETION MARKERS")
    )


def _build_capped_digest(full_text):
    text = full_text
    if text.lstrip().startswith("<!--"):
        end = text.find("-->")
        if end != -1:
            text = text[end + 3:]
    paragraphs = [p.strip() for p in text.strip().split("\n\n") if p.strip()]
    if len(paragraphs) <= 1:
        return full_text

    always, optional = [], []
    for p in paragraphs:
        (always if _keep_always(p.splitlines()[0]) else optional).append(p)

    used = sum(len(p) for p in always)
    kept_optional, dropped_names = [], []
    for p in optional:
        if used + len(p) <= CONTENT_BUDGET_CHARS:
            kept_optional.append(p)
            used += len(p)
        else:
            dropped_names.append(p.splitlines()[0].split(":")[0].strip())

    kept_ids = set(id(p) for p in always) | set(id(p) for p in kept_optional)
    ordered_kept = [p for p in paragraphs if id(p) in kept_ids]

    if not dropped_names:
        return "\n\n".join(ordered_kept)

    pointer = (
        "DIGEST-CAPPED (R5/N45, .claude/sessionstart-cap.enabled): "
        + ", ".join(dropped_names)
        + " omitted here to stay <=1000 tokens — read the full "
        ".claude/INVARIANTS.md directly, or `Skill plexus-protocol` / "
        "`Skill dispatch` / `Skill team-routing`, before relying on them."
    )
    return "\n\n".join(ordered_kept + [pointer])


if capped:
    digest = _build_capped_digest(digest)

print(json.dumps({
    "hookSpecificOutput": {
        "hookEventName": "SessionStart",
        "additionalContext": digest,
    }
}))
PYEOF
