"""Conductor-side client for the daemon's N12 ready-set fast-path (plans/13
§3 item 3). An OPTIONAL O(1) pull against `broker.daemon.ready_set`'s
Unix-socket server, with an always-consistent local mirror so a mid-run
daemon death costs nothing but the fast-path's latency win — never a wrong
answer, never a stall (plans/07 §1 constraint 2: no daemon-required
invariant). This is the client half of the same fail-closed posture
`broker.daemon.client`/`fallback.py` already establish for the registry and
schema-snapshot caches; kept independent here because this node's write
scope does not include `broker/daemon/client.py`.
"""
from __future__ import annotations

import json
import socket
from pathlib import Path
from typing import Any


class ReadySetUnavailable(Exception):
    """Internal-only: raised when the ready-set daemon can't be reached.
    Never escapes `ReadySetClient` — every public method catches it and
    falls back to the local direct scan automatically."""


def _rpc(sock_path: Path, method: str, params: dict[str, Any], timeout: float) -> Any:
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(str(sock_path))
        sock.sendall((json.dumps({"id": 1, "method": method, "params": params}) + "\n").encode("utf-8"))
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = sock.recv(65536)
            if not chunk:
                break
            buf += chunk
        if not buf:
            raise ReadySetUnavailable(f"ready-set daemon closed connection with no response ({method})")
        response = json.loads(buf.decode("utf-8"))
        if "error" in response:
            raise ReadySetUnavailable(f"ready-set daemon error: {response['error']}")
        return response["result"]
    except OSError as exc:
        raise ReadySetUnavailable(str(exc)) from exc
    finally:
        sock.close()


def _in_degree_and_dependents(
    nodes: dict[str, dict[str, Any]],
) -> tuple[dict[str, int], dict[str, list[str]]]:
    """The identical direct-scan computation `broker.conductor.dag.run_dag`
    already performs in-process — reimplemented here (dag.py is out of this
    node's write scope) so the fallback path is provably the same algorithm
    a daemon-absent conductor already runs today, not a second competing one."""
    in_degree = {nid: len(node.get("depends_on") or []) for nid, node in nodes.items()}
    dependents: dict[str, list[str]] = {nid: [] for nid in nodes}
    for nid, node in nodes.items():
        for dep in node.get("depends_on") or []:
            dependents[dep].append(nid)
    return in_degree, dependents


class ReadySetClient:
    """One conductor run's view onto the ready-set fast-path.

    `sock_path=None` means "no daemon configured" — behaves identically to a
    daemon that was unreachable from the first call (pure direct-scan mode).
    `fast_path_calls`/`fallback_calls` are the call-accounting counters the
    N12 acceptance criteria are verified against.
    """

    def __init__(
        self,
        run_id: str,
        nodes: list[dict[str, Any]],
        *,
        sock_path: Path | None,
        timeout: float = 1.0,
    ) -> None:
        self.run_id = run_id
        self.sock_path = sock_path
        self.timeout = timeout
        self.fast_path_calls = 0
        self.fallback_calls = 0
        self.daemon_available = sock_path is not None

        self._local_in_degree, self._local_dependents = _in_degree_and_dependents(
            {n["node_id"]: n for n in nodes}
        )
        self._local_ready: list[str] = [
            nid for nid, deg in self._local_in_degree.items() if deg == 0
        ]

        if self.daemon_available:
            try:
                _rpc(
                    self.sock_path,
                    "ready_set_register",
                    {"run_id": run_id, "nodes": nodes},
                    self.timeout,
                )
            except ReadySetUnavailable:
                self.daemon_available = False

    def pull(self) -> str | None:
        """O(1) daemon pull when available; a direct local-list pop
        otherwise — same FIFO order, so a mid-run fallback never changes the
        sequence of decisions the conductor would have made anyway."""
        if self.daemon_available:
            try:
                result = _rpc(
                    self.sock_path, "ready_set_pull", {"run_id": self.run_id}, self.timeout
                )
                self.fast_path_calls += 1
                nid = result["node_id"]
                # Keep the local mirror's ready list in sync with what the
                # daemon just dispatched — otherwise a node served via the
                # fast path lingers in `_local_ready` and gets handed out a
                # SECOND time once a later pull() falls back to it.
                if nid is not None and nid in self._local_ready:
                    self._local_ready.remove(nid)
                return nid
            except ReadySetUnavailable:
                self.daemon_available = False  # sticky: never retries the daemon within this run

        self.fallback_calls += 1
        if not self._local_ready:
            return None
        return self._local_ready.pop(0)

    def mark_complete(self, node_id: str) -> None:
        """Always updates the local mirror (so a later fallback needs no
        resync), and best-effort mirrors the completion to the daemon."""
        for dep in self._local_dependents.get(node_id, []):
            self._local_in_degree[dep] -= 1
            if self._local_in_degree[dep] == 0:
                self._local_ready.append(dep)

        if self.daemon_available:
            try:
                _rpc(
                    self.sock_path,
                    "ready_set_complete",
                    {"run_id": self.run_id, "node_id": node_id},
                    self.timeout,
                )
            except ReadySetUnavailable:
                self.daemon_available = False

    def invalidate(self) -> None:
        """Drop this run's daemon-side ready-set (the thin slice's only
        teardown surface). A no-op on the local mirror — invalidate ends
        the run."""
        if self.daemon_available:
            try:
                _rpc(self.sock_path, "ready_set_invalidate", {"run_id": self.run_id}, self.timeout)
            except ReadySetUnavailable:
                self.daemon_available = False
