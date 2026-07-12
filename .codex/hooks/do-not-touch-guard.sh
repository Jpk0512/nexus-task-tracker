#!/usr/bin/env python3
"""do-not-touch-guard.sh — SubagentStop hook (ADVISORY ONLY, exit 0 always).

A teammate just finished. This hook reads the approved brief's `do_not_touch`
globs from broker_state.json and cross-checks them against what the working tree
actually changed (tracked diff + untracked files). If a changed path matches a
forbidden glob, it emits a nested hookSpecificOutput WARNING on STDOUT naming the
violated files, so the orchestrator sees the scope breach at the SubagentStop
boundary and can act on it (RCA / revert / re-delegate).

NEVER blocks. This is a detector, not a gate — it exits 0 unconditionally even on
a match, on a missing/malformed state file, or on a git failure. The orchestrator
owns the decision; the hook only surfaces the signal. (Mirrors the advisory
contract of lesson-harvester.sh / context-reset-monitor.sh.)

Output contract (matches the other advisory hooks):
  {"hookSpecificOutput":{"hookEventName":"SubagentStop",
                         "additionalContext":<warning text>}}
on STDOUT only when at least one changed path matches a do_not_touch glob.
Otherwise STDOUT is silent.

Env overrides (test isolation, mirroring broker-gate.py):
  _HOOK_REPO_ROOT             — repo root (resolves state path + runs git here)
  NEXUS_BROKER_STATE_PATH     — explicit path to broker_state.json

NOTE: this file ships un-shimmed and runs under the project's ambient python3
(3.9 on stock macOS), so it MUST stay 3.9-import-safe — do NOT introduce
3.11-only idioms (datetime.UTC, def-time X | None, match/case).
"""
from __future__ import annotations

import contextlib
import fnmatch
import json
import os
import subprocess
import sys
from pathlib import Path


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


def _read_do_not_touch(state_path: Path) -> list:
    """Return the approved brief's do_not_touch globs, or [] when unavailable.

    Advisory: a missing / malformed / approval-less state file yields no globs
    (and therefore no warning) rather than an error — the hook must never break
    the SubagentStop path.
    """
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(state, dict):
        return []
    brief = state.get("approved_brief")
    if not isinstance(brief, dict):
        return []
    globs = brief.get("do_not_touch")
    if not isinstance(globs, list):
        return []
    return [g for g in globs if isinstance(g, str) and g.strip()]


def _changed_paths(repo_root: Path) -> list:
    """Tracked diff (vs HEAD) + untracked files, as repo-relative POSIX paths.

    Best-effort: any git failure (not a repo, no HEAD yet, git absent) yields an
    empty list so the hook stays silent rather than erroring.
    """
    paths: list = []
    for args in (
        ["git", "diff", "--name-only", "HEAD"],
        ["git", "ls-files", "--others", "--exclude-standard"],
    ):
        try:
            out = subprocess.run(
                args,
                cwd=str(repo_root),
                capture_output=True,
                text=True,
            )
        except (OSError, ValueError):
            continue
        if out.returncode != 0:
            continue
        for line in out.stdout.splitlines():
            p = line.strip()
            if p:
                paths.append(p)
    # De-dup while preserving order.
    seen = set()
    unique = []
    for p in paths:
        if p not in seen:
            seen.add(p)
            unique.append(p)
    return unique


def _matches(path: str, glob: str) -> bool:
    """True if a changed path falls under a do_not_touch glob.

    Handles both file globs (`*.py`, `install.sh`) and directory-prefix globs
    written with a trailing slash (`src/`) — the latter is treated as "anything
    under that directory", matching the brief author's intent that `src/`
    forbids the whole subtree.
    """
    normglob = glob.strip()
    if normglob.endswith("/"):
        prefix = normglob.rstrip("/")
        return path == prefix or path.startswith(prefix + "/")
    if fnmatch.fnmatch(path, normglob):
        return True
    # A bare directory name (no trailing slash, no wildcard) still forbids its
    # subtree — `src` should catch `src/app.py`.
    if "*" not in normglob and "?" not in normglob and "[" not in normglob:
        return path == normglob or path.startswith(normglob + "/")
    return False


def main() -> int:
    # Drain stdin so the harness never blocks on an unread pipe. The SubagentStop
    # payload is not needed — the violation signal comes from state + git.
    with contextlib.suppress(Exception):
        sys.stdin.read()

    repo_root = _repo_root()
    globs = _read_do_not_touch(_state_path(repo_root))
    if not globs:
        return 0

    changed = _changed_paths(repo_root)
    if not changed:
        return 0

    violations = []
    for path in changed:
        hit = next((g for g in globs if _matches(path, g)), None)
        if hit is not None:
            violations.append((path, hit))

    if not violations:
        return 0

    lines = [
        "[do-not-touch-guard] SCOPE BREACH — the teammate changed "
        f"{len(violations)} file(s) covered by the approved brief's do_not_touch "
        "globs. These paths were explicitly forbidden:",
    ]
    for path, glob in violations:
        lines.append(f"  - {path}  (matches do_not_touch glob '{glob}')")
    lines.append(
        "Review before NEXUS:DONE: revert the forbidden edits, or if the change "
        "was genuinely required, do an RCA and re-scope the brief. Do NOT close "
        "the task with a do_not_touch breach standing (no-deferral rule)."
    )

    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": "SubagentStop",
                "additionalContext": "\n".join(lines),
            }
        },
        sys.stdout,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
