"""In-memory DAG ready-set cache + thin serve/invalidate surface — plans/13
N12 (item 3.4-thin). Serves the conductor's ready-set as an OPTIONAL
fast-path: an O(1) worker pull backed by an in-memory FIFO queue, updated on
every node completion. The conductor (`broker.conductor.ready_set_client`)
falls back transparently to its own direct in-memory scan whenever this is
unreachable — there is no daemon-required invariant, the same posture
`broker.daemon.client`/`fallback.py` already establish for the registry and
schema-snapshot caches (plans/07 §1 constraint 2).

Thin means exactly: serve (register/pull/complete/snapshot) + invalidate.
No locks beyond a local thread-safety mutex, no event bus, no cross-project
view — one run_id, one in-memory store (plan 13 §2.A row 3.4).

Kept as an INDEPENDENT method-dispatch surface, not folded into
`broker.daemon.server.handle_request` — this node's write scope excludes
server.py. A future node may wire `handle_ready_set_request` into the
primary daemon's dispatch table without changing its shape here.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import threading
from collections import deque
from pathlib import Path
from typing import Any

from broker.daemon import paths


class UnknownRun(KeyError):
    """Raised by `ReadySetRegistry.get()` for a run_id that was never
    registered (or was already invalidated) — `handle_ready_set_request`
    turns this into a normal RPC error response, never a server crash."""


def _in_degree_and_dependents(
    nodes: dict[str, dict[str, Any]],
) -> tuple[dict[str, int], dict[str, list[str]]]:
    """Same computation `broker.conductor.dag` performs for its own
    in-process scheduler — reimplemented here (rather than imported) so the
    daemon layer carries no dependency on the conductor layer; the algorithm
    is a five-line graph fold, low drift risk."""
    in_degree = {nid: len(node.get("depends_on") or []) for nid, node in nodes.items()}
    dependents: dict[str, list[str]] = {nid: [] for nid in nodes}
    for nid, node in nodes.items():
        for dep in node.get("depends_on") or []:
            dependents[dep].append(nid)
    return in_degree, dependents


class ReadySetStore:
    """One DAG run's ready-set: an O(1) FIFO pull queue plus in-degree
    bookkeeping, unlocked on completion of every dependency."""

    def __init__(self, nodes: list[dict[str, Any]]) -> None:
        self.nodes: dict[str, dict[str, Any]] = {n["node_id"]: n for n in nodes}
        self._in_degree, self._dependents = _in_degree_and_dependents(self.nodes)
        self._ready: deque[str] = deque(nid for nid, deg in self._in_degree.items() if deg == 0)
        self._dispatched: set[str] = set()
        self._completed: set[str] = set()
        self._lock = threading.Lock()

    def pull(self) -> str | None:
        with self._lock:
            if not self._ready:
                return None
            nid = self._ready.popleft()
            self._dispatched.add(nid)
            return nid

    def mark_complete(self, node_id: str) -> list[str]:
        with self._lock:
            self._completed.add(node_id)
            newly_ready: list[str] = []
            for dep in self._dependents.get(node_id, []):
                self._in_degree[dep] -= 1
                if self._in_degree[dep] == 0:
                    self._ready.append(dep)
                    newly_ready.append(dep)
            return newly_ready

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "ready": list(self._ready),
                "dispatched": sorted(self._dispatched),
                "completed": sorted(self._completed),
                "remaining": len(self.nodes) - len(self._completed),
            }


class ReadySetRegistry:
    """Holds one `ReadySetStore` per run_id. `invalidate` is the thin
    slice's only teardown surface — it drops a run's ready-set entirely,
    nothing more (no cross-project aggregation, no pub-sub notification)."""

    def __init__(self) -> None:
        self._stores: dict[str, ReadySetStore] = {}
        self._lock = threading.Lock()

    def register(self, run_id: str, nodes: list[dict[str, Any]]) -> ReadySetStore:
        store = ReadySetStore(nodes)
        with self._lock:
            self._stores[run_id] = store
        return store

    def get(self, run_id: str) -> ReadySetStore:
        with self._lock:
            store = self._stores.get(run_id)
        if store is None:
            raise UnknownRun(run_id)
        return store

    def invalidate(self, run_id: str) -> bool:
        with self._lock:
            return self._stores.pop(run_id, None) is not None


_READY_SET_METHODS = frozenset(
    {
        "ready_set_register",
        "ready_set_pull",
        "ready_set_complete",
        "ready_set_snapshot",
        "ready_set_invalidate",
    }
)


def handle_ready_set_request(registry: ReadySetRegistry, method: str, params: dict[str, Any]) -> Any:
    """Pure dispatch — same shape as `broker.daemon.server.handle_request`.
    Exactly the 5 serve/invalidate methods above; anything else is rejected
    — the acceptance boundary that no new daemon capability beyond
    ready-set serve/invalidate is introduced."""
    if method not in _READY_SET_METHODS:
        raise ValueError(f"unknown ready-set method: {method!r}")
    if method == "ready_set_register":
        store = registry.register(params["run_id"], params["nodes"])
        return {"registered": True, "node_count": len(store.nodes)}
    if method == "ready_set_pull":
        return {"node_id": registry.get(params["run_id"]).pull()}
    if method == "ready_set_complete":
        newly_ready = registry.get(params["run_id"]).mark_complete(params["node_id"])
        return {"newly_ready": newly_ready}
    if method == "ready_set_snapshot":
        return registry.get(params["run_id"]).snapshot()
    return {"invalidated": registry.invalidate(params["run_id"])}


async def _ready_set_client_loop(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter, registry: ReadySetRegistry
) -> None:
    """Newline-delimited JSON request/response loop — the same wire format
    `broker.daemon.server` uses, reimplemented here (its `_client_loop` is
    hardwired to `server.handle_request`/`DaemonState`, not reusable as-is)."""
    request: dict[str, Any] | None = None
    try:
        while True:
            line = await reader.readline()
            if not line:
                break
            try:
                request = json.loads(line)
                result = handle_ready_set_request(registry, request["method"], request.get("params") or {})
                response: dict[str, Any] = {"id": request.get("id"), "result": result}
            except Exception as exc:  # noqa: BLE001 — must always answer, never crash the server
                req_id = request.get("id") if isinstance(request, dict) else None
                response = {"id": req_id, "error": str(exc)}
            writer.write((json.dumps(response) + "\n").encode("utf-8"))
            await writer.drain()
    finally:
        with contextlib.suppress(OSError):
            writer.close()


async def serve_ready_set(sock_path: Path, registry: ReadySetRegistry | None = None) -> asyncio.Server:
    """Start the thin ready-set Unix-socket server; returns the live
    `asyncio.Server`. The caller owns its lifecycle (`server.close()` +
    `await server.wait_closed()`, then unlink the socket file) — deliberately
    NOT a long-running process with its own idle-shutdown watchdog (that is
    N11's `broker.daemon.server`, out of this node's write scope); a test or
    a future integration drives the lifecycle directly."""
    sock_path.parent.mkdir(parents=True, exist_ok=True)
    sock_path.unlink(missing_ok=True)
    reg = registry if registry is not None else ReadySetRegistry()

    async def _handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await _ready_set_client_loop(reader, writer, reg)

    return await asyncio.start_unix_server(_handle, path=str(sock_path))


def ready_set_sock_path_for(project_path: Path) -> Path:
    """Sibling socket path to the primary daemon's (N11 `paths.socket_path_for`)
    for the same project, namespaced with a `-readyset` suffix so the two
    Unix sockets never collide — without editing `paths.py` (out of this
    node's write scope)."""
    base = paths.socket_path_for(project_path)
    return base.with_name(f"{base.stem}-readyset{base.suffix}")
