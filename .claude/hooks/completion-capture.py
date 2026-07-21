#!/usr/bin/env python3
"""SubagentStop hook (OPT-040 A1/A3) — completion-event ledger.

On every SubagentStop fires and appends one JSONL row to
.memory/files/completion_events.jsonl:

    {
        "ts": "<ISO-8601 UTC>",
        "session_id": "<str>",
        "persona": "<str>",
        "marker": "<DONE|REVISE|BLOCKED|NEEDS-DECISION|CHECKPOINT|unknown>",
        "files_changed_count": <int>,
        "prompt_hash": "<sha256 hex | ''>",
    }

marker is parsed from the H2 completion-marker heading in last_assistant_message
(mirrors the vocabulary root-cause-gate / return-validator use).

files_changed_count is the length of the `files_changed` list in the agent's
StructuredOutput JSON block (CONTRACT.md Required Output field). Falls back to
0 when no parseable JSON block or no `files_changed` key is found.

prompt_hash is recovered best-effort from the nearest-preceding
router_decisions.jsonl row for the session (same join dispatch-capture uses).
Falls back to "" when unrecoverable.

SECURITY POSTURE — the return body is DATA, never instructions. This hook only
PATTERN-MATCHES and json.loads the text to extract structured fields; it NEVER
executes or eval()s any content from the return.

Fail-soft: ANY error exits 0 with no output. It NEVER blocks a return.
Wired via .claude/settings.json hooks.SubagentStop (after return-summarizer).

3.9 IMPORT-SAFETY CONSTRAINT — live runtime is >=3.11 via the _py.sh resolver
shim but 3.9 IMPORT-safety is retained: the package twin runs this file
un-shimmed under ambient python3 (3.9). Do NOT introduce 3.11-only idioms.
`from __future__ import annotations` keeps PEP-604 unions def-time-safe.
Timestamps use timezone.utc + # noqa: UP017 per the codebase convention.

BUG #1 (workflow blindness) — ARCHITECTURAL LIMITATION (documented 2026-06-21):
Workflow-internal teammate spawns do NOT fire main-session SubagentStop events.
The SubagentStop hook only fires for DIRECT (non-Workflow) subagent completions.
Workflow-internal agent() calls are confined to the Workflow's interior message
stream and are invisible to all main-session PreToolUse/SubagentStop hooks.
Evidence: router_dispatches.jsonl frozen at 63 rows despite ~15 Workflow dispatches
in the same session; completion_events.jsonl only captures direct completions.
No hook mechanism can reach Workflow-internal dispatches without an Anthropic API
extension granting per-Workflow-step hook events.
See: FEATURE-REQUEST-workflow-runtime.md (filed 2026-06-21).
IMPACT: completion_events is a usable label source for DIRECT dispatch completions
only. (session_id, prompt_hash) join to router_decisions recovers labels for those.

R1-T05 (agent_activity live wiring): for a DIRECT completion this hook ALSO
closes the cockpit activity row dispatch-capture.py opened, via
`python3 .memory/log.py activity end --id <id> --status <mapped>`. The row id
is recovered from .memory/files/activity_open.jsonl by (session_id, persona)
— the same join key dispatch-capture.py wrote it under. Marker -> status:
DONE -> done; REVISE|BLOCKED -> failed; CHECKPOINT|NEEDS-DECISION -> active
(non-terminal, still in flight); unknown -> done (fallback so the row always
closes rather than rotting as "active" forever). Best-effort / never blocks,
mirrors feedback-capture.py's subprocess pattern.

DAEMON SHIM (Tranche 2, nexus-redesign/audits/daemon-hook-plan-2026-07-12.md
§C) — the completion_events.jsonl append (this hook's only pure "record"
step) first tries a `record_event` RPC (sink="completion_events") against
the resident daemon's Unix socket with a SHORT, env-tunable timeout
(`NEXUS_COMPLETION_CAPTURE_DAEMON_TIMEOUT_S`, default 0.3s) via the shared
`_daemon_rpc` shim (same-directory dynamic import, mirrors how this file
already loads no cross-package modules). ANY daemon miss/timeout/error
falls back INLINE to the exact `_append_jsonl` call below — no hook may
ever fail or block because the daemon is down. The `_end_activity` log.py
call that follows is unchanged by this tranche (out of scope — it returns
no data this hook needs back and is not a plain append).

FINDING #6 (drift-analysis, 2026-07-12) — dispatch_telemetry capture. Prior
to this change dispatch_telemetry's ONLY writer was the conductor lane
(broker.conductor.dag.record_dispatch_telemetry); a normal harness
completion never reached the table (68/69 rows were conductor-lane, 0
harness). This hook is the only SubagentStop-reachable point for DIRECT
completions (BUG #1 above means Workflow-internal legs still cannot be
captured here — see .memory/log.py's ingest_workflow_journal for that half),
so it now ALSO writes one `dispatch_telemetry` row per completion (any
marker, not just DONE) via `python3 .memory/log.py dispatch record`:
  - tokens: HARNESS-REPORTED (from the transcript's own usage blocks, when
    `data["transcript_path"]` is present and parseable) or char/4 approx of
    `assistant_text` — token_source records which. DEC-092 (ruling,
    2026-07-17): a harness-derived count IS an exact count — token_source=
    'exact' names a PRECISION claim ("this number is not approximated"), not
    a provenance claim, so the harness-reported bucket is correctly labeled
    'exact' and stays that way. No 'harness' token_source value exists or is
    needed; `.memory/log.py`'s `dispatch record` CLI's
    `choices=["exact", "approx"]` (argparse) is unchanged.
  - tool_uses: from the transcript when it yielded tokens, else NULL.
  - duration_ms: genuinely per-dispatch — computed from activity_open.jsonl's
    own `ts` (the dispatch's PreToolUse start, written by dispatch-capture.py)
    to now, independent of what the harness hook payload itself carries. See
    `_find_activity_open_row`'s TASK-093 stage 2 docstring update: a STALE
    (already-consumed) activity_open row is never rejoined, so a completion
    with no genuine open row of its own gets an honest NULL rather than a
    fabricated multi-hour "session-elapsed" number.
  - task_id: relayed from the SAME activity_open.jsonl row dispatch-capture.py
    cached it to (see that hook's TASK-093 stage 2 docstring update) — None
    when no joinable row exists.
  - model: the persona's declared `model:` frontmatter in
    .claude/agents/<persona>.md (static assignment, not runtime-verified).
Best-effort / never blocks, same subprocess discipline as `_end_activity`.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone  # noqa: UP017
from pathlib import Path

HOOKS_DIR = Path(__file__).parent


def _daemon_rpc_module():
    """Same-directory dynamic import — mirrors broker-gate.py's _heartbeat load."""
    spec = importlib.util.spec_from_file_location(
        "_daemon_rpc", HOOKS_DIR / "_daemon_rpc.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


_DAEMON_TIMEOUT_S = float(os.environ.get("NEXUS_COMPLETION_CAPTURE_DAEMON_TIMEOUT_S", "0.3"))


def _daemon_call_isolated() -> bool:
    """True when a test-isolation override is active for this invocation.

    NATIVE-18-3 class of bug (see nexus-broker/tests/test_dispatch_capture.py):
    a RESIDENT daemon binds its socket to the REAL repo root at daemon-start
    time, so it can never honor a per-call `_HOOK_MEMORY_FILES_DIR` /
    `_HOOK_REPO_ROOT` override the way the inline fallback naturally does
    (both are read fresh on every hook invocation). Skipping the daemon hop
    entirely whenever either override is set is the only way to avoid a
    real hook-test silently writing into THIS repo's live telemetry sinks
    while its own assertions look at the (empty) isolated path instead —
    mirrors _heartbeat.py's identical NEXUS_HEARTBEAT_PATH-skips-daemon rule.
    """
    return bool(os.environ.get("_HOOK_MEMORY_FILES_DIR") or os.environ.get("_HOOK_REPO_ROOT"))


def _dispatch_telemetry_capture_isolated() -> bool:
    """True when a files-dir override is active WITHOUT an explicit repo-root
    override — the exact shape every pre-existing test in this file uses
    (`_HOOK_MEMORY_FILES_DIR` alone, via `_run_hook`'s `files_dir` param).

    `_record_dispatch_telemetry` builds its subprocess `root`/`cwd` from
    `_repo_root()`, which ONLY changes when `_HOOK_REPO_ROOT` is explicitly
    set — a files-dir-only override still resolves `_repo_root()` to THIS
    repo's real root, so calling the dispatch-record subprocess in that
    shape would silently spawn a real `.memory/log.py` process against the
    real (or nonexistent, auto-created-empty) project.db. Skipping the
    call entirely in that shape protects every existing/pre-existing test
    from an unintended live-DB side effect; a test that WANTS to exercise
    the real subprocess call sets `_HOOK_REPO_ROOT` explicitly (pointing at
    an isolated fixture repo with its own `.memory/log.py` + a
    `NEXUS_DB_PATH` override) — that combination is NOT isolated here.
    """
    return bool(os.environ.get("_HOOK_MEMORY_FILES_DIR")) and not bool(
        os.environ.get("_HOOK_REPO_ROOT")
    )

# --- Genuine-prompt filter (standalone copy — cannot import broker.*) ---
# Mirrors broker.router_train.transcript.is_genuine_user_prompt.
# Used in _prompt_hash_for_session to skip non-genuine rows when scanning
# router_decisions.jsonl for the (session_id, prompt_hash) join key, so a
# task-notification or system-reminder row never becomes the join anchor.
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


# H2 completion-marker vocabulary (mirrors root-cause-gate / return-validator).
_MARKER_RE = re.compile(
    r"^\s*##\s+NEXUS:(DONE|REVISE|BLOCKED|CHECKPOINT|NEEDS-DECISION)\b",
    re.IGNORECASE | re.MULTILINE,
)

# Marker -> agent_activity.status (active|done|failed per log.py's CLI help).
# DONE closes clean; REVISE/BLOCKED are terminal failures for THIS dispatch;
# CHECKPOINT/NEEDS-DECISION are non-terminal (work continues) so the row stays
# "active"; "unknown" (no H2 marker found) still closes as "done" so a row
# never rots as "active" forever when the marker just couldn't be parsed.
_MARKER_TO_ACTIVITY_STATUS = {
    "DONE": "done",
    "REVISE": "failed",
    "BLOCKED": "failed",
    "CHECKPOINT": "active",
    "NEEDS-DECISION": "active",
}


def _files_dir() -> Path:
    override = os.environ.get("_HOOK_MEMORY_FILES_DIR")
    if override:
        return Path(override)
    return HOOKS_DIR.parent.parent / ".memory" / "files"


def _repo_root() -> Path:
    override = os.environ.get("_HOOK_REPO_ROOT")
    if override:
        return Path(override)
    return HOOKS_DIR.parent.parent


def _log_py(root: Path) -> Path:
    return root / ".memory" / "log.py"


def _closed_activity_ids(files_dir: Path) -> set[int]:
    """activity_ids already consumed by a prior completion-capture.py join
    (TASK-093 stage 2 — the "session-elapsed" duration_ms fix).

    Read fresh on every hook invocation off `.memory/files/
    activity_closed.jsonl`, mirroring activity_open.jsonl's own read
    convention (no locking needed — single-threaded hook execution, at-most-
    one writer at a time). Missing file -> empty set, never an error.
    """
    path = files_dir / "activity_closed.jsonl"
    closed: set[int] = set()
    if not path.exists():
        return closed
    try:
        with path.open() as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                activity_id = rec.get("activity_id")
                if isinstance(activity_id, int):
                    closed.add(activity_id)
    except Exception:
        return closed
    return closed


def _mark_activity_closed(files_dir: Path, activity_id: int) -> None:
    """Record *activity_id* as consumed (TASK-093 stage 2) so a LATER
    completion for the same (session_id, persona) — e.g. a Workflow-internal
    leg with no PreToolUse counterpart of its own — can never silently
    rejoin this same, now-stale row (see `_find_activity_open_row`)."""
    _append_jsonl(
        files_dir / "activity_closed.jsonl",
        {"activity_id": activity_id, "closed_at": datetime.now(timezone.utc).isoformat()},  # noqa: UP017
    )


def _find_activity_open_row(session_id: str, persona: str, files_dir: Path) -> dict | None:
    """Recover the LAST *unconsumed* activity_open.jsonl row dispatch-
    capture.py cached for (session_id, persona) — the full row, not just the
    activity_id, so callers can also read its `ts` (the dispatch's own start
    time) for a genuine wall-clock duration_ms computation (Finding #6,
    2026-07-12) and its `task_id` (TASK-093 stage 2).

    Scans for the LAST matching row NOT already present in
    `_closed_activity_ids` (TASK-093 stage 2 root-cause fix, 07-17 evidence:
    123/132 recent dispatch_telemetry rows were junk) — mirrors the nearest-
    preceding-row join pattern used elsewhere in this file for prompt_hash
    recovery, but skips any row this hook already consumed on a PRIOR
    completion. Root cause of the bug this closes: without the skip, a
    completion with NO PreToolUse-opened row of its own (a Workflow-internal
    leg, or any dispatch dispatch-capture.py's "single" gate missed) would
    silently rejoin the LAST row ever opened for that (session_id, persona)
    pair — which may be hours old — producing a `duration_ms` that is really
    "time since some earlier, unrelated dispatch started" (a session-elapsed-
    shaped number, empirically confirmed non-monotonic across consecutive
    rows) rather than a genuine per-dispatch duration. Skipping consumed rows
    means a completion with no genuine open row of its own now correctly gets
    None (honest missing data, matching wtcs.py's own "NULL, never
    fabricated" convention) instead of a wrong number.

    Returns None when no unconsumed joinable row exists (e.g. a genuine
    Workflow-internal completion that never got a SubagentStop-reachable
    dispatch-capture.py invocation, every candidate row was already consumed,
    or the sidecar file is missing).
    """
    if not session_id or session_id == "unknown" or not persona or persona == "unknown":
        return None
    path = files_dir / "activity_open.jsonl"
    if not path.exists():
        return None
    closed_ids = _closed_activity_ids(files_dir)
    try:
        last_row = None
        with path.open() as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                if rec.get("session_id") != session_id or rec.get("persona") != persona:
                    continue
                activity_id = rec.get("activity_id")
                if isinstance(activity_id, int) and activity_id in closed_ids:
                    continue
                last_row = rec
        return last_row
    except Exception:
        return None


def _open_activity_id(session_id: str, persona: str, files_dir: Path) -> int | None:
    """Recover the activity_id dispatch-capture.py cached for (session_id, persona)."""
    row = _find_activity_open_row(session_id, persona, files_dir)
    if row is None:
        return None
    activity_id = row.get("activity_id")
    return activity_id if isinstance(activity_id, int) else None


def _duration_ms_since(started_ts: str) -> int | None:
    """Genuine wall-clock duration in ms from an ISO-8601 `started_ts` to
    now. Real elapsed time (never approximated) because the endpoints are
    both this repo's own timestamps — dispatch-capture.py's activity_open.jsonl
    write (PreToolUse, the dispatch's true start) and this call (SubagentStop,
    the dispatch's true end) — independent of whatever the harness hook
    payload does or doesn't carry. Returns None on any parse failure or a
    negative delta (clock skew / bad input) rather than fabricate a number.
    """
    try:
        started = datetime.fromisoformat(started_ts)
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)  # noqa: UP017
        delta_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)  # noqa: UP017
        return delta_ms if delta_ms >= 0 else None
    except Exception:
        return None


