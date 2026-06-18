"""Phase 5a — second writer instance refuses to start.

Spawns two `python -m broker.vault.writer` processes simultaneously; asserts
exactly one wins the fcntl lock and the other exits non-zero with the
"already running" message.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path


def _writer_lock_path() -> Path:
    return Path(os.path.expanduser("~/.cache/nexus-research/writer.lock"))


def test_second_writer_exits_nonzero(config_local) -> None:
    # Clear any stale lock from prior runs.
    lock = _writer_lock_path()
    lock.parent.mkdir(parents=True, exist_ok=True)
    if lock.exists():
        # Best-effort; the lock file persisting is fine — flock is fd-based.
        pass

    env = os.environ.copy()
    # We use --once so the *winner* exits cleanly; the *loser* exits immediately
    # because the lock is held during the entire run.
    cmd = [
        sys.executable,
        "-m",
        "broker.vault.writer",
        "--db",
        str(config_local.db_path),
        "--vault-root",
        str(config_local.vault_root),
        "--once",
    ]

    # Make the winner block long enough that the loser observes the lock held.
    # We use a small inflight delay by running the winner with --once on an
    # empty queue — that completes almost instantly, so we instead intercept
    # by starting the winner in the background WITHOUT --once and letting it
    # poll, then race a second --once invocation against it.

    background_cmd = [
        sys.executable,
        "-m",
        "broker.vault.writer",
        "--db",
        str(config_local.db_path),
        "--vault-root",
        str(config_local.vault_root),
    ]
    winner = subprocess.Popen(
        background_cmd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        # Give the winner enough time to grab the lock.
        time.sleep(0.5)

        loser = subprocess.run(
            cmd,
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert loser.returncode != 0, (
            f"second writer should fail; stdout={loser.stdout!r} "
            f"stderr={loser.stderr!r}"
        )
        assert "already running" in (loser.stderr + loser.stdout)
    finally:
        winner.terminate()
        try:
            winner.wait(timeout=5)
        except subprocess.TimeoutExpired:
            winner.kill()
            winner.wait(timeout=5)
