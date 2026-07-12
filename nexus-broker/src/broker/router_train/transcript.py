"""BOOTSTRAP / FALLBACK — transcript-mining labeler (00-DESIGN.md
'GROUND-TRUTH … BOOTSTRAP/FALLBACK').

Until the dispatch sidecar (PRIMARY ground-truth) accrues volume, mine the Claude
Code session transcripts under ``~/.claude/projects/*/<session_id>.jsonl`` for the
persona each prompt was actually routed to: every assistant ``Agent`` tool-use
carries ``input.subagent_type`` — the persona the orchestrator dispatched.

The label is assigned by INTRA-SESSION nearest-FOLLOWING alignment, NOT a naive
session-level join. A session-level join smears one persona across every prompt in
a multi-turn session; here each user prompt is aligned to the FIRST Agent dispatch
whose timestamp is >= the prompt's, within the same session.

To guarantee the emitted pairs are byte-for-byte the same shape as
``label.label()`` — and to inherit its ``label_status`` classification
(``ok`` / ``dropped_generic`` / ``quarantined_retired`` / ``quarantined_buggy``)
for free — this module builds synthetic dispatch rows from the mined
``subagent_type`` values and delegates to ``label.label()``, then stamps the
transcript provenance (``label_source="transcript_mining"``,
``label_confidence``) on the result. Only ``label_status == "ok"`` rows are
training-grade; the rest stay on the result for the check report and are excluded
by ``export()``.

Mining has no model guess (``pred_persona``), so ``label.label()`` leaves
``agree`` unknown (``None`` → absent) on this path — it is never fabricated as
``False``.

Schema-discriminator investigation (2026-06-21): Claude Code transcripts have no
reliable top-level structural flag that separates injected system messages from
genuine human-typed user turns. All str-content user-role records share
``userType='external'``; ``isMeta``, ``promptSource``, and
``hookAdditionalContext`` are all ``None`` for both injected and genuine turns.
The content-level marker denylist below is therefore the authoritative filter,
with the length gate as the backstop for paste-blobs/tool-dumps.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from broker.router_train.aggregate import prompt_hash
from broker.router_train.label import label

LABEL_SOURCE_TRANSCRIPT = "transcript_mining"
LABEL_CONFIDENCE_TRANSCRIPT = 0.8

LABEL_SOURCE_NO_DISPATCH = "transcript_no_dispatch"
LABEL_CONFIDENCE_NO_DISPATCH = 0.6

_AGENT_TOOL_NAMES = frozenset({"Agent", "Task"})

# Content-level markers that identify Claude Code injected messages (not genuine
# human-typed routing queries). Checked as substring membership — any hit rejects.
_INJECTED_MARKERS: tuple[str, ...] = (
    "<task-notification",
    "<system-reminder",
    "<command-name",
    "<local-command-stdout",
    "<command-message",
    "[ctx:",
    "tool_use_id",
    "Caveat: The messages below",
    "hook additional context",
    "<persona-",
    "<routing-pre-fill",
)

_MIN_GENUINE_LEN: int = 12
_MAX_GENUINE_LEN: int = 1500


def is_genuine_user_prompt(text: str) -> bool:
    """Return True iff ``text`` is a genuine human-typed routing query.

    Rejects:
    - Any string containing an injected-wrapper marker (task-notification,
      system-reminder, command-name, etc.) — these are Claude Code machinery
      messages injected into the user role, not real developer prompts.
    - Strings shorter than 12 chars (stripped) — too short to be a routing query.
    - Strings longer than 1500 chars — paste-blobs / tool-dumps, not routing queries.

    No structural flag in the Claude Code JSONL schema reliably discriminates
    injected from genuine user messages (all share userType='external', isMeta=None,
    promptSource=None); the marker+length denylist is the authoritative filter.
    """
    stripped = text.strip()
    if len(stripped) < _MIN_GENUINE_LEN:
        return False
    if len(text) > _MAX_GENUINE_LEN:
        return False
    return all(marker not in text for marker in _INJECTED_MARKERS)


def projects_root() -> Path:
    """The Claude Code transcript root, ``~/.claude/projects``."""
    return Path(os.path.expanduser("~")) / ".claude" / "projects"


def _event_ts(obj: dict[str, Any]) -> str:
    return str(obj.get("timestamp") or "")


def _session_id(obj: dict[str, Any], fallback: str) -> str:
    return str(obj.get("sessionId") or fallback)


def _iter_transcript_lines(path: Path) -> list[dict[str, Any]]:
    objs: list[dict[str, Any]] = []
    try:
        with path.open() as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(obj, dict):
                    objs.append(obj)
    except OSError:
        return objs
    return objs


def _extract_prompts_and_dispatches(
    objs: list[dict[str, Any]], default_session: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split a session's events into (capture-shaped records, synthetic dispatches).

    A user prompt becomes a record; each ``Agent``/``Task`` tool-use with a
    ``subagent_type`` becomes a synthetic dispatch carrying that persona. Both keep
    the line's top-level ``timestamp`` so ``label._align`` orders them correctly and
    picks the nearest-FOLLOWING dispatch per prompt.
    """
    records: list[dict[str, Any]] = []
    dispatches: list[dict[str, Any]] = []
    for obj in objs:
        message = obj.get("message")
        if not isinstance(message, dict):
            continue
        session_id = _session_id(obj, default_session)
        ts = _event_ts(obj)
        role = message.get("role")
        content = message.get("content")

        if role == "user" and isinstance(content, str) and is_genuine_user_prompt(content):
            records.append(
                {
                    "session_id": session_id,
                    "prompt": content,
                    "prompt_hash": prompt_hash(content),
                    "timestamp": ts,
                }
            )
            continue

        if role == "assistant" and isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") != "tool_use":
                    continue
                if block.get("name") not in _AGENT_TOOL_NAMES:
                    continue
                tool_input = block.get("input")
                if not isinstance(tool_input, dict):
                    continue
                persona = str(tool_input.get("subagent_type") or "").strip()
                if not persona:
                    continue
                dispatches.append(
                    {
                        "session_id": session_id,
                        "prompt_hash": "",
                        "dispatched_persona": persona,
                        "ts": ts,
                    }
                )
    return records, dispatches


