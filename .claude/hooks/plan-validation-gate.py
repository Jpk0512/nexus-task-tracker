#!/usr/bin/env python3
"""plan-validation-gate.py — SubagentStop hook.

Thin shim over the plan-validation CLI (`python -m broker.plan_validation score
<file> --json`, nexus-broker/src/broker/plan_validation/score.py) — the gate's
deterministic core, which already folds in the invocability check
(`broker.plan_validation.checks.invocability`) AND the opt-in probes
(`broker.plan_validation.probes.gate.run_probes`, wired into `score_plan`
itself — self-gated, a no-op on a T0/T1 non-irreversible plan). This shim
NEVER reimplements DAG / skills / write-scope / invocability / probe logic
itself — it only decides WHICH file(s) to score and what to do with the
CLI's own pass/fail verdict.

A live `planner` persona must never exist without this gate on its return
path — never even transiently. This file and the dispatch-shape-guard /
deliverables / SKILL_MAP registration that makes `planner` dispatchable land
together so that invariant is never violated, not even between two commits.

WHO IT FIRES FOR: the SubagentStop payload itself does NOT reliably carry the
persona (see return-validator.py's note — SubagentStop payloads here often
omit agent_persona/subagent_type/tool_input.subagent_type). This gate
instead reads `.memory/files/broker_state.json`'s top-level `persona` field —
written by broker-gate.py at PreToolUse approval time — mirroring
do-not-touch-guard.sh's own state-file-over-payload precedent. A missing/
malformed state file, or a persona other than "planner", is a silent ALLOW
(this gate is a no-op for every other returning persona).

WHAT IT SCORES: every `.md` file the planner's turn actually changed under its
own write surface (`docs/plans/**`, `.memory/plans/**`) — tracked diff vs HEAD
plus untracked files, exactly do-not-touch-guard.sh's `_changed_paths`
approach. No changed plan file -> nothing to validate -> ALLOW (a planner that
legitimately returned BLOCKED/NEEDS-DECISION before writing anything is not
penalized here).

FAIL-CLOSED (unlike most advisory hooks in this tree, which fail OPEN on an
internal error): once this gate has confirmed the turn IS a planner return
with at least one plan file to score, ANY failure — the scorer CLI exits
non-{0,1}, the interpreter is missing, the JSON verdict cannot be parsed, a
subprocess timeout — is treated exactly like a failing verdict and DENIES. A
crash must never look like a pass.

Env overrides (test isolation, mirroring do-not-touch-guard.sh / broker-gate.py):
  _HOOK_REPO_ROOT                 — repo root (resolves state path + runs git here)
  NEXUS_BROKER_STATE_PATH         — explicit path to broker_state.json
  NEXUS_PLAN_VALIDATION_BROKER_DIR — cwd + venv root for the scorer invocation
                                      (default: <repo_root>/nexus-broker) — lets
                                      tests point the scorer at the real broker
                                      install while diffing a throwaway git tree
  NEXUS_PLAN_VALIDATION_TIMEOUT   — seconds before the scorer subprocess is
                                     killed and treated as a gate error (default 60)

The scorer subprocess invokes the broker venv's OWN interpreter directly
(`<broker_dir>/.venv/bin/python3`), never `uv run` — `uv run` silently queues
behind ANY other concurrent `uv` process touching the same project's env-lock,
which is a confirmed source of flaky TimeoutExpired failures under concurrent
test/gate runs.

NOTE: kept 3.9-import-safe (no `datetime.UTC`, no def-time `X | None`, no
`match`/`case`) like every other hook module in this tree — the package twin
runs un-shimmed under ambient python3.
"""
from __future__ import annotations

import contextlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

EVENT = "SubagentStop"
_PLAN_GLOB_PREFIXES = ("docs/plans/", ".memory/plans/")
_DEFAULT_TIMEOUT_S = 60.0


def _repo_root() -> Path:
    env = os.environ.get("_HOOK_REPO_ROOT")
    if env:
        return Path(env)
    here = Path(__file__).resolve()
    for candidate in [here, *here.parents]:
        if (candidate / ".memory").is_dir():
            return candidate
    return here.parent.parent.parent


def _state_path(repo_root: Path) -> Path:
    env = os.environ.get("NEXUS_BROKER_STATE_PATH")
    if env:
        return Path(env)
    return repo_root / ".memory" / "files" / "broker_state.json"


def _broker_dir(repo_root: Path) -> Path:
    env = os.environ.get("NEXUS_PLAN_VALIDATION_BROKER_DIR")
    if env:
        return Path(env)
    return repo_root / "nexus-broker"


def _turn_persona(state_path: Path) -> str:
    """The last-approved dispatch's persona, or "" on any read/parse failure.

    Mirrors do-not-touch-guard.sh's resilience: a missing/malformed state file
    means "we cannot identify this turn", not "deny everything" — this gate
    only ever fires on a CONFIRMED planner persona.
    """
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    if not isinstance(state, dict):
        return ""
    return str(state.get("persona", "") or "").strip().lower()


