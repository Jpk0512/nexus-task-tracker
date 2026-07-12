"""P5-01 — tests for the native-task-list visibility hooks.

Two hooks make dispatch/return state VISIBLE so the user can SEE (a) the
task-list status and (b) which agents were dispatched:

  - task-mirror.sh            (PostToolUse:Task) — emits a lifecycle line
        (DISPATCH / DONE / REVISE / BLOCKED) as additionalContext so each
        dispatch→return is traceable in-session and names the native-list
        transition the orchestrator should reflect.
  - session-task-reconcile.sh (SessionStart) — reads OPEN tasks from
        project.db (via `log.py context dump`) and prints a LOUD banner so the
        native panel can be reconciled against ground truth at session start.

Each hook is invoked as a subprocess exactly as the Claude Code harness does
(JSON on stdin; exit code + stdout/stderr asserted). Behavior was confirmed by
direct execution before these assertions were written — no mocked happy paths.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

HOOKS_DIR = Path(__file__).parent.parent
REPO_ROOT = HOOKS_DIR.parent.parent
MEMORY_DIR = REPO_ROOT / ".memory"
LOG_PY = MEMORY_DIR / "log.py"
SCHEMA_SQL = MEMORY_DIR / "schema.sql"

# The interpreter the hooks/log.py actually use (sqlite-vec capable). The venv
# python is preferred; fall back to the one running pytest.
def _venv_python() -> str:
    for cand in (
        MEMORY_DIR / ".venv" / "bin" / "python",
        MEMORY_DIR / ".venv" / "bin" / "python3",
    ):
        if cand.exists():
            return str(cand)
    import sys

    return sys.executable


# ─── helpers ────────────────────────────────────────────────────────────────


def run_bash_hook(
    script: str,
    payload: dict,
    env: dict | None = None,
    stdin: str | None = None,
) -> tuple[int, str, str]:
    """Invoke a shell hook via /bin/bash (matches the harness)."""
    merged = {**os.environ}
    if env:
        merged.update(env)
    data = stdin if stdin is not None else json.dumps(payload)
    result = subprocess.run(
        ["/bin/bash", str(HOOKS_DIR / script)],
        input=data,
        capture_output=True,
        text=True,
        env=merged,
        timeout=20,
    )
    return result.returncode, result.stdout, result.stderr


def _parse_stdout_json(out: str) -> dict:
    out = out.strip()
    if not out:
        return {}
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return {}


def _additional_context(out: str) -> str:
    return (
        _parse_stdout_json(out)
        .get("hookSpecificOutput", {})
        .get("additionalContext", "")
    )


# ─── task-mirror.sh (PostToolUse:Task) ───────────────────────────────────────


class TestTaskMirror:
    """Surfaces the dispatch→return lifecycle as a visible additionalContext line."""

    SCRIPT = "task-mirror.sh"

    def _dispatch_payload(self, persona: str, brief: dict) -> dict:
        # Brief delivered as a fenced JSON block inside tool_input.description —
        # the shape skills-required-guard / broker-gate parse.
        block = "```json\n" + json.dumps(brief) + "\n```"
        return {
            "tool_name": "Task",
            "tool_input": {"subagent_type": persona, "description": block},
        }

    def test_dispatch_emits_in_progress_line(self) -> None:
        payload = self._dispatch_payload(
            "forge-ui", {"task_id": "TASK-042", "intent": "implement_ui"}
        )
        code, out, err = run_bash_hook(self.SCRIPT, payload)
        assert code == 0, f"PostToolUse must never block, got {code}"
        ctx = _additional_context(out)
        assert ctx.startswith("[task-mirror] DISPATCH"), (
            f"A dispatch with no return marker must announce DISPATCH, got: {out!r}"
        )
        assert "persona=forge-ui" in ctx
        assert "task=TASK-042" in ctx
        assert "IN_PROGRESS" in ctx, "Must name the native-list in_progress transition"
        # Lifecycle is also echoed to stderr so it survives a collapsed context.
        assert "[task-mirror] DISPATCH" in err

    def test_done_return_emits_completed_line(self) -> None:
        payload = {
            "tool_name": "Task",
            "tool_input": {"subagent_type": "forge-ui", "description": "task TASK-042"},
            "tool_response": (
                "All done.\n\n## NEXUS:DONE\n\n"
                '```json\n{"files_changed": ["a.ts"]}\n```\n'
            ),
        }
        code, out, _err = run_bash_hook(self.SCRIPT, payload)
        assert code == 0
        ctx = _additional_context(out)
        assert ctx.startswith("[task-mirror] DONE"), (
            f"An accepted NEXUS:DONE must announce DONE, got: {out!r}"
        )
        assert "persona=forge-ui" in ctx
        assert "task=TASK-042" in ctx
        assert "COMPLETED" in ctx, "Must name the native-list completed transition"

    def test_revise_return_keeps_in_progress(self) -> None:
        payload = {
            "tool_name": "Task",
            "tool_input": {"subagent_type": "pipeline-data"},
            "tool_response": "## NEXUS:REVISE\nplease fix the failing test",
        }
        code, out, _err = run_bash_hook(self.SCRIPT, payload)
        assert code == 0
        ctx = _additional_context(out)
        assert ctx.startswith("[task-mirror] REVISE"), (
            f"A REVISE return must announce REVISE (not DONE), got: {out!r}"
        )
        assert "persona=pipeline-data" in ctx
        assert "IN_PROGRESS" in ctx, "REVISE means the task stays in_progress"
        # A REVISE must NOT be mistaken for a completion.
        assert "COMPLETED" not in ctx

    def test_blocked_return_keeps_in_progress(self) -> None:
        payload = {
            "tool_name": "Task",
            "tool_input": {"subagent_type": "hermes"},
            "tool_response": "## NEXUS:BLOCKED\nmissing credentials",
        }
        code, out, _err = run_bash_hook(self.SCRIPT, payload)
        assert code == 0
        ctx = _additional_context(out)
        assert ctx.startswith("[task-mirror] BLOCKED")
        assert "COMPLETED" not in ctx

    def test_agent_style_toplevel_persona_is_read(self) -> None:
        # Some payloads carry subagent_type at the top level (no nested brief).
        payload = {"subagent_type": "atlas", "tool_response": "working on it"}
        code, out, _err = run_bash_hook(self.SCRIPT, payload)
        assert code == 0
        ctx = _additional_context(out)
        assert "persona=atlas" in ctx
        assert ctx.startswith("[task-mirror] DISPATCH")

    def test_marker_only_return_fills_unknown_persona_and_task(self) -> None:
        # A real return (carries a NEXUS marker) but with no persona/task_id in
        # the payload — the line must still be well-formed, never printing an
        # empty field. The marker is the signal that makes this worth surfacing.
        payload = {"tool_name": "Task", "tool_input": {}, "tool_response": "## NEXUS:DONE"}
        code, out, _err = run_bash_hook(self.SCRIPT, payload)
        assert code == 0
        ctx = _additional_context(out)
        assert "persona=unknown" in ctx
        assert "task=(no task id)" in ctx
        assert ctx.startswith("[task-mirror] DONE")

    def test_no_signal_payload_is_silent(self) -> None:
        # Valid JSON dict but no persona AND no NEXUS marker — not a real
        # dispatch/return. Must stay silent rather than emit a misleading
        # "DISPATCH persona=unknown" line on noise.
        payload = {"tool_name": "Task", "tool_input": {}, "tool_response": "hi"}
        code, out, _err = run_bash_hook(self.SCRIPT, payload)
        assert code == 0
        assert out.strip() == "", f"A no-signal payload must be silent, got: {out!r}"

    def test_emitted_additional_context_is_valid_json(self) -> None:
        # The stdout MUST be a single parseable JSON object (the harness parses it).
        payload = self._dispatch_payload("forge-wire", {"task_id": "TASK-7"})
        code, out, _err = run_bash_hook(self.SCRIPT, payload)
        assert code == 0
        parsed = _parse_stdout_json(out)
        assert parsed.get("hookSpecificOutput", {}).get("hookEventName") == "PostToolUse"


# ─── session-task-reconcile.sh (SessionStart) ────────────────────────────────


class TestSessionTaskReconcile:
    """Reads open project.db tasks via log.py and prints a LOUD banner.

    The hook resolves both log.py AND the python interpreter from $REPO_ROOT, so
    tests build a throwaway REPO_ROOT skeleton (real log.py + schema + an
    initialized, seeded project.db) and point the hook at it. This exercises the
    real `context dump` read path end-to-end rather than mocking it.
    """

    SCRIPT = "session-task-reconcile.sh"

    def _make_repo(self) -> str:
        root = tempfile.mkdtemp(prefix="p5-reconcile-")
        mem = Path(root) / ".memory"
        (mem / "files").mkdir(parents=True)
        shutil.copy(LOG_PY, mem / "log.py")
        shutil.copy(SCHEMA_SQL, mem / "schema.sql")
        subprocess.run(
            [_venv_python(), str(mem / "log.py"), "init"],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
        return root

    def _add_task(self, root: str, **kw: str) -> None:
        args = [_venv_python(), str(Path(root) / ".memory" / "log.py"), "task", "add"]
        for k, v in kw.items():
            args += [f"--{k.replace('_', '-')}", v]
        subprocess.run(args, capture_output=True, text=True, timeout=20, check=True)

    def _run(self, root: str) -> tuple[int, str, str]:
        result = subprocess.run(
            ["/bin/bash", str(HOOKS_DIR / self.SCRIPT)],
            input="{}",
            capture_output=True,
            text=True,
            env={**os.environ, "REPO_ROOT": root},
            timeout=30,
        )
        return result.returncode, result.stdout, result.stderr

    def test_open_tasks_print_loud_banner(self) -> None:
        root = self._make_repo()
        try:
            self._add_task(
                root,
                id="TASK-900",
                title="Wire native task mirror",
                status="in_progress",
                priority="high",
                assigned_to="forge",
            )
            self._add_task(
                root,
                id="TASK-901",
                title="Backfill registry",
                status="todo",
                priority="medium",
            )
            code, _out, err = self._run(root)
            assert code == 0, "SessionStart must never block"
            # The banner goes to stderr (advisory surface, like memory-errors-banner).
            assert "OPEN TASKS AT SESSION START" in err, (
                f"Open tasks must produce the LOUD banner, got stderr: {err!r}"
            )
            assert "1 in_progress, 1 other open" in err
            # The in_progress task is headlined with the ▶ glyph; backlog with •.
            assert "▶ TASK-900" in err
            assert "Wire native task mirror" in err
            assert "(forge)" in err
            assert "• TASK-901" in err
            assert "(unassigned)" in err, "A null owner must render as 'unassigned'"
            # The drift-reconciliation instruction must be present (the whole point).
            assert "native" in err.lower() and "DRIFTED" in err
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_no_open_tasks_prints_clean_line_not_banner(self) -> None:
        root = self._make_repo()
        try:
            code, _out, err = self._run(root)
            assert code == 0
            # An empty (initialized) DB: a short reassurance line, NOT the banner,
            # NOT a silent no-op (so the user knows the panel is legitimately empty).
            assert "No open tasks in project.db" in err
            assert "OPEN TASKS AT SESSION START" not in err
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_done_tasks_are_excluded(self) -> None:
        root = self._make_repo()
        try:
            self._add_task(
                root, id="TASK-902", title="Already finished", status="done"
            )
            code, _out, err = self._run(root)
            assert code == 0
            # A done task is not "open" — it must not appear and must not trip
            # the banner.
            assert "TASK-902" not in err
            assert "No open tasks in project.db" in err
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_missing_log_py_fails_loud_not_silent(self) -> None:
        # Point REPO_ROOT at a dir with no .memory/log.py: the hook must say so on
        # stderr and still exit 0 (advisory), never wedge SessionStart.
        root = tempfile.mkdtemp(prefix="p5-reconcile-empty-")
        try:
            code, _out, err = self._run(root)
            assert code == 0
            assert "ERROR" in err and "log.py" in err, (
                f"A broken install must fail LOUD on stderr, got: {err!r}"
            )
        finally:
            shutil.rmtree(root, ignore_errors=True)