def _mine_file(path: Path) -> list[dict[str, Any]]:
    objs = _iter_transcript_lines(path)
    if not objs:
        return []
    default_session = path.stem
    records, dispatches = _extract_prompts_and_dispatches(objs, default_session)
    if not records or not dispatches:
        return []

    pairs = label(records, dispatches)
    for pair in pairs:
        pair["label_source"] = LABEL_SOURCE_TRANSCRIPT
        pair["label_confidence"] = LABEL_CONFIDENCE_TRANSCRIPT
        pair["source_project"] = pair.get("source_project") or str(path.parent.name)
    return pairs


def mine_transcripts(root: Path | None = None) -> list[dict[str, Any]]:
    """Mine every ``~/.claude/projects/*/<session_id>.jsonl`` for labeled pairs.

    Each prompt is aligned to its nearest-FOLLOWING ``Agent`` dispatch within the
    SAME session (multi-turn safe). Emits the same pair shape as ``label.label()``
    with ``label_source="transcript_mining"`` and ``label_confidence=0.8``.
    """
    base = root or projects_root()
    if not base.exists():
        return []
    pairs: list[dict[str, Any]] = []
    for path in sorted(base.glob("*/*.jsonl")):
        pairs.extend(_mine_file(path))
    return pairs


def _mine_no_dispatch_file(path: Path) -> list[dict[str, Any]]:
    """Yield one pair per user prompt that has NO following Agent/Task dispatch
    BEFORE the next user prompt in that session.

    Scans events in timestamp order.  For each user prompt, collects any
    Agent/Task tool_use events that occur AFTER the prompt's timestamp AND before
    the next user prompt's timestamp.  If none exist, the prompt is a no-dispatch
    candidate.

    This is intentionally different from _align (which finds the nearest-following
    dispatch within the whole session): here we partition the event stream into
    per-prompt windows so only dispatches strictly between consecutive user
    messages count.
    """
    objs = _iter_transcript_lines(path)
    if not objs:
        return []
    default_session = path.stem

    # Collect all events sorted by timestamp, keeping their type annotation.
    # We tag each event as 'user_prompt' or 'agent_dispatch'.
    tagged: list[tuple[str, str, dict[str, Any]]] = []
    for obj in objs:
        message = obj.get("message")
        if not isinstance(message, dict):
            continue
        session_id = _session_id(obj, default_session)
        role = message.get("role")
        content = message.get("content")

        if role == "user" and isinstance(content, str) and is_genuine_user_prompt(content):
            tagged.append(("user_prompt", session_id, obj))
            continue

        if role == "assistant" and isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") != "tool_use":
                    continue
                if block.get("name") not in _AGENT_TOOL_NAMES:
                    continue
                tool_input = block.get("input")
                if not isinstance(tool_input, dict):
                    continue
                persona = str(tool_input.get("subagent_type") or "").strip()
                if not persona:
                    continue
                tagged.append(("agent_dispatch", session_id, obj))
                break  # one dispatch tag per assistant turn is enough

    # Sort by timestamp.
    tagged.sort(key=lambda t: _event_ts(t[2]))

    # Walk through tagged events.  For each user_prompt, collect any agent_dispatch
    # events that follow it before the NEXT user_prompt in the SAME session.
    pairs: list[dict[str, Any]] = []
    i = 0
    while i < len(tagged):
        kind, session_id, obj = tagged[i]
        if kind != "user_prompt":
            i += 1
            continue
        # Gather events between this prompt and the next user_prompt in same session.
        prompt_text = str(obj.get("message", {}).get("content") or "").strip()
        prompt_ts = _event_ts(obj)
        has_dispatch_before_next_user = False
        j = i + 1
        while j < len(tagged):
            nkind, nsession, nobj = tagged[j]
            if nkind == "user_prompt" and nsession == session_id:
                break  # next user turn in same session reached — stop looking
            if nkind == "agent_dispatch" and nsession == session_id:
                has_dispatch_before_next_user = True
                break
            j += 1

        if not has_dispatch_before_next_user and prompt_text:
            ph = prompt_hash(prompt_text)
            pair: dict[str, Any] = {
                "session_id": session_id,
                "prompt": prompt_text,
                "prompt_hash": ph,
                "timestamp": prompt_ts,
                "label_persona": "no-dispatch",
                "label_status": "ok",
                "label_source": LABEL_SOURCE_NO_DISPATCH,
                "label_confidence": LABEL_CONFIDENCE_NO_DISPATCH,
            }
            pairs.append(pair)
        i += 1
    return pairs


