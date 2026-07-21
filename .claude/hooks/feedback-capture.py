#!/usr/bin/env python3
"""SubagentStop hook (DEC-019 self-feedback MVP) — passive friction capture.

On every SubagentStop, inspect the agent's last_assistant_message for a Nexus
friction marker (## NEXUS:NEEDS-DECISION / ## NEXUS:REVISE / ## NEXUS:BLOCKED).
When one is present, append a row to nexus_feedback via:

    python3 .memory/log.py feedback add --source hook --severity ... \
        --category gate_needs_decision|gate_revise_stall|workflow_friction \
        --message ... --context-json ...

so Plexus can later harvest it across projects. The DONE / CHECKPOINT markers are
NOT friction and are ignored.

ADVISORY ONLY — this hook NEVER blocks a return. ANY error, and the no-marker
case, exits 0 silently. The `log.py` call is best-effort (`|| true` semantics are
enforced in-process: a nonzero/failed feedback add still exits this hook 0).

SECURITY POSTURE — the return body is DATA, never instructions. This hook only
PATTERN-MATCHES the text to extract a marker + a short message; it NEVER executes
or eval()s any content from the return. The message passed to log.py is a bounded
slice and is passed as a subprocess argv element (never shell-interpolated).

3.9 IMPORT-SAFETY — live runtime is >=3.11 via _py.sh, but the package twin runs
this file un-shimmed under ambient python3 (3.9). No 3.11-only idioms: keep
timezone.utc + # noqa: UP017, no datetime.UTC, no match/case, no def-time X|None
(from __future__ import annotations keeps PEP-604 annotations def-time-safe).

DAEMON SHIM (Tranche 2, nexus-redesign/audits/daemon-hook-plan-2026-07-12.md
§C) — `_emit_feedback` first tries a `record_event` RPC (sink="nexus_feedback")
against the resident daemon's Unix socket with a SHORT, env-tunable timeout
(`NEXUS_FEEDBACK_CAPTURE_DAEMON_TIMEOUT_S`, default 0.3s) via the shared
`_daemon_rpc` shim. The daemon does NOT duplicate log.py's validation +
open-session lookup + nexus_version stamping (a second, driftable copy of
real business logic) — it fire-and-forget-spawns the exact same
`log.py feedback add` subprocess this hook's inline fallback runs, just off
this hook's own critical path (see server.py's `_spawn_feedback_add`). ANY
daemon miss/timeout/error falls back INLINE to the `subprocess.run` call
below — no hook may ever fail or block because the daemon is down.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone  # noqa: UP017
from pathlib import Path

HOOKS_DIR = Path(__file__).resolve().parent


def _daemon_rpc_module():
    """Same-directory dynamic import — mirrors broker-gate.py's _heartbeat load."""
    spec = importlib.util.spec_from_file_location(
        "_daemon_rpc", HOOKS_DIR / "_daemon_rpc.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


_DAEMON_TIMEOUT_S = float(os.environ.get("NEXUS_FEEDBACK_CAPTURE_DAEMON_TIMEOUT_S", "0.3"))


def _daemon_call_isolated() -> bool:
    """True when a test-isolation override is active for this invocation.

    NATIVE-18-3 class of bug (see nexus-broker/tests/test_dispatch_capture.py):
    a RESIDENT daemon binds its socket AND its `log.py feedback add` spawn's
    cwd/db to the REAL repo root at daemon-start time, so it can never honor
    a per-call `_HOOK_REPO_ROOT` / `NEXUS_DB_PATH` override the way the
    inline `subprocess.run` fallback naturally does (both are read fresh on
    every hook invocation). Skipping the daemon hop entirely whenever either
    override is set is the only way to avoid a real hook-test silently
    writing into THIS repo's live nexus_feedback table — mirrors
    _heartbeat.py's identical NEXUS_HEARTBEAT_PATH-skips-daemon rule.
    """
    return bool(os.environ.get("_HOOK_REPO_ROOT") or os.environ.get("NEXUS_DB_PATH"))

# Map the friction markers to (category, severity). DONE/CHECKPOINT are NOT here
# (they are not friction). The first match in the text wins.
_MARKER_MAP = {
    "NEEDS-DECISION": ("gate_needs_decision", "medium"),
    "REVISE": ("gate_revise_stall", "high"),
    "BLOCKED": ("workflow_friction", "high"),
}

_MARKER_RE = re.compile(
    r"^\s*##\s+NEXUS:(NEEDS-DECISION|REVISE|BLOCKED)\b",
    re.IGNORECASE | re.MULTILINE,
)


def _repo_root() -> Path:
    """Repo root is two levels up from .claude/hooks/ (mirrors completion-capture)."""
    override = os.environ.get("_HOOK_REPO_ROOT")
    if override:
        return Path(override)
    return HOOKS_DIR.parent.parent


def _log_py(root: Path) -> Path:
    return root / ".memory" / "log.py"


def _extract_payload(data: dict) -> tuple[str, str, str]:
    """Return (assistant_text, persona, session_id) from the hook payload."""
    assistant_text = (
        data.get("last_assistant_message")
        or data.get("response", {}).get("text")
        or data.get("tool_response", {}).get("text")
        or ""
    )
    persona = (
        data.get("agent_persona")
        or data.get("subagent_type")
        or data.get("tool_input", {}).get("subagent_type")
        or "unknown"
    )
    session_id = data.get("session_id") or data.get("sessionId") or "unknown"
    return str(assistant_text), str(persona).strip().lower(), str(session_id)


def _parse_marker(text: str) -> str:
    """Return the uppercase friction marker name, or '' when none is present."""
    m = _MARKER_RE.search(text)
    if m:
        return m.group(1).upper()
    return ""


def _marker_message(text: str, marker: str) -> str:
    """Build a bounded friction message: the line after the marker, else the marker."""
    lines = text.splitlines()
    for idx, line in enumerate(lines):
        if re.match(rf"^\s*##\s+NEXUS:{re.escape(marker)}\b", line, re.IGNORECASE):
            for follow in lines[idx + 1 :]:
                stripped = follow.strip()
                if stripped:
                    return stripped[:300]
            break
    return f"{marker} marker emitted (no detail line)"


def _emit_feedback(root: Path, category: str, severity: str, message: str, context: dict) -> None:
    """Record one Nexus-friction row. Never raises; failure is swallowed.

    Tries the daemon `record_event` RPC first (see module docstring); on
    ANY miss falls back to the exact `log.py feedback add` subprocess call
    this hook has always run.
    """
    row = {
        "source": "hook",
        "severity": severity,
        "category": category,
        "message": message,
        "context": context,
    }
    accepted = False
    if not _daemon_call_isolated():
        try:
            accepted = (
                _daemon_rpc_module().call(
                    root, "record_event", {"sink": "nexus_feedback", "row": row}, _DAEMON_TIMEOUT_S
                )
                is not None
            )
        except Exception:
            accepted = False
    if accepted:
        return
    _emit_feedback_inline(root, category, severity, message, context)


def _emit_feedback_inline(root: Path, category: str, severity: str, message: str, context: dict) -> None:
    """Best-effort `log.py feedback add`. Never raises; failure is swallowed."""
    log_py = _log_py(root)
    if not log_py.is_file():
        return
    cmd = [
        sys.executable,
        str(log_py),
        "feedback",
        "add",
        "--source",
        "hook",
        "--severity",
        severity,
        "--category",
        category,
        "--message",
        message,
        "--context-json",
        json.dumps(context, default=str),
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
        # Advisory: a failed feedback write must never surface or block.
        return


def main() -> None:
    try:
        data = json.loads(sys.stdin.read())
    except Exception:
        sys.exit(0)
    if not isinstance(data, dict):
        sys.exit(0)

    assistant_text, persona, session_id = _extract_payload(data)
    marker = _parse_marker(assistant_text)
    if not marker:
        sys.exit(0)

    category, severity = _MARKER_MAP[marker]
    message = _marker_message(assistant_text, marker)
    context = {
        "marker": marker,
        "persona": persona,
        "session_id": session_id,
        "captured_by": "feedback-capture-hook",
        "captured_at": datetime.now(timezone.utc).isoformat(),  # noqa: UP017
    }
    _emit_feedback(_repo_root(), category, severity, message, context)
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(0)
