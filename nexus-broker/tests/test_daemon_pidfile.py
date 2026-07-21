"""TASK-105 — single-instance pidfile lock, zombie reap, owner-checked socket
unlink, source-version digest, and the ensure decision logic.

Unit-level throughout: no real daemon is spawned — pids come from throwaway
subprocesses (sleep / true), sockets are plain files under tmp_path.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
from pathlib import Path

import pytest

from broker.daemon import ensure as ensure_mod
from broker.daemon import paths, pidfile
from broker.daemon.client import DaemonUnavailable


@pytest.fixture()
def sock_dir(monkeypatch):
    d = Path(tempfile.mkdtemp(prefix="nxpf", dir="/tmp"))
    monkeypatch.setenv("NEXUS_DAEMON_SOCKET_DIR", str(d))
    yield d
    import shutil

    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture()
def project(tmp_path) -> Path:
    p = tmp_path / "proj"
    p.mkdir()
    return p


@pytest.fixture()
def live_child():
    proc = subprocess.Popen(["sleep", "60"])
    yield proc
    if proc.poll() is None:
        proc.kill()
    proc.wait(timeout=5)


def _dead_pid() -> int:
    proc = subprocess.Popen(["true"])
    proc.wait(timeout=5)
    return proc.pid


# ── pidfile path derivation ─────────────────────────────────────────────────


def test_pidfile_keyed_by_same_digest_as_socket(project, sock_dir) -> None:
    sock = paths.socket_path_for(project)
    pf = pidfile.pidfile_path_for(project)
    assert pf.parent == sock.parent
    assert pf.stem == sock.stem
    assert pf.suffix == ".pid"


# ── single-instance lock ────────────────────────────────────────────────────


def test_second_acquire_fails_while_first_holds(project, sock_dir) -> None:
    lock1 = pidfile.PidfileLock(project)
    lock2 = pidfile.PidfileLock(project)
    assert lock1.acquire() is True
    assert lock2.acquire() is False
    lock1.release()
    lock3 = pidfile.PidfileLock(project)
    assert lock3.acquire() is True
    lock3.release()


def test_write_owner_roundtrip(project, sock_dir) -> None:
    lock = pidfile.PidfileLock(project)
    assert lock.acquire()
    sock = paths.socket_path_for(project)
    lock.write_owner(pid=os.getpid(), socket_path=sock, version="v123")
    data = pidfile.read_pidfile(lock.path)
    assert data is not None
    assert data["pid"] == os.getpid()
    assert data["socket"] == str(sock)
    assert data["version"] == "v123"
    lock.release()


def test_write_owner_requires_acquired_lock(project, sock_dir) -> None:
    lock = pidfile.PidfileLock(project)
    with pytest.raises(RuntimeError):
        lock.write_owner(pid=1, socket_path=Path("/tmp/x"), version="v")


def test_holder_pid_sees_live_holder_and_clears_on_release(project, sock_dir) -> None:
    pf = pidfile.pidfile_path_for(project)
    assert pidfile.holder_pid(pf) is None  # no file at all

    lock = pidfile.PidfileLock(project)
    assert lock.acquire()
    lock.write_owner(pid=os.getpid(), socket_path=paths.socket_path_for(project), version="v")
    assert pidfile.holder_pid(pf) == os.getpid()

    lock.release()
    assert pidfile.holder_pid(pf) is None  # file remains, lock does not


def test_holder_pid_none_for_unlocked_stale_content(project, sock_dir) -> None:
    pf = pidfile.pidfile_path_for(project)
    pf.parent.mkdir(parents=True, exist_ok=True)
    pf.write_text(json.dumps({"pid": _dead_pid()}), encoding="utf-8")
    assert pidfile.holder_pid(pf) is None


# ── zombie reap ─────────────────────────────────────────────────────────────


def test_reap_terminates_a_live_process(live_child) -> None:
    outcome = pidfile.reap(live_child.pid, term_wait_s=5.0, kill_wait_s=2.0)
    assert outcome == "terminated"
    assert not pidfile.pid_alive(live_child.pid)


def test_reap_escalates_to_kill_for_term_immune_process(tmp_path) -> None:
    ready = tmp_path / "ready"
    proc = subprocess.Popen(["bash", "-c", f'trap "" TERM; touch {ready}; sleep 60 & wait'])
    try:
        deadline = time.monotonic() + 5.0
        while not ready.exists():
            assert time.monotonic() < deadline, "trap-immune fixture never became ready"
            time.sleep(0.02)
        outcome = pidfile.reap(proc.pid, term_wait_s=0.5, kill_wait_s=5.0)
        assert outcome == "killed"
        assert not pidfile.pid_alive(proc.pid)
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=5)


def test_reap_already_dead() -> None:
    assert pidfile.reap(_dead_pid()) == "already-dead"


# ── owner-checked socket unlink ─────────────────────────────────────────────


def test_unlink_allowed_when_no_pidfile(tmp_path) -> None:
    sock = tmp_path / "a.sock"
    sock.touch()
    assert pidfile.owner_checked_unlink(sock, tmp_path / "a.pid") is True
    assert not sock.exists()


def test_unlink_allowed_when_owner_is_self(tmp_path) -> None:
    sock = tmp_path / "a.sock"
    sock.touch()
    pf = tmp_path / "a.pid"
    pf.write_text(json.dumps({"pid": os.getpid()}), encoding="utf-8")
    assert pidfile.owner_checked_unlink(sock, pf) is True
    assert not sock.exists()


def test_unlink_allowed_when_owner_is_dead(tmp_path) -> None:
    sock = tmp_path / "a.sock"
    sock.touch()
    pf = tmp_path / "a.pid"
    pf.write_text(json.dumps({"pid": _dead_pid()}), encoding="utf-8")
    assert pidfile.owner_checked_unlink(sock, pf) is True
    assert not sock.exists()


def test_unlink_refused_when_owner_is_live_other_process(tmp_path, live_child) -> None:
    """Incident hole 3: a TERMed zombie's cleanup must never unlink the LIVE
    daemon's socket."""
    sock = tmp_path / "a.sock"
    sock.touch()
    pf = tmp_path / "a.pid"
    pf.write_text(json.dumps({"pid": live_child.pid}), encoding="utf-8")
    assert pidfile.owner_checked_unlink(sock, pf) is False
    assert sock.exists()