def _approx_tokens(text: str) -> int:
    """char/4 heuristic fallback — the documented approx convention
    (dispatch_telemetry.token_source='approx'; see .memory/schema.sql)."""
    return max(0, len(text) // 4)


def _model_for_persona(persona: str, root: Path) -> str | None:
    """Best-effort: the orchestrator's STATIC model assignment for *persona*,
    read from the declared `model:` frontmatter field in
    .claude/agents/<persona>.md. The SubagentStop hook payload carries no
    model identifier (never observed in ANY hook payload in this repo — see
    context-reset-monitor.py's transcript_length/message_count/
    context_message_count field list, the closest thing to a documented
    payload shape), so the agent file's own declared assignment is the best
    available ground truth, not a runtime-verified value. Returns None on
    any read/parse failure (never raises).
    """
    if not persona or persona == "unknown":
        return None
    agent_file = root / ".claude" / "agents" / f"{persona}.md"
    try:
        if not agent_file.is_file():
            return None
        text = agent_file.read_text(encoding="utf-8")
    except Exception:
        return None
    m = re.search(r"^model:\s*(\S+)\s*$", text, re.MULTILINE)
    return m.group(1).strip() if m else None


def _extract_exact_usage_from_transcript(
    transcript_path: str | None, *, max_lines: int = 2000
) -> tuple[int | None, int | None, str | None]:
    """Best-effort EXACT (tokens, tool_uses, model) extraction from the harness
    transcript JSONL.

    TASK-094 LEG B — "model attr from harness usage blocks": each assistant
    turn's `message` dict carries the harness's OWN reported `model` id
    (e.g. "claude-sonnet-4-5-..."), alongside `usage`. This is a live,
    per-turn signal, distinct from `_model_for_persona`'s STATIC read of the
    persona's declared `model:` frontmatter — the two can legitimately
    diverge (a runtime model override), and the harness-reported value is
    preferred when present (see its one call site in `main()` below). The
    LAST seen non-empty `model` string among matched entries wins (most
    recent model actually in use for this dispatch).

    Claude Code (and Claude-Code-flavored harness variants) forwards
    `transcript_path` on most hook events; each line is a JSON object whose
    `message.usage` (for assistant turns) carries `input_tokens`/
    `output_tokens`, and whose `message.content` list may contain
    `type: "tool_use"` blocks. When the harness ALSO tags subagent turns
    with `isSidechain: true`, this scopes to those; when that field is
    absent, it scans every entry in the tail of the file instead (best-effort
    — see the "NEVER raises" contract below).

    THIS HAS NOT BEEN EMPIRICALLY VERIFIED against a captured real harness
    SubagentStop payload (a leaf-executor dispatch cannot observe its own
    hook firing to confirm the exact live shape) — it is deliberately
    defensive: ANY missing file, unparseable line, or absent `usage` field
    degrades to returning (None, None, None), and the caller falls back to
    the char/4 approx path (tokens/tool_uses) / `_model_for_persona` (model).
    A wrong guess here can only under-deliver (approx/static fallback
    fires), never corrupt a row or crash the hook.
    """
    if not transcript_path:
        return None, None, None
    try:
        path = Path(transcript_path)
        if not path.is_file():
            return None, None, None
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return None, None, None

    tail = lines[-max_lines:]
    total_tokens = 0
    tool_uses = 0
    saw_any_usage = False
    model: str | None = None
    for line in tail:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue
        if not isinstance(rec, dict):
            continue
        if "isSidechain" in rec and rec.get("isSidechain") is not True:
            continue
        message = rec.get("message")
        if not isinstance(message, dict):
            continue
        usage = message.get("usage")
        if isinstance(usage, dict):
            inp = usage.get("input_tokens")
            out = usage.get("output_tokens")
            if isinstance(inp, int) and isinstance(out, int):
                total_tokens += inp + out
                saw_any_usage = True
        msg_model = message.get("model")
        if isinstance(msg_model, str) and msg_model:
            model = msg_model
        content = message.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    tool_uses += 1

    if not saw_any_usage:
        return None, None, model
    return total_tokens, tool_uses, model


def _record_dispatch_telemetry(
    root: Path,
    *,
    persona: str,
    model: str | None,
    marker: str,
    tokens: int,
    token_source: str,
    tool_uses: int | None,
    duration_ms: int | None,
    session_id: str,
    task_id: str | None = None,
) -> None:
    """Best-effort `log.py dispatch record`. Never raises; failure is
    swallowed — mirrors `_end_activity`'s subprocess invocation pattern
    (argv list, never shell-interpolated, bounded timeout). This is the
    missing write completion-capture.py never made: dispatch_telemetry's
    only prior writer was the conductor lane (broker.conductor.dag); a
    normal harness DONE/REVISE/BLOCKED return never reached this table
    (drift-analysis Finding #6, 2026-07-12).

    task_id (TASK-093 stage 2, additive): relayed from the dispatch's own
    activity_open.jsonl row when recoverable — see dispatch-capture.py's
    `_extract_task_id`. None -> the `--task-id` flag is omitted, same as
    every other optional field below.

    TASK-093 stage 3 (daemon-first bridge): before falling back to the
    `log.py dispatch record` subprocess below, this first tries the
    daemon's `record_telemetry` RPC (per-consumer timeout pattern —
    `_daemon_rpc.py` + this hook's own `_DAEMON_TIMEOUT_S`, same shim
    `main()`'s other two RPC hops already use). A confirmed accept lands
    the row in the daemon's write-through TelemetryStore (flushed to
    project.db later) AND bridges it into a durable dispatch span via
    `server._emit_dispatch_span_from_telemetry` — the one live path that
    closes the "`span.emit` built but never called" gap (see
    `nexus-broker/src/broker/daemon/server.py`'s TASK-093 stage 1 note).
    Gated by the SAME `_daemon_call_isolated()` check `main()`'s existing
    RPC hops use, so every pre-existing isolated test keeps exercising
    ONLY the subprocess path below, byte-identical to before this change.
    ANY daemon miss — dead/unreachable daemon, timeout, malformed reply,
    or a test-isolation skip — falls straight through to the UNCHANGED
    subprocess call: capture must NEVER brick or lose a row, so this
    fallback is unconditional."""
    if not _daemon_call_isolated():
        row: dict = {"persona": persona, "tokens": tokens, "token_source": token_source, "run_context": "local"}
        if model:
            row["model"] = model
        if marker and marker != "unknown":
            row["marker"] = marker
        if tool_uses is not None:
            row["tool_uses"] = tool_uses
        if duration_ms is not None:
            row["duration_ms"] = duration_ms
        if session_id and session_id != "unknown":
            row["session_id"] = session_id
        if task_id:
            row["task_id"] = task_id
        try:
            result = _daemon_rpc_module().call(
                root, "record_telemetry", {"table": "dispatch_telemetry", "row": row}, _DAEMON_TIMEOUT_S
            )
        except Exception:
            result = None
        if result is not None:
            return

    log_py = _log_py(root)
    if not log_py.is_file():
        return
    cmd = [
        sys.executable, str(log_py), "dispatch", "record",
        "--persona", persona,
        "--tokens", str(tokens),
        "--token-source", token_source,
    ]
    if model:
        cmd += ["--model", model]
    if marker and marker != "unknown":
        cmd += ["--marker", marker]
    if tool_uses is not None:
        cmd += ["--tool-uses", str(tool_uses)]
    if duration_ms is not None:
        cmd += ["--duration-ms", str(duration_ms)]
    if session_id and session_id != "unknown":
        cmd += ["--session-id", session_id]
    if task_id:
        cmd += ["--task-id", task_id]
    try:
        subprocess.run(
            cmd,
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=30,
        )
    except Exception:
        return


def _end_activity(root: Path, activity_id: int, status: str) -> None:
    """Best-effort `log.py activity end`. Never raises; failure is swallowed.

    Mirrors feedback-capture.py's subprocess invocation pattern: argv list
    (never shell-interpolated), bounded timeout, swallow any failure.
    """
    log_py = _log_py(root)
    if not log_py.is_file():
        return
    cmd = [
        sys.executable,
        str(log_py),
        "activity",
        "end",
        "--id",
        str(activity_id),
        "--status",
        status,
    ]
    try:
        subprocess.run(
            cmd,
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=30,
        )
    except Exception:
        return


def _extract_payload(data: dict) -> tuple[str, str, str]:
    """Return (assistant_text, persona, session_id) from the hook payload.

    NATIVE-5-11 RCA (2026-07-14): `agent_type` / `tool_input.agent_type` added
    to the persona fallback chain. This harness dispatches via the Agent tool,
    which carries the persona under `subagent_type` for Task-shaped dispatches
    but under `agent_type` for Agent/Team-shaped dispatches (see
    dispatch-capture.py's `_dispatched_persona()` and return-validator.py's
    NATIVE-4 fix, 2026-07-03, which already applied this exact fallback to its
    own extractor). completion-capture.py was authored on 2026-07-12 (Finding
    #6) — AFTER NATIVE-4 landed — but never picked up the same fallback field,
    so a real Agent-tool-shaped SubagentStop payload fell through all three
    prior keys straight to "unknown" every time. Confirmed empirically: 3371 of
    4556 real completion_events.jsonl rows (~74%) resolved persona="unknown".
    Finding #6's dispatch_telemetry write is gated behind
    `persona != "unknown"` (see main() below), so this single missing fallback
    silently zeroed out 100% of real harness dispatch_telemetry capture since
    Finding #6 merged — the write path itself was correct and its own tests
    passed (they hand-construct payloads with `agent_persona` set directly,
    never reproducing the field-naming asymmetry a genuine payload exhibits).
    """
    assistant_text: str = (
        data.get("last_assistant_message")
        or data.get("response", {}).get("text")
        or data.get("tool_response", {}).get("text")
        or ""
    )
    tool_input = data.get("tool_input", {})
    if not isinstance(tool_input, dict):
        tool_input = {}
    persona: str = (
        data.get("agent_persona")
        or data.get("subagent_type")
        or data.get("agent_type")
        or tool_input.get("subagent_type")
        or tool_input.get("agent_type")
        or "unknown"
    )
    session_id: str = (
        data.get("session_id")
        or data.get("sessionId")
        or "unknown"
    )
    return str(assistant_text), str(persona).strip().lower(), str(session_id)


def _parse_marker(text: str) -> str:
    """Extract the NEXUS completion marker from the assistant text.

    Returns the uppercase marker name (DONE, REVISE, BLOCKED, etc.)
    or "unknown" when no H2 marker is found.
    """
    m = _MARKER_RE.search(text)
    if m:
        return m.group(1).upper()
    return "unknown"


def _parse_files_changed_count(text: str) -> int:
    """Extract len(files_changed) from the first parseable JSON block.

    CONTRACT.md Required Output includes `"files_changed": ["<path>", ...]`.
    Returns 0 when no parseable JSON block or no `files_changed` key.
    """
    for block in re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL):
        try:
            obj = json.loads(block)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(obj, dict):
            continue
        fc = obj.get("files_changed")
        if isinstance(fc, list):
            return len(fc)
    return 0


