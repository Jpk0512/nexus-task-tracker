"""Tests for plan-13 N12 — the 3.4-thin daemon<->conductor ready-set
fast-path: an O(1) worker pull served by `broker.daemon.ready_set`, with
`broker.conductor.ready_set_client.ReadySetClient` falling back
transparently to a direct local scan whenever the daemon is unreachable
(fail-closed, no daemon-required invariant).

No `broker.conductor.dag` import anywhere in this suite — N12's write scope
excludes dag.py, so the fallback's direct-scan algorithm is verified as an
independent reimplementation, never by delegating to it.
"""
from __future__ import annotations

import asyncio
import shutil
import tempfile
import threading
from pathlib import Path

import pytest

from broker.conductor.ready_set_client import ReadySetClient
from broker.daemon.ready_set import (
    ReadySetRegistry,
    ReadySetStore,
    UnknownRun,
    handle_ready_set_request,
    ready_set_sock_path_for,
    serve_ready_set,
)


class _BackgroundReadySetServer:
    """Runs `serve_ready_set` on its own thread + event loop, so the test's
    synchronous (blocking-socket) `ReadySetClient` calls never contend with
    the server's asyncio loop for the same OS thread — mirroring the
    two-process relationship a real daemon has with its caller, without
    subprocess-spawn overhead."""

    def __init__(self, sock_path: Path) -> None:
        self.sock_path = sock_path
        self._loop = asyncio.new_event_loop()
        self._server: asyncio.Server | None = None
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        assert self._ready.wait(timeout=5.0), "ready-set test server never started"

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)

        async def _start() -> None:
            self._server = await serve_ready_set(self.sock_path)
            self._ready.set()

        self._loop.create_task(_start())
        self._loop.run_forever()

    def stop(self) -> None:
        async def _shutdown() -> None:
            if self._server is not None:
                self._server.close()
                await self._server.wait_closed()

        asyncio.run_coroutine_threadsafe(_shutdown(), self._loop).result(timeout=5.0)
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5.0)
        self.sock_path.unlink(missing_ok=True)


def _diamond_nodes() -> list[dict]:
    """root -> {A, B} (disjoint branches) -> merge."""
    return [
        {"node_id": "root", "depends_on": []},
        {"node_id": "A", "depends_on": ["root"]},
        {"node_id": "B", "depends_on": ["root"]},
        {"node_id": "merge", "depends_on": ["A", "B"]},
    ]


def _wide_nodes() -> list[dict]:
    """One root fanning out to three independent leaves — exercises more
    than one simultaneously-ready node in the queue."""
    return [
        {"node_id": "root", "depends_on": []},
        {"node_id": "leaf1", "depends_on": ["root"]},
        {"node_id": "leaf2", "depends_on": ["root"]},
        {"node_id": "leaf3", "depends_on": ["root"]},
    ]


@pytest.fixture()
def short_sock_dir():
    # AF_UNIX paths are capped at ~104 bytes on macOS/BSD — a short dir
    # directly under /tmp (never pytest's deeply nested tmp_path) is required
    # for bind()/connect() to succeed (same constraint test_daemon_pilot.py
    # documents for the primary daemon's socket).
    d = Path(tempfile.mkdtemp(prefix="nxrs", dir="/tmp"))
    yield d
    shutil.rmtree(d, ignore_errors=True)


def _drive(client: ReadySetClient, node_ids: set[str]) -> list[str]:
    """Single-worker driver loop: pull, no-op 'work', mark_complete, repeat
    until every node has been visited. Deterministic (single worker, FIFO
    queues on both the daemon and local-mirror sides) so two runs of the
    same fixture DAG are directly comparable for the 'identical results'
    acceptance criterion."""
    order: list[str] = []
    remaining = set(node_ids)
    stalls = 0
    while remaining:
        nid = client.pull()
        if nid is None:
            stalls += 1
            assert stalls < 1000, "ready-set driver stalled — no progress"
            continue
        stalls = 0
        order.append(nid)
        remaining.discard(nid)
        client.mark_complete(nid)
    return order


# ── ReadySetStore — pure in-memory logic, no socket ─────────────────────────


def test_initial_ready_set_is_exactly_the_zero_in_degree_nodes() -> None:
    store = ReadySetStore(_diamond_nodes())
    assert store.snapshot()["ready"] == ["root"]


def test_pull_is_fifo_and_empty_queue_returns_none() -> None:
    store = ReadySetStore(_wide_nodes())
    assert store.pull() == "root"
    assert store.pull() is None  # leaf1/2/3 still blocked on root


def test_mark_complete_only_unlocks_a_dependent_once_all_its_deps_are_done() -> None:
    store = ReadySetStore(_diamond_nodes())
    store.pull()  # root
    newly_ready = store.mark_complete("root")
    assert sorted(newly_ready) == ["A", "B"]

    store.pull()  # A
    assert store.mark_complete("A") == []  # merge still waits on B
    snap = store.snapshot()
    assert "merge" not in snap["ready"]


def test_full_diamond_resolves_with_valid_topological_order() -> None:
    store = ReadySetStore(_diamond_nodes())
    order: list[str] = []
    while True:
        nid = store.pull()
        if nid is None:
            break
        order.append(nid)
        store.mark_complete(nid)
    assert order[0] == "root"
    assert order[-1] == "merge"
    assert set(order) == {"root", "A", "B", "merge"}
    assert order.index("A") < order.index("merge")
    assert order.index("B") < order.index("merge")


# ── ReadySetRegistry + handle_ready_set_request — pure dispatch, no socket ──


def test_registry_get_unknown_run_raises() -> None:
    registry = ReadySetRegistry()
    with pytest.raises(UnknownRun):
        registry.get("no-such-run")


