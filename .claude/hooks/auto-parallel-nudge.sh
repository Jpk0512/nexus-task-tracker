#!/usr/bin/env bash
# UserPromptSubmit hook — ADVISORY prefer-workflows nudge.
#
# When a user prompt looks like delegation / implementation work (multi-step,
# imperative verbs like implement/fix/build/add, or an enumerated list of asks),
# inject a brief additionalContext nudge suggesting a Workflow even for a single
# task: a Workflow buys a Lens review stage, is monitorable, and lets agents
# coordinate. The nudge is NEVER forced — it is one sentence of advice.
#
# Discipline:
#   - exit 0 ALWAYS; this hook NEVER blocks and NEVER errors a turn.
#   - emits ONLY hookSpecificOutput.additionalContext (never permissionDecision).
#   - LOW NOISE: stays silent on trivial / conversational / pure-question prompts
#     (greetings, "what is …?", "why …?", one-liners with no action verb).
#   - any parse failure / unexpected shape => emit nothing, exit 0 (fail-open).
#
# Python-bodied .sh (bash shebang + python3 heredoc, like inject-invariants.sh):
# this body runs un-shimmed under the project's ambient python3, so it MUST stay
# 3.9-import-safe (no datetime.UTC, no def-time PEP-604 unions, no match/case).
set -euo pipefail

INPUT=$(cat)

# The heredoc below occupies python3's stdin, so the JSON payload is handed to
# the body via an env var (NEXUS_NUDGE_PAYLOAD), not stdin. It extracts .prompt,
# classifies, and prints the additionalContext JSON (or nothing).
NEXUS_NUDGE_PAYLOAD="$INPUT" python3 - <<'PYEOF'
import json
import os
import re
import sys

NUDGE = (
    "[auto-parallel-nudge] This looks like delegation / implementation work. "
    "Consider authoring a Workflow rather than working inline or firing a lone "
    "single dispatch — a Workflow gives you a built-in Lens review stage, is "
    "monitorable, and lets agents coordinate. It is valuable even for a single, "
    "simple task and is never forced; keep fan-out width modest to avoid token "
    "waste. Advisory only — not blocking."
)


def _read_prompt():
    """Return the user prompt string, or None on any malformed input (fail-open)."""
    raw = os.environ.get("NEXUS_NUDGE_PAYLOAD", "")
    try:
        payload = json.loads(raw)
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    prompt = payload.get("prompt", "")
    if not isinstance(prompt, str):
        return None
    return prompt


# Imperative / delegation verbs that signal "do work", as whole words. Kept
# conservative: these strongly imply build/change work, not a question.
_ACTION_VERB_RE = re.compile(
    r"\b("
    r"implement|build|create|add|fix|refactor|migrate|rewrite|"
    r"write|wire|integrate|deploy|generate|update|delete|remove|"
    r"rename|extract|split|merge|optimi[sz]e|harden|patch|"
    r"audit|investigate|diagnose|debug|review|verify|test"
    r")\b",
    re.IGNORECASE,
)

# Conversational / question openers that should stay silent even if a stray
# action verb appears ("what does build do?" is a question, not a work order).
_QUESTION_OPENER_RE = re.compile(
    r"^\s*(what|why|how|when|where|who|which|is|are|does|do|can|could|"
    r"should|would|will|did|hi|hey|hello|thanks|thank you)\b",
    re.IGNORECASE,
)


def _looks_like_delegation(prompt):
    """True when the prompt reads as multi-step / implementation / list work.

    Low-noise by design: a short conversational or pure-question prompt returns
    False even if it contains an action verb in passing.
    """
    text = prompt.strip()
    if not text:
        return False

    # Pure question / greeting that ends with '?' and has no enumerated list:
    # treat as conversational, stay silent.
    is_question_opener = bool(_QUESTION_OPENER_RE.match(text))

    # Enumerated / bulleted list of asks => multi-step work order.
    has_list = bool(
        re.search(r"(?m)^\s*(?:[-*]|\(?[0-9a-d]\)|[0-9]+[.)])\s+\S", text)
    )

    has_action_verb = bool(_ACTION_VERB_RE.search(text))

    # A list of asks is delegation work regardless of phrasing.
    if has_list and (has_action_verb or len(text) >= 60):
        return True

    # A trailing-question-only prompt with no list: stay silent.
    if is_question_opener and text.endswith("?") and not has_list:
        return False

    # Substantive, action-verbed, non-trivial prompt => nudge.
    if has_action_verb and len(text) >= 40:
        return True

    return False


def main():
    prompt = _read_prompt()
    if prompt is None:
        return
    if not _looks_like_delegation(prompt):
        return
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": NUDGE,
        }
    }))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Fail-open: an advisory nudge must never break a turn.
        pass
    sys.exit(0)
PYEOF

exit 0