def _prompt_hash_for_session(session_id: str, files_dir: Path) -> str:
    """Best-effort prompt_hash via the nearest-preceding router decision.

    Identical join logic to dispatch-capture.py: scan router_decisions.jsonl
    for the last row matching this session_id, recover its prompt_hash (or
    recompute it from the raw prompt field if present). Returns "" when no
    joinable row exists.

    BUG #2 guard: rows whose ``prompt`` is not a genuine user prompt (task-
    notifications, system-reminders, etc.) are skipped so a noise row never
    becomes the join anchor.  Future rows are clean (router.py now exits early
    for non-genuine turns); this guard handles historical noise rows already in
    the file.
    """
    if not session_id or session_id == "unknown":
        return ""
    decisions = files_dir / "router_decisions.jsonl"
    if not decisions.exists():
        return ""
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
                    last_hash = hashlib.sha256(
                        prompt.encode("utf-8")
                    ).hexdigest()
        return last_hash
    except Exception:
        return ""


def _append_jsonl(path: Path, record: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as fh:
            fh.write(json.dumps(record) + "\n")
    except Exception:
        pass


def main() -> None:
    try:
        data = json.loads(sys.stdin.read())
    except Exception:
        sys.exit(0)
    if not isinstance(data, dict):
        sys.exit(0)

    assistant_text, persona, session_id = _extract_payload(data)

    files_dir = _files_dir()
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),  # noqa: UP017
        "session_id": session_id,
        "persona": persona,
        "marker": _parse_marker(assistant_text),
        "files_changed_count": _parse_files_changed_count(assistant_text),
        "prompt_hash": _prompt_hash_for_session(session_id, files_dir),
    }
    accepted = False
    if not _daemon_call_isolated():
        try:
            accepted = (
                _daemon_rpc_module().call(
                    _repo_root(), "record_event", {"sink": "completion_events", "row": record}, _DAEMON_TIMEOUT_S
                )
                is not None
            )
        except Exception:
            accepted = False
    if not accepted:
        _append_jsonl(files_dir / "completion_events.jsonl", record)

    # R1-T05: close the cockpit activity row dispatch-capture.py opened, if any.
    marker = record["marker"]
    activity_row = _find_activity_open_row(session_id, persona, files_dir)
    activity_id = activity_row.get("activity_id") if activity_row else None
    if isinstance(activity_id, int):
        status = _MARKER_TO_ACTIVITY_STATUS.get(marker, "done")
        _end_activity(_repo_root(), activity_id, status)
        # TASK-093 stage 2: mark this row consumed so a LATER completion for
        # the same (session_id, persona) — e.g. a Workflow-internal leg with
        # no PreToolUse row of its own — never silently rejoins it (the
        # "session-elapsed" duration_ms root cause; see
        # _find_activity_open_row's docstring).
        _mark_activity_closed(files_dir, activity_id)

    # Finding #6 (2026-07-12, drift-analysis mandatory item 6): capture one
    # dispatch_telemetry row per harness completion. tokens: HARNESS-REPORTED
    # (token_source='exact' — DEC-092: a harness-derived count IS exact, see
    # this module's docstring) when the transcript exposes a usage block,
    # else char/4 approx (token_source='approx'). duration_ms: genuinely
    # per-dispatch via the activity_open.jsonl
    # start timestamp dispatch-capture.py wrote at PreToolUse time — never
    # approximated, independent of harness payload shape, and never a stale
    # rejoin (TASK-093 stage 2 fix above). task_id: relayed from the same
    # activity_open row (TASK-093 stage 2). Runs regardless of marker
    # (REVISE/BLOCKED dispatches still cost tokens+time and are equally worth
    # capturing), skipped only when persona is unresolvable.
    if persona and persona != "unknown" and not _dispatch_telemetry_capture_isolated():
        transcript_path = data.get("transcript_path")
        exact_tokens, exact_tool_uses, harness_model = _extract_exact_usage_from_transcript(transcript_path)
        if exact_tokens is not None:
            tokens, token_source, tool_uses = exact_tokens, "exact", exact_tool_uses
        else:
            tokens, token_source, tool_uses = _approx_tokens(assistant_text), "approx", None

        started_ts = activity_row.get("ts") if activity_row else None
        duration_ms = _duration_ms_since(started_ts) if isinstance(started_ts, str) else None
        task_id = activity_row.get("task_id") if activity_row else None

        # TASK-094 LEG B — "model attr from harness usage blocks": prefer the
        # harness's own per-turn reported model (a live, runtime-accurate
        # signal) over `_model_for_persona`'s STATIC frontmatter read; the
        # static value stays the fallback for transcripts that never surface
        # a `message.model` field (see `_extract_exact_usage_from_transcript`'s
        # docstring).
        model = harness_model or _model_for_persona(persona, _repo_root())

        _record_dispatch_telemetry(
            _repo_root(),
            persona=persona,
            model=model,
            marker=marker,
            tokens=tokens,
            token_source=token_source,
            tool_uses=tool_uses,
            duration_ms=duration_ms,
            session_id=session_id,
            task_id=task_id if isinstance(task_id, str) and task_id else None,
        )

    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(0)
