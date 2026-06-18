"""Security regression test for memory-consolidator.sh (WF6 heredoc RCE fix).

The Stop hook's "apply ops" block used to interpolate the *model-controlled*
API ``$RESPONSE`` (and ``$FILES_DIR``) raw into a Python literal inside an
UNQUOTED heredoc delimiter::

    python3 - <<APPLYEOF
    response_raw = '''$RESPONSE'''
    ...
    APPLYEOF

With an unquoted delimiter the shell expands the heredoc body BEFORE python
sees it, so a ``$RESPONSE`` containing ``$(...)``, backticks, or quotes is a
remote-code-execution / shell-substitution vector: the API response is attacker
/ model influenced, so a crafted response can run arbitrary commands or corrupt
the python source.

The fix mirrors the already-safe ``$CONTEXT`` / ``REQUEST_BODY`` pattern in the
same file: pass the value through the ENVIRONMENT (``_MEM_RESPONSE`` /
``_MEM_FILES_DIR``) and QUOTE the heredoc delimiter (``<<'APPLYEOF'``) so the
shell performs NO expansion of the body and python reads the value verbatim via
``os.environ``.

These tests are hermetic. The hook derives its repo root from its OWN location
(``git -C "$(dirname "$0")" rev-parse --show-toplevel``), not from cwd — so we
COPY the real hook into a throwaway git repo and run that copy, giving it a
minimal seeded ``.memory/project.db`` (only the columns the gate/context queries
touch). We shadow ``curl`` with a PATH stub that returns a crafted malicious
response. They assert BOTH directions:

  * SECURITY (deny): the injected ``$(touch SENTINEL)`` / backtick payloads do
    NOT execute — no sentinel file appears anywhere.
  * BEHAVIOR PARITY (allow): the hook still exits 0 and performs its normal
    consolidation (writes ``.memory/files/progress.md`` from the ops carried in
    the response, and prints the ``done`` marker).

Run from nexus-package/:
    uv run pytest .claude/hooks/tests/test_memory_consolidator.py -v
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parent.parent / "memory-consolidator.sh"

# Sentinel paths an injection would create if shell substitution fired.
# Kept out of /tmp directly so a stale file from another run cannot mask a
# regression — each test gets its own tmp_path-rooted sentinel where possible.
_GIT = shutil.which("git")
_BASH = shutil.which("bash") or "/bin/bash"


def _seed_db(db_path: Path) -> None:
    """Create the minimal project.db the consolidator gates require.

    Only the tables/columns the two python gate queries read are created:
      * sessions      — id, started_at, ended_at, summary, next_step
      * context_log   — session_id, action_type, summary, logged_at
      * validation_log— session_id, task_or_brief_hash, verdict, logged_at
      * tasks         — id, title, status, assigned_to
      * decisions     — id, title, decision, logged_at
      * agent_notepad / agent_root_cause_log — session_id (count-only)

    One OPEN session (ended_at IS NULL) plus a state-changing context_log row
    (action_type='task_update') makes the CHANGED gate non-zero AND the CONTEXT
    blob non-empty, so execution reaches the vulnerable apply-ops block.
    """
    import sqlite3

    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(
            """
            CREATE TABLE sessions (
                id INTEGER PRIMARY KEY,
                started_at TEXT,
                ended_at TEXT,
                summary TEXT,
                next_step TEXT
            );
            CREATE TABLE context_log (
                session_id INTEGER,
                action_type TEXT,
                summary TEXT,
                logged_at TEXT
            );
            CREATE TABLE validation_log (
                session_id INTEGER,
                task_or_brief_hash TEXT,
                verdict TEXT,
                validated_at TEXT
            );
            CREATE TABLE tasks (
                id TEXT PRIMARY KEY,
                title TEXT,
                status TEXT,
                assigned_to TEXT
            );
            CREATE TABLE decisions (
                id TEXT PRIMARY KEY,
                title TEXT,
                decision TEXT,
                decided_at TEXT
            );
            CREATE TABLE agent_notepad (session_id INTEGER);
            CREATE TABLE agent_root_cause_log (session_id INTEGER);
            """
        )
        conn.execute(
            "INSERT INTO sessions (id, started_at, ended_at) VALUES (?, ?, NULL)",
            (1, "2026-06-01T00:00:00"),
        )
        # state-changing event → CHANGED gate > 0
        conn.execute(
            "INSERT INTO context_log (session_id, action_type, summary, logged_at) "
            "VALUES (?, ?, ?, ?)",
            (1, "task_update", "did the thing", "2026-06-01T00:01:00"),
        )
        conn.execute(
            "INSERT INTO tasks (id, title, status, assigned_to) VALUES (?, ?, ?, ?)",
            ("TASK-1", "open task", "in_progress", "forge"),
        )
        conn.commit()
    finally:
        conn.close()


def _stub_curl(bin_dir: Path, response_json: str) -> None:
    """Install a PATH-shadowing `curl` that ignores its args and prints
    `response_json` verbatim on stdout (exit 0), so the hook's RESPONSE=$(curl…)
    receives our crafted payload without any network access."""
    curl = bin_dir / "curl"
    # Write the response to a sidecar file so the stub never has to embed the
    # (hostile) payload in its own source — it just cats the file.
    payload_file = bin_dir / "response.json"
    payload_file.write_text(response_json, encoding="utf-8")
    curl.write_text(
        "#!/usr/bin/env bash\n"
        f'cat {json.dumps(str(payload_file))}\n'
        "exit 0\n",
        encoding="utf-8",
    )
    curl.chmod(0o755)


def _make_repo(tmp_path: Path, *, seed: bool = True) -> Path:
    """git-init a throwaway repo, COPY the real hook into it (so its
    ``dirname "$0"`` → ``git rev-parse`` resolves to THIS repo, not the live
    nexus-package tree), and seed .memory. Returns the repo path; the hook copy
    lives at ``<repo>/.claude/hooks/memory-consolidator.sh``."""
    assert _GIT, "git is required for this hermetic test"
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run([_GIT, "init", "-q"], cwd=str(repo), check=True)
    (repo / ".memory" / "files").mkdir(parents=True)
    hooks_dir = repo / ".claude" / "hooks"
    hooks_dir.mkdir(parents=True)
    shutil.copy2(HOOK, hooks_dir / HOOK.name)
    if seed:
        _seed_db(repo / ".memory" / "project.db")
    return repo


def _hook_in(repo: Path) -> Path:
    return repo / ".claude" / "hooks" / HOOK.name


def _run(repo: Path, bin_dir: Path) -> subprocess.CompletedProcess[str]:
    env = {**os.environ}
    # curl stub first on PATH; real git/python3 still resolvable behind it.
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    # Reach the curl branch: a non-empty key is all the hook checks.
    env["ANTHROPIC_API_KEY"] = "test-key-not-used-by-stub"
    # Point the AI base anywhere; the stub curl ignores the URL.
    env["AI_API_BASE_URL"] = "https://stub.invalid"
    return subprocess.run(
        [_BASH, str(_hook_in(repo))],
        cwd=str(repo),
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


def _rce_breakout_response(sentinel: Path) -> str:
    """The REAL exploit. The vulnerable hook interpolated $RESPONSE straight into
    a triple-quoted Python literal under an UNQUOTED heredoc::

        response_raw = '''$RESPONSE'''

    A $RESPONSE that contains ``'''`` closes that literal early, and everything
    after it is parsed as Python SOURCE — i.e. arbitrary code execution. (The
    API response is model/attacker influenced, so this is reachable.) Note: the
    classic shell ``$(...)`` / backtick form does NOT execute here because bash
    does not re-scan an expanded VARIABLE's value for command substitution — the
    breakout vector is the Python-literal escape, which this payload exercises.

    Verified against a reconstructed pre-fix hook: this payload runs
    ``os.system('touch SENTINEL')`` (sentinel appears); against the fixed hook
    (env-passed value + ``<<'APPLYEOF'`` quoted delimiter) the ``'''`` is inert
    data and no sentinel is created. It is deliberately NOT valid JSON so the
    fixed hook hits a benign parse-error and exits 0 without side effects."""
    s = str(sentinel)
    return (
        "'''\n"
        f"import os; os.system('touch {s}')\n"
        "_x = '''"
    )


def _benign_with_hostile_content(sentinel: Path) -> str:
    """A VALID Anthropic-messages response whose ops `content` embeds shell
    metacharacters (``$(touch …)``, backticks, quotes, newlines, ``rm -rf``).
    The normal consolidation path must WRITE that string to progress.md as inert
    DATA — never execute it. Used for the behavior-parity (allow) direction."""
    hostile = (
        f"$(touch {sentinel}) `touch {sentinel}` "
        "'single' \"double\"\nnewline ; rm -rf / ;"
    )
    ops = {
        "ops": [
            {
                "action": "ADD",
                "file": "progress.md",
                "content": "consolidated progress\nmarker=" + hostile,
            }
        ]
    }
    return json.dumps({"content": [{"type": "text", "text": json.dumps(ops)}]})


# ---------------------------------------------------------------------------
# SECURITY (deny): the breakout payload must NOT execute.
# ---------------------------------------------------------------------------


def test_python_literal_breakout_does_not_execute(tmp_path: Path) -> None:
    """Given an API response that closes the ``'''`` Python literal and appends
    ``import os; os.system('touch SENTINEL')``, When the Stop hook applies it,
    Then NO sentinel file is created — the value is read from the environment
    inside a QUOTED heredoc, so the breakout is inert data (RCE closed).

    This is the bug-reproducing assertion: it FAILS against the pre-fix
    unquoted-heredoc hook (the sentinel appears) and PASSES against the fix."""
    repo = _make_repo(tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    sentinel = tmp_path / "pwned_sentinel"
    _stub_curl(bin_dir, _rce_breakout_response(sentinel))

    result = _run(repo, bin_dir)

    assert not sentinel.exists(), (
        "INJECTION FIRED: the sentinel file was created, meaning the "
        "model-controlled $RESPONSE escaped the triple-quoted Python literal "
        "and executed (the unquoted-heredoc RCE). The delimiter must be quoted "
        "(`<<'APPLYEOF'`) and the value passed via the environment."
    )
    stray = list(tmp_path.glob("**/pwned_sentinel*"))
    assert stray == [], f"Unexpected injection side-effect files: {stray}"
    assert result.returncode == 0, (
        f"Hook crashed on hostile input (rc={result.returncode}): {result.stderr}"
    )


def test_shell_metachars_in_content_do_not_execute(tmp_path: Path) -> None:
    """Given a VALID response whose ops content carries ``$(touch SENTINEL)`` /
    backticks, When the hook applies it, Then no sentinel is created — the
    metacharacters reach python (and the written file) only as data."""
    repo = _make_repo(tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    sentinel = tmp_path / "pwned_sentinel"
    _stub_curl(bin_dir, _benign_with_hostile_content(sentinel))

    result = _run(repo, bin_dir)

    assert not sentinel.exists(), (
        "Shell metacharacters in the model content were executed — they must be "
        "inert data."
    )
    assert result.returncode == 0, result.stderr


# ---------------------------------------------------------------------------
# BEHAVIOR PARITY (allow): the normal consolidation still happens.
# ---------------------------------------------------------------------------


def test_consolidation_still_runs_on_hostile_input(tmp_path: Path) -> None:
    """Given a valid response whose ops content carries shell metacharacters,
    When the hook runs, Then it still exits 0 AND performs its normal work: the
    ADD op is applied to .memory/files/progress.md and the 'done' marker is
    printed. The hostile string is written as inert file CONTENT, never
    executed."""
    repo = _make_repo(tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    sentinel = tmp_path / "pwned_sentinel"
    _stub_curl(bin_dir, _benign_with_hostile_content(sentinel))

    result = _run(repo, bin_dir)

    assert result.returncode == 0, (
        f"Expected clean exit 0, got rc={result.returncode}: {result.stderr}"
    )
    progress = repo / ".memory" / "files" / "progress.md"
    assert progress.exists(), (
        "Behavior regression: the ADD op was not applied — progress.md is "
        "missing, so the consolidation path no longer runs after the fix."
    )
    body = progress.read_text(encoding="utf-8")
    assert "consolidated progress" in body, (
        f"progress.md content did not come from the response ops: {body!r}"
    )
    # The hostile metacharacters are present in the file as literal DATA —
    # written, not executed.
    assert "$(touch" in body and "rm -rf" in body, (
        "Expected the hostile string to be stored verbatim as file content "
        f"(inert data), got: {body!r}"
    )
    assert "[memory-consolidator] done" in result.stdout, (
        f"Expected the consolidator done marker on stdout, got: {result.stdout!r}"
    )


# ---------------------------------------------------------------------------
# GATE (allow-through): with NO state changes, the hook short-circuits (exit 0,
# no curl, no files) — proving the security fix did not alter the gate behavior.
# ---------------------------------------------------------------------------


def test_no_changes_short_circuits(tmp_path: Path) -> None:
    """Given a repo whose session has NO state-changing events, When the hook
    runs, Then it exits 0 early, never calls the (stub) curl, and writes no
    files — the gate that precedes the apply-ops block is untouched by the fix."""
    repo = _make_repo(tmp_path, seed=False)

    import sqlite3

    db = repo / ".memory" / "project.db"
    conn = sqlite3.connect(str(db))
    try:
        conn.executescript(
            """
            CREATE TABLE sessions (id INTEGER PRIMARY KEY, started_at TEXT,
                ended_at TEXT, summary TEXT, next_step TEXT);
            CREATE TABLE context_log (session_id INTEGER, action_type TEXT,
                summary TEXT, logged_at TEXT);
            CREATE TABLE validation_log (session_id INTEGER,
                task_or_brief_hash TEXT, verdict TEXT, validated_at TEXT);
            CREATE TABLE tasks (id TEXT PRIMARY KEY, title TEXT, status TEXT,
                assigned_to TEXT);
            CREATE TABLE decisions (id TEXT PRIMARY KEY, title TEXT,
                decision TEXT, decided_at TEXT);
            CREATE TABLE agent_notepad (session_id INTEGER);
            CREATE TABLE agent_root_cause_log (session_id INTEGER);
            """
        )
        # Open session but ZERO state-changing rows → CHANGED == 0.
        conn.execute(
            "INSERT INTO sessions (id, started_at, ended_at) VALUES (1, ?, NULL)",
            ("2026-06-01T00:00:00",),
        )
        conn.commit()
    finally:
        conn.close()

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    sentinel = tmp_path / "pwned_sentinel"
    # A curl stub that, if ever called, would BOTH create a tripwire and emit a
    # hostile payload — so reaching it is detectable.
    tripwire = tmp_path / "curl_was_called"
    curl = bin_dir / "curl"
    curl.write_text(
        "#!/usr/bin/env bash\n"
        f"touch {json.dumps(str(tripwire))}\n"
        f"cat {json.dumps(str(_make_payload_file(tmp_path, sentinel)))}\n",
        encoding="utf-8",
    )
    curl.chmod(0o755)

    result = _run(repo, bin_dir)

    assert result.returncode == 0, result.stderr
    assert not tripwire.exists(), (
        "Gate regression: curl was called even though there were no "
        "state-changing events this session — the CHANGED gate should "
        "short-circuit before the API call."
    )
    assert not (repo / ".memory" / "files" / "progress.md").exists(), (
        "No consolidation should occur when the change gate is zero."
    )


def _make_payload_file(tmp_path: Path, sentinel: Path) -> Path:
    """Sidecar hostile payload for the short-circuit test's curl stub."""
    p = tmp_path / "sc_response.json"
    ops = {"ops": [{"action": "ADD", "file": "progress.md",
                    "content": f"$(touch {sentinel})"}]}
    p.write_text(
        json.dumps({"content": [{"type": "text", "text": json.dumps(ops)}]}),
        encoding="utf-8",
    )
    return p


def _traversal_response(fname: str, content: str = "PWNED") -> str:
    """Build an Anthropic-messages response whose single op targets `fname`.

    Used to inject model-controlled traversal payloads through the curl stub
    so the full hook (not just the apply-ops block in isolation) is exercised.
    """
    ops = {"ops": [{"action": "ADD", "file": fname, "content": content}]}
    return json.dumps({"content": [{"type": "text", "text": json.dumps(ops)}]})


# ---------------------------------------------------------------------------
# CWE-22 regression tests (path-traversal containment)
# ---------------------------------------------------------------------------
# The apply-ops block receives `fname` from model-controlled API JSON.
# A compromised or adversarial model could return "../../tmp/PWNED.md" or
# "/etc/passwd". These tests drive the FULL hook (not just the isolated block)
# via the hermetic curl-stub approach, so any regression in the hook itself
# — not just the extracted test block — is caught.
#
# Security invariants:
#   (A) ALLOWED allowlist rejects filenames outside the 4-file set.
#   (B) realpath + commonpath rejects symlink escapes for ALLOWED filenames.


@pytest.mark.parametrize(
    "traversal_fname",
    [
        "../../tmp/PWNED.md",
        "/etc/passwd",
        "reflections/../../../tmp/escape.md",
    ],
)
def test_cwe22_traversal_payload_blocked(
    tmp_path: Path, traversal_fname: str
) -> None:
    """Given a model response with a path-traversal fname, When the full hook
    runs with a curl stub delivering that payload, Then no file is created
    outside .memory/files/ and the hook still exits 0 (fail-open).

    This is a regression guard: it FAILS if the ALLOWED allowlist or the
    realpath/commonpath containment guard is removed or broken.
    """
    repo = _make_repo(tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub_curl(bin_dir, _traversal_response(traversal_fname))

    result = _run(repo, bin_dir)

    assert result.returncode == 0, (
        f"Hook must exit 0 (fail-open) on traversal payload. "
        f"rc={result.returncode} stderr={result.stderr!r}"
    )

    # The hook's .memory/files sandbox must be empty — no traversal file written.
    files_dir = repo / ".memory" / "files"
    written = list(files_dir.rglob("*"))
    assert written == [], (
        f"CWE-22: files written inside sandbox: {written}. "
        f"Traversal fname={traversal_fname!r} must not produce any output."
    )

    # Belt-and-suspenders: well-known escape targets must not exist on disk.
    assert not Path("/tmp/PWNED.md").exists(), (
        "CWE-22: /tmp/PWNED.md was created — containment guard is bypassed."
    )
    assert not Path("/tmp/escape.md").exists(), (
        "CWE-22: /tmp/escape.md was created — containment guard is bypassed."
    )

    # Stderr must carry the SKIP diagnostic so operators can detect probing.
    assert "SKIP" in result.stderr, (
        f"Hook must log a SKIP diagnostic to stderr for traversal payload "
        f"{traversal_fname!r}. Got stderr={result.stderr!r}"
    )


def test_cwe22_symlink_bypass_blocked(tmp_path: Path) -> None:
    """Given a symlink inside .memory/files/ that resolves outside the sandbox,
    and an ops payload targeting 'reflections/INDEX.md' (which IS in ALLOWED),
    When the full hook runs, Then the escape target is NOT written and the hook
    exits 0.

    This tests the belt-and-suspenders realpath+commonpath check — the ALLOWED
    allowlist alone cannot stop symlink escapes.
    """
    repo = _make_repo(tmp_path)

    # Plant a symlink inside files_dir pointing outside the sandbox.
    files_dir = repo / ".memory" / "files"
    escape_dir = tmp_path / "escape_target"
    escape_dir.mkdir()
    (files_dir / "reflections").symlink_to(escape_dir)

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    # "reflections/INDEX.md" is in ALLOWED — only realpath can block this.
    _stub_curl(bin_dir, _traversal_response("reflections/INDEX.md", "SYMLINK_PWNED"))

    result = _run(repo, bin_dir)

    assert result.returncode == 0, (
        f"Hook must exit 0 on symlink-bypass payload. "
        f"rc={result.returncode} stderr={result.stderr!r}"
    )

    escaped_file = escape_dir / "INDEX.md"
    assert not escaped_file.exists(), (
        "CWE-22: symlink bypass succeeded — reflections/INDEX.md was written to "
        f"{escaped_file} (outside .memory/files/). "
        "The realpath commonpath guard is broken."
    )

    assert "SKIP" in result.stderr, (
        "Hook must emit a SKIP diagnostic for symlink escape. "
        f"Got stderr={result.stderr!r}"
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
