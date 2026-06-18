"""OPT-053 — self-test for the non-empty-collection gate in conftest.py.

The gate (tests/conftest.py :: pytest_collection_modifyitems) turns a 0-test
collection into a HARD ERROR so the suite can never exit green by collecting
nothing (the false-green trap that OPT-051's testpaths fix also guards). This
file PROVES the gate works by driving pytest as a SUBPROCESS:

  ARMED (default): an empty selection (-k __nonexistent_marker__) must exit
    NON-ZERO with the gate's UsageError message — NOT a green 0-collected exit.
  DISARMED (--allow-empty-collection): the same empty selection is tolerated,
    proving normal runs are not collaterally broken by the gate and that the
    escape hatch exists for the rare intentional-empty case.

Subprocess (not in-process) because the gate raises DURING collection — it must
run in its own pytest session to be observed, and a nested in-process pytest run
would tangle the parent session's collection.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

_TESTS_DIR = Path(__file__).resolve().parent
_IMPOSSIBLE_K = "__nonexistent_marker_that_matches_no_test__"

# Recursion guard: these tests spawn CHILD pytest runs of this very file. The
# child must NOT spawn grandchildren. The parent sets this env var; a child sees
# it and skips the subprocess-spawning tests (it only needs to be COLLECTED/RUN
# as a target, not to re-spawn).
_CHILD_ENV = "OPT053_GATE_SELFTEST_CHILD"
_IS_CHILD = os.environ.get(_CHILD_ENV) == "1"


def _child_env() -> dict[str, str]:
    return {**os.environ, _CHILD_ENV: "1"}


def _run_pytest(*extra: str) -> subprocess.CompletedProcess[str]:
    """Run a child pytest scoped to THIS test file with an impossible -k filter.

    Scoping to one file (not the whole tests/ tree) keeps the child run fast and
    deterministic: the -k filter deselects this file's own tests → 0 collected,
    which is exactly the condition the gate must catch. `-p no:cacheprovider`
    avoids touching the parent's cache.
    """
    cmd = [
        sys.executable, "-m", "pytest",
        "-p", "no:cacheprovider",
        "-k", _IMPOSSIBLE_K,
        str(Path(__file__).resolve()),
        *extra,
    ]
    return subprocess.run(
        cmd, cwd=str(_TESTS_DIR.parent), capture_output=True, text=True,
        timeout=120, env=_child_env(),
    )


@pytest.mark.skipif(_IS_CHILD, reason="child run — must not re-spawn (recursion guard)")
def test_gate_fails_on_zero_collected_when_armed() -> None:
    """Empty selection → non-zero exit + the gate's message (NOT a green pass)."""
    proc = _run_pytest()
    combined = proc.stdout + proc.stderr

    assert proc.returncode != 0, (
        "0-collected run exited GREEN — the non-empty-collection gate did not "
        f"fire.\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    assert "non-empty-collection gate" in combined, (
        "gate fired but without its identifying message — "
        f"output was:\n{combined}"
    )


@pytest.mark.skipif(_IS_CHILD, reason="child run — must not re-spawn (recursion guard)")
def test_gate_disarmed_by_allow_empty_flag() -> None:
    """--allow-empty-collection tolerates an empty selection (escape hatch works).

    Pytest's own exit code for 'no tests collected' is 5; with the gate disarmed
    we expect that NON-erroring 'nothing ran' outcome (exit 5) rather than the
    gate's UsageError (exit 4). The key contract: the gate's message must be
    ABSENT — the gate did not abort the session.
    """
    proc = _run_pytest("--allow-empty-collection")
    combined = proc.stdout + proc.stderr

    assert "non-empty-collection gate" not in combined, (
        "gate fired despite --allow-empty-collection — escape hatch is broken.\n"
        f"output:\n{combined}"
    )
    # Exit 5 = pytest's own "no tests ran"; explicitly NOT 4 (UsageError) and
    # NOT a misleading 0 (green).
    assert proc.returncode == pytest.ExitCode.NO_TESTS_COLLECTED, (
        f"expected NO_TESTS_COLLECTED (5) with gate disarmed, got "
        f"{proc.returncode}\noutput:\n{combined}"
    )


def test_populated_run_is_unaffected_by_gate() -> None:
    """Sanity: a real selection collects >0 and exits cleanly (gate stays silent).

    In the PARENT this spawns a child pytest selecting exactly this test → 1
    item collected → the gate's empty-branch never runs → exit 0. In the CHILD
    (recursion guard set) it is a trivial pass: BEING collected-and-run here is
    itself the populated-run proof, and not re-spawning keeps it finite.
    """
    if _IS_CHILD:
        return  # the child's mere collection+run of this test IS the proof

    proc = subprocess.run(
        [
            sys.executable, "-m", "pytest", "-p", "no:cacheprovider",
            "-k", "test_populated_run_is_unaffected_by_gate",
            str(Path(__file__).resolve()),
        ],
        cwd=str(_TESTS_DIR.parent), capture_output=True, text=True, timeout=120,
        env=_child_env(),
    )
    combined = proc.stdout + proc.stderr
    assert proc.returncode == 0, f"populated run did not pass:\n{combined}"
    assert "non-empty-collection gate" not in combined
