"""Root broker test config — OPT-053 non-empty-collection gate.

THE FALSE-GREEN TRAP this closes: `pyproject.toml` declares
`testpaths = ["tests"]`. If that directory is ever empty, renamed, or a glob
silently matches nothing, pytest collects ZERO tests and exits 0 — a green run
that proves nothing. OPT-051 fixed the testpaths declaration; this gate is the
belt-and-braces partner: it turns "0 tests collected" from a silent green into a
HARD ERROR, so the suite can never again pass by collecting nothing.

Implementation: `pytest_collection_finish` fires once per session AFTER the full
collection — including every `pytest_collection_modifyitems` deselection (-k/-m
keyword/mark filtering runs there). `session.items` at that point is the final
selected set, so an empty `session.items` is the genuine 0-collected condition.
(`pytest_collection_modifyitems` is the WRONG hook for this: this plugin's impl
can run BEFORE the builtin -k deselection, seeing a pre-filter non-empty list.)
When the final set is empty we raise `pytest.UsageError`, which aborts the
session with a non-zero exit and a clear message — never a green exit.

NON-INTERFERENCE with normal runs:
  * A normal full run collects 200+ items → the branch never triggers.
  * `--collect-only` introspection is exempt (it is a deliberate inspection, not
    a verification run that could be mistaken for green).
  * The gate is opt-OUTable for the gate's OWN self-test via the
    `--allow-empty-collection` flag this conftest registers, so the test that
    proves the gate fires on an empty selection (-k __nonexistent__) can also
    prove the escape hatch leaves an empty run un-erroring. Production CI never
    passes that flag, so the trap stays armed.
"""
from __future__ import annotations

import contextlib
import os
import sys

import pytest

# NEX-002: must precede every broker.* import in this process — beartype's claw
# only instruments submodules imported AFTER the hook below is installed. None
# of the stdlib/pytest imports above touch broker.*, so this is still "first",
# and placing it after them (rather than before) keeps this file E402-clean.
# See _beartype_activation.py for the covered-module list and why it's scoped.
from _beartype_activation import activate as _nex002_activate_beartype

_nex002_activate_beartype()

# Several tests in this suite dynamically load nexus-package/ modules in-process
# (`importlib.util.spec_from_file_location(...).exec_module(...)` against
# nexus-package/.memory/health.py, nexus-package/tools/safe_update.py, etc. —
# see test_health_conformance.py, test_install_selfverify.py,
# test_observability_graduation.py, test_safe_update.py,
# test_update_venv_and_tomllib.py). A plain in-process import respects
# `sys.dont_write_bytecode`, so left at the default (False) those loads write
# real __pycache__/*.pyc files INTO nexus-package/, self-dirtying the exact
# tree that tools/build_snapshot.sh --check's R0 packaging-artifact hygiene
# scan later inspects — TestBuildSnapshotCheckModeCleanTree then fails on
# artifacts THIS suite generated, not on any real packaging defect. Setting
# this at conftest import time (before collection, before any test module
# runs) covers every in-process load for the whole session.
#
# `sys.dont_write_bytecode` alone only protects THIS interpreter — it is not
# inherited by child processes. Several tests also shell out to real installer
# machinery that imports nexus-package/ modules in a FRESH subprocess against
# the SOURCE tree (not a tmp copy): e.g. nexus-package/install.sh's own
# `python3 - <<PYHEALTHCHECK` heredoc `sys.path.insert(0, ...); from health
# import CORE_TABLES`, and R4a-shaped `import broker.server` smoke checks —
# invoked from test_install_selfverify.py's real-install fixture and similar.
# A bare subprocess.run() inherits os.environ by default, so exporting
# PYTHONDONTWRITEBYTECODE=1 here (interpreter-startup equivalent of `-B`)
# propagates the same suppression down the whole process tree, covering those
# subprocess-spawned interpreters too. This does NOT cover
# `subprocess.run([sys.executable, "-m", "py_compile", ...])` calls —
# `py_compile` ignores sys.dont_write_bytecode/-B/PYTHONDONTWRITEBYTECODE
# unconditionally; those are isolated separately via PYTHONPYCACHEPREFIX
# (see test_build_gate_behavioral.py's `_pycache_isolated_env`).
sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"


