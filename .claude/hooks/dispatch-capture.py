#!/usr/bin/env python3
"""PreToolUse hook (matcher: Agent) — the dispatch sidecar (PRIMARY ground-truth).

The missing labeling half of router data capture. `router.py` logs the model's
GUESS (pred_*); this hook logs what the human-supervised orchestrator ACTUALLY
dispatched — the correct label for fine-tune training.

On every Agent-tool dispatch it appends one row to
.memory/files/router_dispatches.jsonl:
    {"session_id", "prompt_hash", "dispatched_persona", "dispatch_kind", "ts"}

dispatch_kind is "single" for an Agent/Task (one sub-agent) and "fanout" for a
Workflow/TeamCreate (a parallel team). It is the ground-truth signal the broker's
advisory decomposition nudge counts: N consecutive "single" rows with no "fanout"
since session start is the "fan out earlier" cue (Constitution Art. XIII.d).

The dispatch label is the Agent tool's subagent_type (this harness dispatches via
the Agent tool; Task.subagent_type is always empty here, so subagent_type/agent_type
is read with the same fallback dispatch-announce.sh uses).

prompt_hash is the sha256 of the session's triggering user prompt (shared
convention with router.py: hashlib.sha256(prompt.encode("utf-8")).hexdigest()).
The dispatch payload does NOT carry the prompt, so it is recovered best-effort from
the nearest-preceding router_decisions.jsonl row for this session — that is exactly
the nearest-following-dispatch alignment the labeler joins on. When unrecoverable the
row still records session_id + dispatched_persona + ts; the labeler aligns on
session_id alone.

Fail-soft and fail-open: ANY error exits 0 with no output. It NEVER blocks a
dispatch (no permissionDecision is ever emitted). Wired via
.claude/settings.json hooks.PreToolUse matcher "Agent".

R1-T05 (agent_activity live wiring): for a "single" dispatch (real Agent-tool
call with a persona) this hook ALSO opens a cockpit activity row via
`python3 .memory/log.py activity start --agent <persona> --task <brief> \
--session <session_id>` and caches the returned activity_id to
.memory/files/activity_open.jsonl keyed by (session_id, persona) so
completion-capture.py (SubagentStop) can find and close it. "fanout" rows
(Workflow/TeamCreate) are skipped — BUG #1 in completion-capture.py means no
SubagentStop ever fires for Workflow-internal teammates, so an activity row
opened for a fanout dispatch could never close and would rot as "active"
forever; only direct single-Agent dispatches get a cockpit row. This mirrors
feedback-capture.py's subprocess invocation pattern (argv list, never shell-
interpolated, best-effort / never blocks).
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

HOOKS_DIR = Path(__file__).parent

# --- Genuine-prompt filter (standalone copy — cannot import broker.*) ---
# Mirrors broker.router_train.transcript.is_genuine_user_prompt.
# Used in _prompt_hash_for_session to skip non-genuine rows when scanning
# router_decisions.jsonl for the (session_id, prompt_hash) join key.
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


def _is_genuine_user_prompt(text: str) -> bool:
    """Return True iff *text* is a genuine human-typed routing query.

    Standalone copy — hooks run under ambient python3 (3.9) without the broker
    .venv; duplication across the hook boundary is intentional.
    """
    stripped = text.strip()
    if len(stripped) < _MIN_GENUINE_LEN:
        return False
    if len(text) > _MAX_GENUINE_LEN:
        return False
    return all(marker not in text for marker in _INJECTED_MARKERS)

# Read-only / recon personas: re-running the same brief is normal recon (re-grep,
# re-read), not the "re-firing the same code-writing brief" loop this advisory
# targets — so they are exempt (mirrors the DEC-027 gate-exempt read-only set).
_REDISPATCH_EXEMPT = frozenset({"scout", "lens", "lens-fast", "palette", "plexus", "nexus"})

# How many recent same-session dispatches to scan for a same (persona, brief_hash)
# repeat before the current one.
_REDISPATCH_LOOKBACK = 3


def _files_dir() -> Path:
    override = os.environ.get("_HOOK_MEMORY_FILES_DIR")
    if override:
        return Path(override)
    return HOOKS_DIR.parent.parent / ".memory" / "files"


def _tool_input(data: dict) -> dict:
    """Recover the tool input across the harness's payload shapes.

    PreToolUse nests it under "tool_input" (current harness) or "input" (older
    shape); some shapes pass the fields flat at top level. Mirrors the dual-shape
    handling in dispatch-announce.sh / persona-alias-resolver.sh.
    """
    for key in ("tool_input", "input"):
        candidate = data.get(key)
        if isinstance(candidate, dict):
            return candidate
    return data


def _dispatched_persona(tool_input: dict) -> str | None:
    """The persona ACTUALLY dispatched — subagent_type, agent_type fallback.

    This harness dispatches via the Agent tool, which carries the persona under
    subagent_type; Agent/Team-shaped payloads use agent_type. Same fallback order
    as dispatch-announce.sh so every dispatch flavour records.
    """
    for key in ("subagent_type", "agent_type"):
        val = tool_input.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None


def _dispatch_kind(tool_name: str) -> str:
    """Classify a dispatch as a serial single or a parallel fan-out.

    "single"  — Agent / Task: one sub-agent dispatched serially.
    "fanout"  — Workflow / TeamCreate: a parallel team / dynamic Workflow.

    This is the ground-truth label the broker's advisory decomposition nudge
    counts (Constitution Art. XIII.d): N consecutive "single" rows with no
    "fanout" since session start is the "author a Workflow now" cue. An empty
    tool_name (older flat payload that omits it) defaults to "single".
    """
    if tool_name in ("Workflow", "TeamCreate"):
        return "fanout"
    return "single"


def _prompt_hash_for_session(session_id: str, files_dir: Path) -> str:
    """Best-effort prompt_hash via the nearest-preceding router decision.

    The dispatch payload has no prompt, so recover the triggering prompt's hash
    from the LAST router_decisions.jsonl row for this session (the nearest-
    preceding routed prompt = the dispatch's nearest-following alignment target).
    Returns "" when no joinable row exists; the labeler then aligns on session_id.

    BUG #2 guard: rows whose ``prompt`` is not a genuine user prompt are skipped
    so a noise row (task-notification, system-reminder, etc.) never becomes the
    join anchor.  Future rows are clean (router.py exits early for non-genuine
    turns); this guard handles historical noise rows already in the file.
    """
    if not session_id or session_id == "unknown":
        return ""
    decisions = files_dir / "router_decisions.jsonl"
    try:
        last_hash = ""
        with decisions.open() as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                if rec.get("session_id") != session_id:
                    continue
                # Skip non-genuine rows so they cannot corrupt the join key.
                prompt = rec.get("prompt")
                if isinstance(prompt, str) and not _is_genuine_user_prompt(prompt):
                    continue
                ph = rec.get("prompt_hash")
                if ph:
                    last_hash = ph
                    continue
                if isinstance(prompt, str) and prompt:
                    last_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        return last_hash
    except Exception:
        return ""


def _brief_hash(tool_input: dict) -> str:
    """Short sha256 of the dispatch brief (description + prompt).

    The brief text is what the orchestrator re-words when it re-fires the SAME
    goal at the SAME persona. Joining description + prompt and hashing gives a
    stable per-brief key; the 12-char prefix is enough to collide only on
    genuinely identical briefs. Returns "" when neither field is present.
    """
    parts = []
    for key in ("description", "prompt"):
        val = tool_input.get(key)
        if isinstance(val, str) and val.strip():
            parts.append(val.strip())
    if not parts:
        return ""
    joined = "\n".join(parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:12]


def _recent_same_brief(
    session_id: str, persona: str, brief_hash: str, files_dir: Path
) -> bool:
    """True if a recent same-session row repeats this (persona, brief_hash).

    Scans the LAST _REDISPATCH_LOOKBACK same-session rows of
    router_dispatches.jsonl (the rows BEFORE the one about to be written). A hit
    means the orchestrator already dispatched this exact persona+goal moments ago.
    Fail-open: any read/parse error => False (no advisory, write the row anyway).
    """
    if not session_id or session_id == "unknown" or not brief_hash:
        return False
    path = files_dir / "router_dispatches.jsonl"
    try:
        same_session = []
        with path.open() as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                if rec.get("session_id") != session_id:
                    continue
                same_session.append(rec)
        for rec in same_session[-_REDISPATCH_LOOKBACK:]:
            if (
                rec.get("dispatched_persona") == persona
                and rec.get("brief_hash") == brief_hash
            ):
                return True
        return False
    except Exception:
        return False


def _redispatch_advisory(persona: str) -> None:
    """Emit the ONE same-goal re-dispatch advisory (PreToolUse additionalContext).

    ADVISORY ONLY — never permissionDecision:deny, never a non-zero exit. Printed
    to stdout as the nested hookSpecificOutput object the harness consumes.
    """
    payload = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext": (
                "[dispatch] You are re-dispatching the same persona+goal ("
                + persona
                + ") you dispatched moments ago. Change the APPROACH — a different "
                "persona, escalate to -pro, or ask the user — rather than re-firing "
                "the same brief with reworded text."
            ),
        }
    }
    with contextlib.suppress(Exception):
        sys.stdout.write(json.dumps(payload))


def _append_jsonl(path: Path, record: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as fh:
            fh.write(json.dumps(record) + "\n")
    except Exception:
        pass


def _repo_root() -> Path:
    """Repo root is two levels up from .claude/hooks/ (mirrors feedback-capture)."""
    override = os.environ.get("_HOOK_REPO_ROOT")
    if override:
        return Path(override)
    return HOOKS_DIR.parent.parent


def _log_py(root: Path) -> Path:
    return root / ".memory" / "log.py"


def _dispatch_task_label(tool_input: dict) -> str:
    """Short human-readable task label for the activity row.

    Prefers "description" (CONTRACT.md brief field), falls back to the first
    line of "prompt". Truncated to keep the cockpit table readable.
    """
    for key in ("description", "prompt"):
        val = tool_input.get(key)
        if isinstance(val, str) and val.strip():
            first_line = val.strip().splitlines()[0]
            return first_line[:120]
    return ""


def _start_activity(root: Path, persona: str, task: str, session_id: str) -> int | None:
    """Best-effort `log.py activity start`; returns the new activity_id or None.

    Mirrors feedback-capture.py's subprocess invocation: argv list (never
    shell-interpolated), bounded timeout, swallow any failure. Never raises;
    a failed activity-start must never block or surface to the dispatch.
    """
    log_py = _log_py(root)
    if not log_py.is_file():
        return None
    cmd = [sys.executable, str(log_py), "activity", "start", "--agent", persona]
    if task:
        cmd += ["--task", task]
    if session_id and session_id != "unknown":
        cmd += ["--session", session_id]
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=30,
        )
        if proc.returncode != 0:
            return None
        out = json.loads(proc.stdout.strip())
        activity_id = out.get("activity_id")
        return int(activity_id) if activity_id is not None else None
    except Exception:
        return None


def _cache_open_activity(files_dir: Path, session_id: str, persona: str, activity_id: int) -> None:
    """Append the (session_id, persona) -> activity_id row completion-capture.py reads.

    A plain append-only JSONL: completion-capture.py scans for the LAST
    matching (session_id, persona) row, mirroring the join pattern already
    used for prompt_hash recovery elsewhere in this file. No mutation/locking
    needed — single-threaded hook execution, at-most-one writer at a time.
    """
    record = {
        "session_id": session_id,
        "persona": persona,
        "activity_id": activity_id,
        "ts": datetime.now(timezone.utc).isoformat(),  # noqa: UP017
    }
    _append_jsonl(files_dir / "activity_open.jsonl", record)


def main() -> None:
    try:
        data = json.loads(sys.stdin.read())
    except Exception:
        sys.exit(0)
    if not isinstance(data, dict):
        sys.exit(0)

    tool_name = data.get("tool_name") or data.get("toolName") or ""
    if tool_name and tool_name not in ("Agent", "Task", "TeamCreate", "Workflow"):
        sys.exit(0)

    tool_input = _tool_input(data)
    kind = _dispatch_kind(tool_name)
    persona = _dispatched_persona(tool_input)
    # A "single" dispatch is meaningless without the persona it labels, so it is
    # still skipped when none is present. A "fanout" (Workflow/TeamCreate) often
    # carries no subagent_type — it is the very signal the decomposition nudge
    # counts, so it MUST be recorded even without a persona (empty string).
    if persona is None:
        if kind != "fanout":
            sys.exit(0)
        persona = ""

    session_id = (
        data.get("session_id")
        or data.get("sessionId")
        or tool_input.get("session_id")
        or "unknown"
    )

    files_dir = _files_dir()
    brief_hash = _brief_hash(tool_input)

    # Same-goal re-dispatch advisory — fire BEFORE appending the current row so it
    # cannot self-match. Exempt read-only/recon personas (re-recon is normal), and
    # fail-open: the lookback never blocks the write. ADVISORY ONLY.
    if (
        persona
        and persona not in _REDISPATCH_EXEMPT
        and brief_hash
        and _recent_same_brief(session_id, persona, brief_hash, files_dir)
    ):
        _redispatch_advisory(persona)

    record = {
        "session_id": session_id,
        "prompt_hash": _prompt_hash_for_session(session_id, files_dir),
        "dispatched_persona": persona,
        "brief_hash": brief_hash,
        "dispatch_kind": kind,
        "ts": datetime.now(timezone.utc).isoformat(),  # noqa: UP017
    }
    _append_jsonl(files_dir / "router_dispatches.jsonl", record)

    # R1-T05: open a cockpit activity row for real single-Agent dispatches only.
    # "fanout" (Workflow/TeamCreate) is skipped — see module docstring BUG #1:
    # no SubagentStop ever fires for Workflow-internal teammates, so a fanout
    # activity row could never be closed by completion-capture.py.
    if kind == "single" and persona:
        activity_id = _start_activity(
            _repo_root(), persona, _dispatch_task_label(tool_input), session_id
        )
        if activity_id is not None:
            _cache_open_activity(files_dir, session_id, persona, activity_id)

    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(0)
