"""Seam test: a real ALLOW and a real DENY through .claude/hooks/broker-gate.py
(TASK-118).

Real integration boundary: `broker-gate.py` is invoked as an actual subprocess
(the exact way the PreToolUse:Task hook harness invokes it), fed a real
`broker_state.json` + a real sqlite `project.db`, and its actual stage
decomposition (1a bookkeeping -> 1b state read -> 1c approval/turn/persona/
notepad checks -> 5 planning-gate DB lookup) runs unmocked end to end — never
a unit-level call into an internal function. Runs in RITUAL mode
(NEXUS_RITUAL_AUTHORITY=1) so no HMAC capability-token minting is needed to
exercise the gate's real allow/deny decision logic — the token-mode path is
already the dedicated subject of .claude/hooks/tests/test_broker_gate.py.

Helpers below are hand-duplicated from that suite (same file-local-duplication
convention it already documents) so this seam file has no cross-test-file
coupling and stays entirely inside its nexus-broker/tests/seams/ scope.
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
GATE_SCRIPT = _REPO_ROOT / ".claude" / "hooks" / "broker-gate.py"


def _utc_now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()  # noqa: UP017


def _write_state(path: Path, **fields) -> None:
    path.write_text(json.dumps(fields))


def _make_db(path: Path, planning_rows: list[dict] | None = None) -> None:
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        CREATE TABLE context_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT, logged_at TEXT, action_type TEXT,
            files_modified TEXT, decision_refs TEXT, task_updates TEXT, summary TEXT
        );
        CREATE TABLE feature_specs (id TEXT PRIMARY KEY, status TEXT);
        """
    )
    for row in planning_rows or []:
        conn.execute(
            "INSERT INTO context_log (session_id, logged_at, action_type, summary) "
            "VALUES (?, ?, ?, ?)",
            ("s1", row["logged_at"], "planning-gate-submit", json.dumps(row["plan"])),
        )
    conn.commit()
    conn.close()


def _payload(persona: str, *, task_tier: str = "standard", feat: str | None = None) -> dict:
    brief: dict = {
        "goal": "do a thing",
        "context_files": ["a.py"],
        "acceptance_criteria": ["it works"],
        "do_not_touch": ["nexus-package/"],
        "task_tier": task_tier,
        "notepad_topic": "TASK-118",
    }
    if feat:
        brief["feat"] = feat
    return {
        "tool_name": "Task",
        "input": {
            "subagent_type": persona,
            "description": "```json\n" + json.dumps(brief) + "\n```",
        },
        "session_id": "S-seam-broker-gate",
    }


def _run(payload: dict, *, state_path: Path, db_path: Path) -> tuple[int, dict | None, str]:
    env = {**os.environ}
    env["NEXUS_BROKER_STATE_PATH"] = str(state_path)
    env["_HOOK_DB_PATH"] = str(db_path)
    env["NEXUS_RITUAL_AUTHORITY"] = "1"
    env.pop("NEXUS_BROKER_ALLOW_DEGRADED", None)
    env.pop("NEXUS_BROKER_GATE_DAEMON_MODE", None)
    proc = subprocess.run(
        [sys.executable, str(GATE_SCRIPT)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
        timeout=10,
    )
    parsed: dict | None = None
    if proc.stdout.strip():
        try:
            parsed = json.loads(proc.stdout)
        except json.JSONDecodeError:
            parsed = None
    return proc.returncode, parsed, proc.stderr


def test_seam_allow_full_compliant_code_dispatch(tmp_path: Path) -> None:
    """GIVEN a broker-approved state (fresh turn + fresh notepad) and a recent
    ACCEPTED planning-gate row for the same feature, WHEN a Standard/Complex
    code-writing dispatch runs the real gate end to end, THEN it exits 0
    (allow) — driving stages 1a/1b/1c/5 for real, no stage mocked out.
    """
    assert GATE_SCRIPT.is_file(), f"broker-gate.py not found at {GATE_SCRIPT}"

    db = tmp_path / "project.db"
    _make_db(db, planning_rows=[{"logged_at": _utc_now(), "plan": {"feat": "FEAT-SEAM"}}])
    state = tmp_path / "broker_state.json"
    _write_state(
        state,
        approved=True,
        persona="forge-wire",
        called_at=_utc_now(),
        notepad_logged_at=_utc_now(),
    )

    code, out, err = _run(
        _payload("forge-wire", task_tier="complex", feat="FEAT-SEAM"),
        state_path=state,
        db_path=db,
    )

    assert code == 0, f"a fully compliant brief must pass. got {code}. out={out} stderr={err!r}"


def test_seam_deny_unapproved_dispatch_blocks(tmp_path: Path) -> None:
    """GIVEN a broker state the broker itself marked unapproved, WHEN a
    dispatch runs the real gate end to end, THEN it exits 2 with a real-object
    PreToolUse deny decision naming the rejection on both stdout and stderr —
    the fail-closed path the daemon-incident class depends on staying intact.
    """
    db = tmp_path / "project.db"
    _make_db(db)
    state = tmp_path / "broker_state.json"
    _write_state(state, approved=False, persona="forge-wire", called_at=_utc_now())

    code, out, err = _run(_payload("forge-wire"), state_path=state, db_path=db)

    assert code == 2, f"gate MUST exit 2 to block. got {code}. stderr={err!r}"
    assert isinstance(out, dict), f"expected real JSON stdout, got {out!r}"
    inner = out.get("hookSpecificOutput")
    assert isinstance(inner, dict), f"hookSpecificOutput must be a real object: {inner!r}"
    assert inner.get("hookEventName") == "PreToolUse"
    assert inner.get("permissionDecision") == "deny"
    assert "not allowed" in inner.get("permissionDecisionReason", "").lower()
    assert "not allowed" in err.lower(), f"deny reason must also surface on stderr: {err!r}"
