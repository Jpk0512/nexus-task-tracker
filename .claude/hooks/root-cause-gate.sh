#!/usr/bin/env python3
# SubagentStop hook: enforces Root Cause Analysis discipline (DEC-028).
#
# Rules (DEC-028 — advisory only, no mechanical min-why-count floor):
#   - Every error-fix MUST state a root cause (true underlying cause, not
#     symptom). Why-chain DEPTH is at the fixer's discretion — NO min count.
#   - A symptom-only fix remains a contract violation.
#   - When an RCA block is absent entirely, emit ONE advisory nudge (exit 0).
#   - scout/lens/lens-fast/palette on REVISE/BLOCKED: exempt entirely (exit 0).
#   - Passes append a row to .memory/files/agent_root_cause_log.jsonl
#     (ADR-001 Phase 0 — fire-and-forget telemetry; no gate reads this back
#     synchronously, so a durable JSONL journal replaces the old raw
#     sqlite3 INSERT + schema-init DDL entirely).
#
# Returns exit 0 always (advisory — never blocks).

import importlib.util
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Load _heartbeat from the same hooks directory. Best-effort only — see
# _heartbeat.py; this MUST NEVER change exit code/behavior of this gate.
try:
    _hb_path = Path(__file__).parent / "_heartbeat.py"
    _hb_spec = importlib.util.spec_from_file_location("_heartbeat", _hb_path)
    _heartbeat_mod = importlib.util.module_from_spec(_hb_spec)
    _hb_spec.loader.exec_module(_heartbeat_mod)
except Exception:
    _heartbeat_mod = None


def _emit_heartbeat(event, decision, latency_ms):
    if _heartbeat_mod is None:
        return
    _heartbeat_mod.emit_heartbeat("root-cause-gate", event, decision, latency_ms)


_START_TIME = time.time()


def _elapsed_ms():
    try:
        return int((time.time() - _START_TIME) * 1000)
    except Exception:
        return 0


def _resolve_db_path() -> str:
    """Resolve the project.db path at RUNTIME.

    Precedence:
      1. _HOOK_DB_PATH env override (used by tests and custom installs).
      2. git rev-parse --show-toplevel from this script's directory.
    Falls back to <cwd>/.memory/project.db if git is unavailable.
    """
    override = os.environ.get("_HOOK_DB_PATH")
    if override:
        return override
    script_dir = os.path.dirname(os.path.abspath(__file__))
    try:
        repo = subprocess.run(
            ["git", "-C", script_dir, "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        repo = os.getcwd()
    return os.path.join(repo, ".memory", "project.db")


DB_PATH = _resolve_db_path()

# ADR-001 Phase 0: journal lives beside the (env/git-resolved) DB path rather
# than a separate root — mirrors _HOOK_DB_PATH's existing test seam (a
# scratch DB) so a test pointing _HOOK_DB_PATH at an isolated file also gets
# an isolated journal, with zero new env plumbing.
FILES_DIR = Path(DB_PATH).parent / "files"
JOURNAL_PATH = FILES_DIR / "agent_root_cause_log.jsonl"

# DEC-028: personas exempt from RCA checks on REVISE/BLOCKED markers.
# Their job is to investigate / validate / report — not to fix.
RCA_EXEMPT_PERSONAS = frozenset({"scout", "lens", "lens-fast", "palette"})

FIX_KEYWORDS = re.compile(
    r"\b(fix|bug|error|regression|broken|hangs|crashes|500)\b", re.IGNORECASE
)
WHY_LINE = re.compile(r"^\s*Why\s+\d+\s*:", re.IGNORECASE | re.MULTILINE)
RCA_HEADER = re.compile(r"##\s+Root Cause Analysis", re.IGNORECASE)
MARKER_RE = re.compile(
    r"##\s+NEXUS:(DONE|REVISE|BLOCKED|CHECKPOINT|NEEDS-DECISION)", re.IGNORECASE
)


def _append_journal(row):
    """Fire-and-forget JSONL append (ADR-001 Phase 0): no reader ever
    synchronously blocks on this row (advisory RCA capture only), so a
    durable append-only journal replaces the old raw sqlite3 INSERT + DDL —
    no independent writer connection to project.db remains in this hook.

    Returns an error string on failure (never raises) — mirrors the old
    sqlite3.Error swallow so this hook still never blocks.
    """
    try:
        FILES_DIR.mkdir(parents=True, exist_ok=True)
        with open(JOURNAL_PATH, "a") as fh:
            fh.write(json.dumps(row) + "\n")
        return None
    except OSError as exc:
        return str(exc)


def extract_rca(text: str) -> tuple[str, list[str], str]:
    """Return (symptom, why_chain, pattern_fix) from the RCA block, or empty."""
    m = RCA_HEADER.search(text)
    if not m:
        return "", [], ""
    block = text[m.start():]
    # Grab the next H2 boundary as end of block
    next_h2 = re.search(r"\n##\s+", block[3:])
    block = block[: next_h2.start() + 3] if next_h2 else block

    symptom = ""
    sm = re.search(r"Symptom\s*:\s*(.+)", block, re.IGNORECASE)
    if sm:
        symptom = sm.group(1).strip()

    # Collect the full line text for each Why N:
    why_chain: list[str] = []
    for wm in re.finditer(r"(Why\s+\d+\s*:.+)", block, re.IGNORECASE):
        why_chain.append(wm.group(1).strip())

    pattern_fix = ""
    pfm = re.search(r"Pattern\s+fix\s*:\s*(.+)", block, re.IGNORECASE)
    if pfm:
        pattern_fix = pfm.group(1).strip()

    return symptom, why_chain, pattern_fix


def _warn_extract_miss(payload: dict) -> None:
    """EXTRACT_OK canary (S1-22): valid SubagentStop JSON yielded NO assistant text.

    Harness schema drift (renamed payload keys) would silently disarm this gate —
    every return would look empty and exit 0 forever. Warn LOUDLY instead of
    staying silent (still exit 0: warn, not block). Once per session via a flag
    file keyed on session_id so repeat returns do not spam the orchestrator.
    """
    if not isinstance(payload, dict) or not payload:
        return
    import contextlib
    import tempfile
    sid = re.sub(r"[^A-Za-z0-9_-]", "_", str(payload.get("session_id") or "unknown"))[:64]
    flag = os.path.join(tempfile.gettempdir(), ".nexus-extract-miss-root-cause-gate-" + sid)
    if os.path.exists(flag):
        return
    with contextlib.suppress(OSError):
        open(flag, "w").close()
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "SubagentStop",
            "additionalContext": (
                "[root-cause-gate] EXTRACT-MISS: SubagentStop payload had no "
                "extractable assistant text — possible harness schema drift"
            ),
        }
    }))