def pytest_configure(config: pytest.Config) -> None:
    """F3-05: register the `property` marker for the hypothesis property suites.

    Home-of-record for pytest marker registration is normally
    `[tool.pytest.ini_options].markers` in pyproject.toml; that file is outside
    the test-author write surface for this leg, so the marker is registered here
    via the programmatic `addinivalue_line` equivalent (identical effect: it
    seeds the ini `markers` list, so `-m property` selects the suites and no
    PytestUnknownMarkWarning fires). See F3-05 property suites `test_prop_*.py`.
    """
    config.addinivalue_line(
        "markers",
        "property: hypothesis property suite asserting an INVARIANT, not an "
        "example (F3-05) — select with `-m property`.",
    )
    _register_hypothesis_profiles()


def _register_hypothesis_profiles() -> None:
    """TASK-111a: register the `deep` (default) and `gate` Hypothesis profiles.

    Two profiles, selected by the NEXUS_HYPOTHESIS_PROFILE env var:

      * `deep`  (default) — max_examples=100, i.e. Hypothesis's own default; the
        default deadline (200ms) is left untouched. Loading it changes NOTHING
        about how an on-demand / dev `pytest` run behaves versus not registering
        any profile at all, so the property suites keep their full search depth
        for interactive and CI-baseline runs.

      * `gate`  — max_examples=25, deadline=None. Activated ONLY by
        tools/build_snapshot.sh's release-gate pytest invocations (which export
        NEXUS_HYPOTHESIS_PROFILE=gate). It caps the per-property example budget so
        the gate lane runs fast WITHOUT weakening any assertion: every property is
        still exercised, the invariant checked is byte-identical, only the number
        of generated examples per property drops (25 vs 100) for the gate run. The
        `deadline=None` avoids flaky slow-example failures on shared CI hardware —
        speed comes from fewer examples, not from a per-example wall-clock cap.

    Property suites that pin their own `@settings(max_examples=...)` (e.g.
    test_prop_projection_idempotency / _replay_determinism, at 60) keep that
    explicit value in BOTH profiles — an explicit per-test `@settings` overrides
    the active profile by Hypothesis's own precedence rules. The gate cap
    therefore governs exactly the suites that DON'T pin a count (envelope /
    capability-token / parity-diff), which is where the example bulk lives.

    Defensive: guarded on the Hypothesis import and on an unknown profile name so
    this conftest (loaded for EVERY broker test session) can never break
    collection on a mis-set env var or a stripped-down interpreter.
    """
    try:
        from hypothesis import settings
    except ImportError:
        return
    settings.register_profile("deep", max_examples=100)
    settings.register_profile("gate", max_examples=25, deadline=None)
    profile = os.environ.get("NEXUS_HYPOTHESIS_PROFILE", "deep")
    try:
        settings.load_profile(profile)
    except Exception:
        settings.load_profile("deep")


def pytest_addoption(parser: pytest.Parser) -> None:
    """Register the gate's own escape hatch.

    Only the gate's self-test passes --allow-empty-collection; it exists so the
    test suite can assert BOTH directions (armed → errors on empty; disarmed →
    tolerates empty) without the gate aborting its own verification.
    """
    parser.addoption(
        "--allow-empty-collection",
        action="store_true",
        default=False,
        help=(
            "OPT-053 escape hatch: tolerate a 0-test collection instead of "
            "erroring. For the non-empty-collection gate's self-test only — "
            "never pass this in CI."
        ),
    )


def pytest_collection_finish(session: pytest.Session) -> None:
    """OPT-053: fail the session loudly when 0 tests were collected.

    Fires after the WHOLE collection (post -k/-m deselection), so an empty
    `session.items` means the session genuinely has nothing to run — the precise
    false-green condition we refuse to let exit 0.
    """
    if session.items:
        return  # the common case — never interfere with a populated run

    config = session.config
    # `--collect-only` is an explicit inspection, not a verification run; and the
    # gate's own self-test disarms via --allow-empty-collection.
    if config.getoption("--collect-only", default=False):
        return
    if config.getoption("--allow-empty-collection", default=False):
        return

    raise pytest.UsageError(
        "OPT-053 non-empty-collection gate: 0 tests were collected. A green exit "
        "here would be a FALSE PASS (the silent-zero trap). Check that "
        "testpaths/-k/-m actually select tests. To intentionally allow an empty "
        "run (gate self-test only), pass --allow-empty-collection."
    )


