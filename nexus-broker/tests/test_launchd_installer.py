"""TASK-105 deliverable C — render test for tools/install_daemon_launchd.sh.

Only the --render-test mode is exercised (writes a plist to a temp dir and
validates it); install/uninstall touch launchctl and are operator-run."""

from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "tools" / "install_daemon_launchd.sh"


def test_installer_script_exists_and_parses() -> None:
    assert SCRIPT.is_file(), f"missing {SCRIPT}"
    proc = subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr


def test_render_test_produces_valid_plist() -> None:
    proc = subprocess.run(
        ["bash", str(SCRIPT), "--render-test"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "RENDER-TEST PASS" in proc.stdout


def test_render_test_label_digest_matches_socket_digest() -> None:
    """The launchd label digest must be the SAME sha256[:16] that keys the
    daemon socket and pidfile (broker/daemon/paths.py), so one project maps
    to exactly one label."""
    proc = subprocess.run(
        ["bash", str(SCRIPT), "--render-test", "--project-path", str(REPO_ROOT)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    match = re.search(r"com\.nexus\.daemon\.([0-9a-f]{16})\.plist", proc.stdout)
    assert match, proc.stdout
    expected = hashlib.sha256(str(REPO_ROOT.resolve()).encode("utf-8")).hexdigest()[:16]
    assert match.group(1) == expected
