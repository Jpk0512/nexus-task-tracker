"""Tests for .claude/hooks/do-not-touch-guard.sh — SubagentStop advisory hook.

The hook reads the approved brief's `do_not_touch` globs from broker_state.json,
diffs the working tree (tracked + untracked), and emits a nested
hookSpecificOutput WARNING on STDOUT naming any changed path that matches a
forbidden glob. It is ADVISORY: exit 0 always, never blocks.

Exercised via subprocess (the live invocation shape), driving the same env
overrides the hook honors — _HOOK_REPO_ROOT (a throwaway git repo as the tree to
diff) and NEXUS_BROKER_STATE_PATH (the seeded state file).
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[1] / "do-not-touch-guard.sh"


def _git(repo: Path, *args):
    return subprocess.run(
        ["git", *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=True,
    )


@pytest.fixture
def repo(tmp_path):
    """A throwaway git repo with one committed file, so HEAD exists and the hook's
    `git diff --name-only HEAD` has a baseline to diff against."""
    r = tmp_path / "tree"
    r.mkdir()
    _git(r, "init", "-q")
    _git(r, "config", "user.email", "t@t")
    _git(r, "config", "user.name", "t")
    (r / "keep.txt").write_text("baseline\n", encoding="utf-8")
    _git(r, "add", "keep.txt")
    _git(r, "commit", "-q", "-m", "baseline")
    return r


def _write_state(tmp_path, do_not_touch, approved=True):
    state = {
        "turn_id": "t-test",
        "approved": approved,
        "persona": "forge",
        "called_at": "2026-06-14T00:00:00+00:00",
        "approved_brief": {
            "goal": "g",
            "do_not_touch": do_not_touch,
        },
    }
    p = tmp_path / "broker_state.json"
    p.write_text(json.dumps(state), encoding="utf-8")
    return p


def run_hook(repo, state_path, payload=None):
    env = dict(os.environ)
    env["_HOOK_REPO_ROOT"] = str(repo)
    env["NEXUS_BROKER_STATE_PATH"] = str(state_path)
    proc = subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(payload or {"hook_event": "SubagentStop"}),
        capture_output=True,
        text=True,
        env=env,
    )
    return proc


def _warning(stdout):
    if not stdout.strip():
        return None
    return json.loads(stdout)["hookSpecificOutput"]


# ------------------------- positive: warning fires -------------------------


def test_matching_changed_file_fires_warning(repo, tmp_path):
    """A changed tracked file under a do_not_touch glob -> warning naming it."""
    state = _write_state(tmp_path, ["vendor/"])
    target = repo / "vendor"
    target.mkdir()
    (target / "lib.py").write_text("edited\n", encoding="utf-8")

    proc = run_hook(repo, state)
    assert proc.returncode == 0  # advisory: never blocks
    warn = _warning(proc.stdout)
    assert warn is not None
    assert warn["hookEventName"] == "SubagentStop"
    assert "vendor/lib.py" in warn["additionalContext"]
    assert "do_not_touch" in warn["additionalContext"]


def test_tracked_modification_fires_warning(repo, tmp_path):
    """Modifying an already-tracked, committed file that matches a glob fires."""
    forbidden = repo / "secret.py"
    forbidden.write_text("v1\n", encoding="utf-8")
    _git(repo, "add", "secret.py")
    _git(repo, "commit", "-q", "-m", "add secret")
    forbidden.write_text("v2 — tampered\n", encoding="utf-8")

    state = _write_state(tmp_path, ["*.py"])
    proc = run_hook(repo, state)
    assert proc.returncode == 0
    warn = _warning(proc.stdout)
    assert warn is not None
    assert "secret.py" in warn["additionalContext"]


def test_file_glob_matches(repo, tmp_path):
    """A `*.lock` file glob (not a directory) matches a changed lockfile."""
    state = _write_state(tmp_path, ["*.lock"])
    (repo / "uv.lock").write_text("locked\n", encoding="utf-8")
    proc = run_hook(repo, state)
    assert proc.returncode == 0
    warn = _warning(proc.stdout)
    assert warn is not None
    assert "uv.lock" in warn["additionalContext"]


# ------------------------- negative: silent -------------------------


def test_non_matching_changed_file_is_silent(repo, tmp_path):
    """A changed file OUTSIDE every glob -> no warning, empty stdout."""
    state = _write_state(tmp_path, ["vendor/"])
    (repo / "src_app.py").write_text("allowed edit\n", encoding="utf-8")
    proc = run_hook(repo, state)
    assert proc.returncode == 0
    assert proc.stdout.strip() == ""


def test_no_changes_is_silent(repo, tmp_path):
    """do_not_touch present but a clean tree -> silent."""
    state = _write_state(tmp_path, ["vendor/", "*.py"])
    proc = run_hook(repo, state)
    assert proc.returncode == 0
    assert proc.stdout.strip() == ""


def test_empty_do_not_touch_is_silent(repo, tmp_path):
    """An empty do_not_touch list never warns, even with a changed file."""
    state = _write_state(tmp_path, [])
    (repo / "vendor").mkdir()
    (repo / "vendor" / "x.sh").write_text("e\n", encoding="utf-8")
    proc = run_hook(repo, state)
    assert proc.returncode == 0
    assert proc.stdout.strip() == ""


def test_missing_state_file_is_silent(repo, tmp_path):
    """A nonexistent broker_state.json -> no globs -> silent, exit 0."""
    missing = tmp_path / "nope.json"
    (repo / "vendor").mkdir()
    (repo / "vendor" / "x.sh").write_text("e\n", encoding="utf-8")
    proc = run_hook(repo, missing)
    assert proc.returncode == 0
    assert proc.stdout.strip() == ""


def test_malformed_state_file_is_silent(repo, tmp_path):
    """A malformed state JSON -> silent, exit 0 (advisory; never errors)."""
    p = tmp_path / "broker_state.json"
    p.write_text("{not json", encoding="utf-8")
    (repo / "vendor").mkdir()
    (repo / "vendor" / "x.sh").write_text("e\n", encoding="utf-8")
    proc = run_hook(repo, p)
    assert proc.returncode == 0
    assert proc.stdout.strip() == ""


def test_no_approved_brief_is_silent(repo, tmp_path):
    """State with no approved_brief key (e.g. a rejection) -> silent."""
    p = tmp_path / "broker_state.json"
    p.write_text(
        json.dumps({"approved": False, "persona": "forge"}), encoding="utf-8"
    )
    (repo / "vendor").mkdir()
    (repo / "vendor" / "x.sh").write_text("e\n", encoding="utf-8")
    proc = run_hook(repo, p)
    assert proc.returncode == 0
    assert proc.stdout.strip() == ""