def test_registry_invalidate_drops_the_run() -> None:
    registry = ReadySetRegistry()
    registry.register("run-1", _diamond_nodes())
    assert registry.invalidate("run-1") is True
    with pytest.raises(UnknownRun):
        registry.get("run-1")
    assert registry.invalidate("run-1") is False  # already gone


def test_handle_ready_set_request_full_cycle() -> None:
    registry = ReadySetRegistry()
    reg_result = handle_ready_set_request(
        registry, "ready_set_register", {"run_id": "run-1", "nodes": _diamond_nodes()}
    )
    assert reg_result == {"registered": True, "node_count": 4}

    pulled = handle_ready_set_request(registry, "ready_set_pull", {"run_id": "run-1"})
    assert pulled == {"node_id": "root"}

    completed = handle_ready_set_request(
        registry, "ready_set_complete", {"run_id": "run-1", "node_id": "root"}
    )
    assert sorted(completed["newly_ready"]) == ["A", "B"]

    snap = handle_ready_set_request(registry, "ready_set_snapshot", {"run_id": "run-1"})
    assert snap["completed"] == ["root"]
    assert sorted(snap["ready"]) == ["A", "B"]

    invalidated = handle_ready_set_request(registry, "ready_set_invalidate", {"run_id": "run-1"})
    assert invalidated == {"invalidated": True}


def test_handle_ready_set_request_rejects_unknown_method() -> None:
    """The acceptance boundary: no new daemon capability beyond ready-set
    serve/invalidate is introduced."""
    registry = ReadySetRegistry()
    with pytest.raises(ValueError, match="unknown ready-set method"):
        handle_ready_set_request(registry, "some_other_capability", {})


def test_handle_ready_set_request_unregistered_run_surfaces_as_keyerror() -> None:
    registry = ReadySetRegistry()
    with pytest.raises(UnknownRun):
        handle_ready_set_request(registry, "ready_set_pull", {"run_id": "ghost"})


def test_ready_set_sock_path_differs_from_primary_daemon_socket(tmp_path) -> None:
    from broker.daemon import paths

    project = tmp_path / "proj"
    primary = paths.socket_path_for(project)
    readyset = ready_set_sock_path_for(project)
    assert readyset != primary
    assert readyset.parent == primary.parent


# ── live socket: fast-path pull with call accounting (acceptance #1) ───────


def test_fast_path_pull_serves_ready_nodes_with_call_accounting(short_sock_dir) -> None:
    sock_path = short_sock_dir / "rs.sock"
    bg = _BackgroundReadySetServer(sock_path)
    try:
        nodes = _diamond_nodes()
        client = ReadySetClient("run-fast", nodes, sock_path=sock_path)
        order = _drive(client, {n["node_id"] for n in nodes})

        assert order[0] == "root"
        assert order[-1] == "merge"
        assert client.fast_path_calls > 0
        assert client.fallback_calls == 0
        assert client.daemon_available is True
    finally:
        bg.stop()


# ── daemon killed mid-run: transparent fallback, identical results (acceptance #2) ──


def test_daemon_killed_mid_run_falls_back_with_identical_results(short_sock_dir) -> None:
    nodes = _wide_nodes()
    node_ids = {n["node_id"] for n in nodes}

    # Run 1: daemon up for the FIRST pull only, then killed mid-run.
    sock_path = short_sock_dir / "rs.sock"
    bg = _BackgroundReadySetServer(sock_path)
    client_mixed = ReadySetClient("run-mixed", nodes, sock_path=sock_path)

    first = client_mixed.pull()
    assert first == "root"
    client_mixed.mark_complete(first)
    assert client_mixed.fast_path_calls >= 1
    assert client_mixed.fallback_calls == 0

    bg.stop()  # simulates the daemon dying mid-run

    remaining_order: list[str] = []
    while True:
        nid = client_mixed.pull()
        if nid is None:
            break
        remaining_order.append(nid)
        client_mixed.mark_complete(nid)
    mixed_order = [first, *remaining_order]

    assert client_mixed.daemon_available is False
    assert client_mixed.fallback_calls > 0
    assert set(mixed_order) == node_ids

    # Run 2: no daemon at all — pure direct-scan from the first pull.
    client_direct = ReadySetClient("run-direct", nodes, sock_path=None)
    direct_order = _drive(client_direct, node_ids)
    assert client_direct.fast_path_calls == 0
    assert client_direct.fallback_calls > 0

    # The SAME fixture DAG, completed via two different paths, yields
    # IDENTICAL results — the acceptance criterion this test exists for.
    assert mixed_order == direct_order


def test_no_daemon_configured_uses_pure_fallback_and_still_completes() -> None:
    nodes = _diamond_nodes()
    client = ReadySetClient("run-none", nodes, sock_path=None)
    order = _drive(client, {n["node_id"] for n in nodes})
    assert order[0] == "root"
    assert order[-1] == "merge"
    assert client.fast_path_calls == 0
    assert client.fallback_calls == len(nodes)
    assert client.daemon_available is False


def test_unreachable_socket_path_falls_back_immediately(short_sock_dir) -> None:
    # A socket path with no listener at all (never served) — the "daemon was
    # never up" case, distinct from "daemon died mid-run".
    sock_path = short_sock_dir / "nobody-home.sock"
    nodes = _diamond_nodes()
    client = ReadySetClient("run-unreachable", nodes, sock_path=sock_path)
    assert client.daemon_available is False  # register() already failed closed
    order = _drive(client, {n["node_id"] for n in nodes})
    assert set(order) == {"root", "A", "B", "merge"}
    assert client.fast_path_calls == 0