def main() -> int:
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        # Non-JSON input — fail-safe, do not block.
        return 0

    # Extract fields from hook payload (multiple possible paths).
    assistant_text: str = (
        payload.get("last_assistant_message")
        or payload.get("response", {}).get("text")
        or payload.get("tool_response", {}).get("text")
        or ""
    )
    session_id: str = payload.get("session_id", "unknown")
    tool_input = payload.get("tool_input", {})
    if not isinstance(tool_input, dict):
        tool_input = {}
    # NATIVE-4: agent_type / tool_input.agent_type added to the persona
    # fallback chain (mirrors return-validator.py's _extract()). This harness
    # dispatches via the Agent tool, which carries the persona under
    # subagent_type for Task-shaped dispatches but under agent_type for
    # Agent/Team-shaped dispatches — an Agent-tool SubagentStop payload was
    # falling through all subagent_type-flavoured keys straight to "unknown".
    agent_name: str = (
        payload.get("agent_persona")
        or payload.get("subagent_type")
        or payload.get("agent_type")
        or tool_input.get("subagent_type")
        or tool_input.get("agent_type")
        or "unknown"
    )
    task_description: str = (
        payload.get("task_description")
        or tool_input.get("description")
        or os.environ.get("CLAUDE_TASK_DESCRIPTION", "")
        or ""
    )

    if not assistant_text:
        _warn_extract_miss(payload)
        return 0

    # Determine which marker is present.
    marker_match = MARKER_RE.search(assistant_text)
    if not marker_match:
        return 0

    marker = marker_match.group(1).upper()

    # DEC-028: exempt personas on REVISE/BLOCKED — their job is to report, not fix.
    if marker in ("REVISE", "BLOCKED") and agent_name in RCA_EXEMPT_PERSONAS:
        return 0

    needs_rca = marker in ("REVISE", "BLOCKED") or (
        marker == "DONE" and FIX_KEYWORDS.search(task_description)
    )

    if not needs_rca:
        return 0

    symptom, why_chain, pattern_fix = extract_rca(assistant_text)
    rca_present = RCA_HEADER.search(assistant_text) is not None

    # DEC-028: advisory only — no blocking on count. Nudge if RCA block is absent.
    if not rca_present:
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "SubagentStop",
                "additionalContext": (
                    "[root-cause-gate] ADVISORY — fix-tasks should include a "
                    "## Root Cause Analysis block stating the true underlying cause "
                    "(not just the symptom). Why-chain depth is at your discretion. "
                    "See Constitution Article X (DEC-028).\n"
                    "  Marker: NEXUS:" + marker
                ),
            }
        }))
        return 0

    # Pass — append to the JSONL journal (fail-safe: a write failure does
    # not block the agent, mirroring the old sqlite3.Error swallow).
    _append_journal({
        "session_id": session_id,
        "agent_name": agent_name,
        "task_summary": task_description[:200],
        "symptom": symptom,
        "why_chain": why_chain,
        "pattern_fix": pattern_fix,
        "logged_at": datetime.now(timezone.utc).isoformat(),  # noqa: UP017
    })

    return 0


if __name__ == "__main__":
    # main() returns an int (always 0 — advisory only, never blocks). Capture
    # it here so heartbeat covers every one of main()'s early-return exit
    # paths without touching its internal control flow.
    _rc = main()
    _emit_heartbeat("SubagentStop", "block" if _rc == 2 else "allow", _elapsed_ms())
    sys.exit(_rc)