def _changed_paths(repo_root: Path) -> tuple:
    """Tracked diff (vs HEAD) + untracked files, as repo-relative POSIX paths.

    Returns (paths, ok). Mirrors do-not-touch-guard.sh._changed_paths' git
    invocation, but — unlike that ADVISORY hook — this gate is fail-closed:
    `ok=False` (git itself could not be run/exceptioned on EVERY attempt)
    must NOT be silently read as "nothing changed", since that would let a
    genuinely un-enumerable planner turn sail through as an implicit ALLOW.
    A git command that runs and simply exits non-zero (e.g. "not a git
    repo") is treated the same way — ok=False — for the same reason.
    """
    paths: list = []
    any_ok = False
    for args in (
        ["git", "diff", "--name-only", "HEAD"],
        ["git", "ls-files", "--others", "--exclude-standard"],
    ):
        try:
            out = subprocess.run(
                args, cwd=str(repo_root), capture_output=True, text=True, timeout=15,
            )
        except (OSError, ValueError, subprocess.TimeoutExpired):
            continue
        if out.returncode != 0:
            continue
        any_ok = True
        for line in out.stdout.splitlines():
            p = line.strip()
            if p:
                paths.append(p)
    seen = set()
    unique = []
    for p in paths:
        if p not in seen:
            seen.add(p)
            unique.append(p)
    return unique, any_ok


def _plan_files(repo_root: Path) -> tuple:
    """Changed paths under the planner's own write surface, `.md` only.

    Returns (files, ok) — see `_changed_paths`; `ok=False` is a gate error,
    never silently treated as "no plan files".
    """
    changed, ok = _changed_paths(repo_root)
    result = []
    for path in changed:
        if not path.endswith(".md"):
            continue
        if any(path.startswith(prefix) for prefix in _PLAN_GLOB_PREFIXES):
            result.append(path)
    return sorted(result), ok


def _venv_python(broker_dir: Path) -> Path:
    """The broker venv's own interpreter — see the module docstring note:
    never `uv run` here, it silently queues behind any concurrent `uv`
    process on the same project's env-lock."""
    return broker_dir / ".venv" / "bin" / "python3"


def _score(repo_root: Path, rel_path: str) -> tuple:
    """Run the plan-validation CLI on one plan file. Returns (status, detail):
    status is "pass" | "fail" | "error"; detail is a short human string.

    NEVER reimplements score_plan's own checks — this only shells out to the
    CLI already exposed for exactly this purpose and interprets its exit
    code (`0` = overall_pass, `1` = a real, deterministic fail — see
    broker.plan_validation.score._cli_score) and JSON verdict.
    """
    broker_dir = _broker_dir(repo_root)
    abs_path = str(repo_root / rel_path)
    timeout = float(os.environ.get("NEXUS_PLAN_VALIDATION_TIMEOUT", _DEFAULT_TIMEOUT_S))
    py = _venv_python(broker_dir)
    try:
        proc = subprocess.run(
            [str(py), "-m", "broker.plan_validation", "score", abs_path, "--json"],
            cwd=str(broker_dir), capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return "error", f"'{rel_path}': scorer timed out after {timeout:.0f}s"
    except OSError as exc:
        return "error", f"'{rel_path}': could not invoke the scorer ({exc})"

    if proc.returncode not in (0, 1):
        stderr_tail = (proc.stderr or "").strip()[-300:]
        return "error", f"'{rel_path}': scorer exited {proc.returncode} unexpectedly: {stderr_tail}"

    try:
        verdict = json.loads(proc.stdout)
    except (json.JSONDecodeError, ValueError):
        stderr_tail = (proc.stderr or "").strip()[-300:]
        return "error", f"'{rel_path}': scorer produced non-JSON output: {stderr_tail}"

    if not isinstance(verdict, dict) or "overall_pass" not in verdict:
        return "error", f"'{rel_path}': scorer verdict missing 'overall_pass'"

    if verdict["overall_pass"] is True:
        return "pass", f"'{rel_path}': PASS"

    # Most sub-verdicts (acyclic, mece, skills_derived, ...) key their pass/fail
    # under "pass"; the N09 "probes" sub-verdict (score_plan's `probes` key, the
    # dict `run_probes` returns) instead keys it under "overall_pass" — check
    # both so a probe-only failure (stub-mutation/citation) is still named here,
    # not silently collapsed to the generic "overall_pass=false" fallback.
    failing = sorted(
        key for key, val in verdict.items()
        if isinstance(val, dict) and (val.get("pass") is False or val.get("overall_pass") is False)
    )
    return "fail", f"'{rel_path}': FAIL ({', '.join(failing) or 'overall_pass=false'})"


def main() -> int:
    with contextlib.suppress(Exception):
        sys.stdin.read()

    repo_root = _repo_root()
    persona = _turn_persona(_state_path(repo_root))
    if persona != "planner":
        return 0

    plan_files, git_ok = _plan_files(repo_root)

    problems = []
    if not git_ok:
        problems.append(
            "could not enumerate changed files (git diff/ls-files both failed) — "
            "cannot confirm there is nothing to validate"
        )
    for rel_path in plan_files:
        status, detail = _score(repo_root, rel_path)
        if status != "pass":
            problems.append(detail)

    if not problems:
        return 0

    gd_path = Path(__file__).parent / "_gate_deny.py"
    spec = importlib.util.spec_from_file_location("_gate_deny", gd_path)
    gate_deny_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gate_deny_mod)  # type: ignore[union-attr]

    reason = (
        "planner return failed the plan-validation gate (fail-closed) — "
        + "; ".join(problems)
    )
    return gate_deny_mod.deny(EVENT, "PLAN-VALIDATION/FAIL", reason)


if __name__ == "__main__":
    sys.exit(main())