def mine_no_dispatch(
    root: Path | None = None,
    *,
    max_pairs: int | None = None,
) -> list[dict[str, Any]]:
    """Mine transcripts for user prompts with NO following Agent/Task dispatch.

    For each session file, for each user prompt that has no Agent/Task tool_use
    with a subagent_type before the NEXT user prompt (or end of session), emit ONE
    labeled pair with:
      - label_persona    = 'no-dispatch'
      - label_status     = 'ok'  (training-grade)
      - label_source     = LABEL_SOURCE_NO_DISPATCH
      - label_confidence = LABEL_CONFIDENCE_NO_DISPATCH (0.6)
      - 'agree' is absent (no model guess on this path)

    When max_pairs is set, the result is capped deterministically by sorting
    candidates on (session_id, prompt_hash) and taking the first max_pairs.
    This guarantees identical output on repeated calls (no randomness).
    """
    base = root or projects_root()
    if not base.exists():
        return []
    candidates: list[dict[str, Any]] = []
    for path in sorted(base.glob("*/*.jsonl")):
        candidates.extend(_mine_no_dispatch_file(path))

    # Deterministic stable sort before cap so repeated calls return identical order.
    candidates.sort(
        key=lambda p: (str(p.get("session_id") or ""), str(p.get("prompt_hash") or ""))
    )

    if max_pairs is not None:
        candidates = candidates[:max_pairs]
    return candidates