# ── source-version digest ───────────────────────────────────────────────────


def test_source_version_changes_when_source_changes(tmp_path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.py").write_text("X = 1\n", encoding="utf-8")
    (src / "b.py").write_text("Y = 2\n", encoding="utf-8")
    v1 = pidfile.source_version(src)
    assert v1 == pidfile.source_version(src)
    (src / "a.py").write_text("X = 999\n", encoding="utf-8")
    assert pidfile.source_version(src) != v1


def test_source_version_of_real_daemon_package_is_stable() -> None:
    assert pidfile.source_version() == pidfile.source_version()
    assert len(pidfile.source_version()) == 16


# ── ensure decision logic ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("ping_ok", "resident", "current", "holder", "expected"),
    [
        (True, "v1", "v1", None, ensure_mod.ACTION_OK),
        (True, "v1", "v1", 42, ensure_mod.ACTION_OK),
        (True, "v0", "v1", 42, ensure_mod.ACTION_RESTART_STALE),
        (True, None, "v1", None, ensure_mod.ACTION_RESTART_STALE),
        (False, None, "v1", 42, ensure_mod.ACTION_REAP_THEN_SPAWN),
        (False, None, "v1", None, ensure_mod.ACTION_SPAWN),
    ],
)
def test_decide_action(ping_ok, resident, current, holder, expected) -> None:
    assert ensure_mod.decide_action(ping_ok, resident, current, holder) == expected


# ── ensure flow (client + pidfile monkeypatched — no real daemon) ───────────


def _fake_call_factory(responses):
    """responses: list of dicts to return in order, or DaemonUnavailable
    instances to raise. Records (method, spawn_if_missing) per call."""
    calls = []

    def fake_call(
        project_path,
        method,
        params=None,
        *,
        connect_timeout=None,
        spawn_if_missing=True,
        spawn_wait_s=None,
    ):
        calls.append((method, spawn_if_missing))
        item = responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return dict(item)

    return fake_call, calls


