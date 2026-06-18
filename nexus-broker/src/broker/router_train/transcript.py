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

_AGENT_TOOL_NAMES = frozenset({"Agent", "Task"})


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

        if role == "user" and isinstance(content, str) and content.strip():
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