# ── TASK-111b: spawn a real hermetic daemon serving one project ──────────────
#
# Post-F2-03 the loud SessionStart banners ("INSTALL INCOMPLETE — log.py
# missing", "NEXUS MEMORY UNWRITABLE") no longer live in the hook body — the
# hook shrank to `exec _ping_shim.py <event> <consumer>` and the shout is
# computed daemon-resident in advisory_handlers.py. With NO daemon the shim
# fails OPEN (silent, exit 0) — so a "banner shouts" invariant is only
# FALSIFIABLE against a REACHABLE daemon. TASK-092 leg B (9882c47) papered over
# this by loosening the asserts to `banner OR empty`, making the shout
# unfalsifiable; TASK-111b reverts that and re-pins the loud leg here.
#
# The factory returns the env a hook subprocess merges to route its shim to
# THIS daemon (socket dir + repo root + a widened per-invocation shim timeout
# for the log.py-shelling handlers). Mirrors test_event_bus.py's own
# `_spawn_daemon_process`/`_wait_for_health_ping` and the hooks-suite
# `resident_daemon` fixture; kept lean + local so the two banner tests do not
# have to reach across suites for the machinery.
#
# 3.9-import-safe (this conftest is snapshotted to the package twin): no
# `datetime.UTC`, no def-time `X | None`, no `match`/`case`.
def _tb_socket_path(socket_dir, project_path):
    import hashlib
    from pathlib import Path

    digest = hashlib.sha256(str(Path(project_path).resolve()).encode("utf-8")).hexdigest()[:16]
    return Path(socket_dir) / (digest + ".sock")


def _tb_daemon_answers_health(socket_path):
    import contextlib
    import json
    import socket as _socket

    s = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
    s.settimeout(1.0)
    try:
        s.connect(str(socket_path))
        s.sendall((json.dumps({"id": 1, "method": "health", "params": {}}) + "\n").encode("utf-8"))
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
        return bool(buf)
    except OSError:
        return False
    finally:
        with contextlib.suppress(OSError):
            s.close()


@pytest.fixture()
def spawn_daemon_for_project():
    """Factory `(project_path) -> hook_env` that stands up a hermetic broker
    daemon serving `project_path` and returns the env dict a hook subprocess
    merges to route its ping shim to that daemon. Every daemon + temp dir is
    torn down at test end. See the block comment above for why the loud-banner
    tests need a reachable daemon."""
    import shutil
    import subprocess
    import tempfile
    import time
    from pathlib import Path

    broker_root = Path(__file__).resolve().parents[1]
    procs = []
    temp_dirs = []

    def factory(project_path):
        project_path = Path(project_path)
        (project_path / ".memory").mkdir(parents=True, exist_ok=True)
        # AF_UNIX paths cap ~104 bytes on macOS — keep the socket dir short.
        socket_dir = Path(tempfile.mkdtemp(prefix="tbd-s-", dir="/tmp"))
        daemon_tmp = Path(tempfile.mkdtemp(prefix="tbd-t-", dir="/tmp"))
        temp_dirs.extend([socket_dir, daemon_tmp])
        env = dict(os.environ)
        env["NEXUS_DAEMON_SOCKET_DIR"] = str(socket_dir)
        env["NEXUS_DISABLE_VEC"] = "1"
        env["TMPDIR"] = str(daemon_tmp)
        proc = subprocess.Popen(
            [sys.executable, "-m", "broker.daemon.server", "--project-path", str(project_path)],
            cwd=str(broker_root),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        procs.append(proc)
        socket_path = _tb_socket_path(socket_dir, project_path)
        deadline = time.time() + 10.0
        while time.time() < deadline:
            if proc.poll() is not None:
                err = proc.stderr.read() if proc.stderr else ""
                raise AssertionError(
                    "spawned daemon exited during health gate (rc="
                    + str(proc.returncode) + "): " + err
                )
            if socket_path.exists() and _tb_daemon_answers_health(socket_path):
                return {
                    "NEXUS_DAEMON_SOCKET_DIR": str(socket_dir),
                    "_HOOK_REPO_ROOT": str(project_path),
                    # log.py-shelling handlers exceed the 50ms shim default; widen
                    # per-invocation (test-scoped, never changes prod shim defaults).
                    "NEXUS_PING_SHIM_TIMEOUT_S": "15",
                }
            time.sleep(0.05)
        raise AssertionError(
            "spawned daemon never answered health within 10s (socket="
            + str(socket_path) + ")"
        )

    try:
        yield factory
    finally:
        for proc in procs:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    with contextlib.suppress(subprocess.TimeoutExpired):
                        proc.wait(timeout=2)
            for stream in (proc.stdout, proc.stderr):
                if stream is not None:
                    with contextlib.suppress(OSError):
                        stream.close()
        for d in temp_dirs:
            shutil.rmtree(d, ignore_errors=True)