def test_ensure_healthy_current_daemon_is_a_noop(project, sock_dir, monkeypatch) -> None:
    fake_call, calls = _fake_call_factory([{"status": "ok", "pid": 111, "resident_version": "cur"}])
    monkeypatch.setattr(ensure_mod, "call", fake_call)
    monkeypatch.setattr(ensure_mod.pidfile, "source_version", lambda src_dir=None: "cur")
    reaped = []
    monkeypatch.setattr(
        ensure_mod.pidfile, "reap", lambda pid, **kw: reaped.append(pid) or "terminated"
    )
    result = ensure_mod.ensure(project)
    assert result["ensure_action"] == "ok"
    assert reaped == []
    assert calls == [("health", False)]


def test_ensure_gracefully_restarts_stale_resident(project, sock_dir, monkeypatch) -> None:
    """The auto-refresh path: daemon answers, but its resident_version no
    longer matches the on-disk source — exactly the failure class that forced
    the app restart in the incident."""
    fake_call, calls = _fake_call_factory(
        [
            {"status": "ok", "pid": 111, "resident_version": "old"},
            {"status": "ok", "pid": 222, "resident_version": "new"},
        ]
    )
    monkeypatch.setattr(ensure_mod, "call", fake_call)
    monkeypatch.setattr(ensure_mod.pidfile, "source_version", lambda src_dir=None: "new")
    monkeypatch.setattr(ensure_mod.pidfile, "holder_pid", lambda p: 111)
    reaped = []
    monkeypatch.setattr(
        ensure_mod.pidfile, "reap", lambda pid, **kw: reaped.append(pid) or "terminated"
    )
    unlinked = []
    monkeypatch.setattr(
        ensure_mod.pidfile, "owner_checked_unlink", lambda s, p: unlinked.append(s) or True
    )
    result = ensure_mod.ensure(project)
    assert reaped == [111]
    assert unlinked, "stale restart must clear the old socket before respawn"
    assert result["pid"] == 222
    assert result["ensure_action"] == "restart-stale"
    assert calls == [("health", False), ("health", True)]


def test_ensure_reaps_socketless_zombie_before_respawn(project, sock_dir, monkeypatch) -> None:
    """Incident hole 2: ping fails but the pidfile lock is held by a live pid
    — ensure must reap it, never spawn a duplicate beside it."""
    fake_call, calls = _fake_call_factory(
        [
            DaemonUnavailable("no socket"),
            {"status": "ok", "pid": 444, "resident_version": "new"},
        ]
    )
    monkeypatch.setattr(ensure_mod, "call", fake_call)
    monkeypatch.setattr(ensure_mod.pidfile, "source_version", lambda src_dir=None: "new")
    monkeypatch.setattr(ensure_mod.pidfile, "holder_pid", lambda p: 333)
    reaped = []
    monkeypatch.setattr(
        ensure_mod.pidfile, "reap", lambda pid, **kw: reaped.append(pid) or "killed"
    )
    monkeypatch.setattr(ensure_mod.pidfile, "owner_checked_unlink", lambda s, p: True)
    result = ensure_mod.ensure(project)
    assert reaped == [333]
    assert result["pid"] == 444
    assert result["ensure_action"] == "reap-then-spawn"


def test_ensure_plain_spawn_when_nothing_is_running(project, sock_dir, monkeypatch) -> None:
    fake_call, calls = _fake_call_factory(
        [
            DaemonUnavailable("no socket"),
            {"status": "ok", "pid": 555, "resident_version": "new"},
        ]
    )
    monkeypatch.setattr(ensure_mod, "call", fake_call)
    monkeypatch.setattr(ensure_mod.pidfile, "source_version", lambda src_dir=None: "new")
    monkeypatch.setattr(ensure_mod.pidfile, "holder_pid", lambda p: None)
    reaped = []
    monkeypatch.setattr(
        ensure_mod.pidfile, "reap", lambda pid, **kw: reaped.append(pid) or "terminated"
    )
    result = ensure_mod.ensure(project)
    assert reaped == []
    assert result["ensure_action"] == "spawn"
    assert calls == [("health", False), ("health", True)]
