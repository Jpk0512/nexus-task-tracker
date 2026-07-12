"""Daemon pilot server body — Unix-domain-socket JSON-RPC, spawn-on-demand,
idle-shutdown, per-project namespaced (R4-T06, plans/13 N11).

Generalizes the already-deployed `broker/vault/http.py` lifecycle pattern
(health route, graceful signal handling, warm in-process state) from HTTP to
a hand-rolled asyncio Unix-domain-socket transport per plans/07 §2 Option C's
stdlib-only path — no new dependency added.

Wire format: newline-delimited JSON. Request `{"id", "method", "params"}`,
response `{"id", "result"}` or `{"id", "error"}`.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import os
import signal
import socket
import time
from pathlib import Path
from typing import Any

from broker.daemon import drift_watch, paths
from broker.daemon.ownership import OwnershipRegistry, handle_ownership_request
from broker.daemon.registry_query import query_registry
from broker.daemon.registry_scan import filter_registry, scan_registry
from broker.daemon.schema_scan import scan_schema
from broker.daemon.session_digest import SessionDigestCache
from broker.daemon.telemetry_store import TelemetryStore

LOG = logging.getLogger("nexus-daemon")


class _RegistryCache:
    """1.1 warm skills/agents cache — refreshed on file-mtime change or TTL."""

    def __init__(self, project_path: Path, ttl_s: float = 30.0) -> None:
        self.project_path = project_path
        self.ttl_s = ttl_s
        self._entries: list[dict[str, Any]] | None = None
        self._loaded_at = 0.0
        self._mtime_key: tuple[tuple[str, float], ...] | None = None

    def _current_mtime_key(self) -> tuple[tuple[str, float], ...]:
        stamps: list[tuple[str, float]] = []
        for base in (
            self.project_path / ".claude" / "agents",
            self.project_path / ".claude" / "skills",
        ):
            if base.is_dir():
                for p in sorted(base.rglob("*.md")):
                    with contextlib.suppress(OSError):
                        stamps.append((str(p), p.stat().st_mtime))
        return tuple(stamps)

    def get(self) -> list[dict[str, Any]]:
        now = time.monotonic()
        key = self._current_mtime_key()
        stale = (now - self._loaded_at) > self.ttl_s
        if self._entries is None or key != self._mtime_key or stale:
            self._entries = scan_registry(self.project_path)
            self._mtime_key = key
            self._loaded_at = now
        return self._entries


class _SchemaCache:
    """1.3 schema-snapshot cache — refreshed on project.db mtime change or TTL."""

    def __init__(self, db_path: Path, ttl_s: float = 30.0) -> None:
        self.db_path = db_path
        self.ttl_s = ttl_s
        self._shape: dict[str, list[str]] | None = None
        self._loaded_at = 0.0
        self._mtime: float | None = None

    def get(self) -> dict[str, list[str]]:
        now = time.monotonic()
        mtime = self.db_path.stat().st_mtime if self.db_path.is_file() else None
        stale = (now - self._loaded_at) > self.ttl_s
        if self._shape is None or mtime != self._mtime or stale:
            self._shape = scan_schema(self.db_path)
            self._mtime = mtime
            self._loaded_at = now
        return self._shape


class DaemonState:
    """All warm in-process state for one project's daemon instance."""

    def __init__(self, project_path: Path) -> None:
        self.project_path = project_path
        self.db_path = project_path / ".memory" / "project.db"
        self.registry_cache = _RegistryCache(project_path)
        self.schema_cache = _SchemaCache(self.db_path)
        # R5-T04 (N57) — the 2.9 session-digest substrate (`session_digest.py`,
        # built by N47 but never wired into this dispatch table until now) and
        # the FULL-scope registry query (`registry_query.py`, same gap). Both
        # were the daemon RPC methods `session_digest`/`registry_query_full`
        # fell through as "unknown method" on (feedback id=135) -- wiring them
        # here is what lets `get_session_digest()`/`registry_query_full()` in
        # `broker.jit.context_expansion` actually answer "daemon" instead of
        # always degrading to their direct-read fallback.
        self.session_digest_cache = SessionDigestCache(self.db_path)
        self.telemetry = TelemetryStore()
        self.ownership = OwnershipRegistry()
        self.started_at = time.monotonic()
        self.last_activity = time.monotonic()
        # 2.8 budget-summary counters — raw counts only, no invented estimates.
        self.registry_queries_served = 0
        self.registry_bytes_served = 0
        self.schema_queries_served = 0
        self.session_digest_queries_served = 0
        self.registry_query_full_queries_served = 0
        # 2.6 install-drift background loop (N31) — None on a non-meta-repo
        # tenant; see start_drift_watch(). Advisory only, never authoritative.
        self.drift_watcher: drift_watch.DriftWatcher | None = None
        self.drift_report: drift_watch.DriftReport | None = None

    def touch(self) -> None:
        self.last_activity = time.monotonic()


def handle_request(state: DaemonState, method: str, params: dict[str, Any]) -> Any:
    """Pure dispatch — no I/O beyond what each cache/store already does."""
    state.touch()
    if method == "health":
        return {
            "status": "ok",
            "pid": os.getpid(),
            "project_path": str(state.project_path),
            "uptime_s": time.monotonic() - state.started_at,
        }
    if method == "query_registry":
        entries = state.registry_cache.get()
        filtered = filter_registry(entries, params.get("query_context"))
        state.registry_queries_served += 1
        payload = {"entries": filtered}
        state.registry_bytes_served += len(json.dumps(payload))
        return payload
    if method == "schema_snapshot":
        state.schema_queries_served += 1
        return {"tables": state.schema_cache.get()}
    if method == "session_digest":
        state.session_digest_queries_served += 1
        return state.session_digest_cache.get()
    if method == "registry_query_full":
        entries = query_registry(state.project_path, params.get("query_context"))
        state.registry_query_full_queries_served += 1
        return {"entries": entries}
    if method == "record_telemetry":
        state.telemetry.record(params["table"], params["row"])
        return {"accepted": True, "pending": state.telemetry.pending_count()}
    if method == "flush_telemetry":
        return {"flushed": state.telemetry.flush(state.db_path)}
    if method == "budget_summary":
        return {
            "uptime_s": time.monotonic() - state.started_at,
            "registry_queries_served": state.registry_queries_served,
            "registry_bytes_served": state.registry_bytes_served,
            "schema_queries_served": state.schema_queries_served,
            "session_digest_queries_served": state.session_digest_queries_served,
            "registry_query_full_queries_served": state.registry_query_full_queries_served,
            "telemetry_rows_flushed": state.telemetry.rows_flushed,
            "telemetry_flush_count": state.telemetry.flush_count,
            "telemetry_pending": state.telemetry.pending_count(),
        }
    if method.startswith("ownership_"):
        return handle_ownership_request(state.ownership, method, params)
    raise ValueError(f"unknown method: {method!r}")


async def _client_loop(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter, state: DaemonState
) -> None:
    request: dict[str, Any] | None = None
    try:
        while True:
            line = await reader.readline()
            if not line:
                break
            try:
                request = json.loads(line)
                result = handle_request(state, request["method"], request.get("params") or {})
                response: dict[str, Any] = {"id": request.get("id"), "result": result}
            except Exception as exc:  # noqa: BLE001 — must always answer, never crash the server
                req_id = request.get("id") if isinstance(request, dict) else None
                response = {"id": req_id, "error": str(exc)}
            writer.write((json.dumps(response) + "\n").encode("utf-8"))
            await writer.drain()
    finally:
        with contextlib.suppress(OSError):
            writer.close()


async def _idle_watchdog(state: DaemonState, shutdown_event: asyncio.Event) -> None:
    """1.7 idle-shutdown: exit once no request has landed for IDLE_TIMEOUT_S."""
    while True:
        await asyncio.sleep(paths.IDLE_CHECK_INTERVAL_S)
        if time.monotonic() - state.last_activity > paths.IDLE_TIMEOUT_S:
            shutdown_event.set()
            return


async def _flush_loop(state: DaemonState) -> None:
    """1.5 periodic write-through flush, independent of any client call landing."""
    while True:
        await asyncio.sleep(paths.FLUSH_INTERVAL_S)
        with contextlib.suppress(Exception):  # noqa: BLE001 — must not kill the loop
            state.telemetry.flush(state.db_path)


async def _drift_watch_loop(state: DaemonState, interval_s: float) -> None:
    """2.6 periodic install-drift re-check under the daemon's own event loop.

    Mirrors `_flush_loop`'s posture: runs independent of any client call
    landing, and a failed check must never kill the loop or the daemon —
    drift-watch is advisory-only (see drift_watch.py's module docstring);
    `tools/build_snapshot.sh --check` remains the sole authoritative gate.
    """
    assert state.drift_watcher is not None
    while True:
        with contextlib.suppress(Exception):  # noqa: BLE001 — advisory only, must not kill the loop
            state.drift_report = state.drift_watcher.check()
        await asyncio.sleep(interval_s)


def start_drift_watch(state: DaemonState, *, interval_s: float | None = None) -> asyncio.Task[None] | None:
    """N31 — start the 2.6 install-drift background loop for this daemon's
    project, gated meta-repo-tenant-only. Returns None (no watcher, no task)
    on any tenant lacking `nexus-package/` + `tools/build_snapshot.sh` — an
    installed product tenant never pays for or sees drift-watch. Non-None
    return sets `state.drift_watcher` immediately, before the first check has
    even run, so a caller can always tell "watching" apart from "not a
    meta-repo tenant" without waiting on the loop's first tick.
    """
    if not drift_watch.is_meta_repo_tenant(state.project_path):
        return None
    state.drift_watcher = drift_watch.watch_repo(state.project_path, ttl_s=paths.DRIFT_WATCH_TTL_S)
    resolved_interval = interval_s if interval_s is not None else paths.DRIFT_WATCH_INTERVAL_S
    return asyncio.create_task(_drift_watch_loop(state, resolved_interval))


def _already_serving(sock_path: Path) -> bool:
    """Best-effort liveness probe so two racing spawns don't both try to bind."""
    if not sock_path.exists():
        return False
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(0.2)
    try:
        s.connect(str(sock_path))
        return True
    except OSError:
        return False
    finally:
        s.close()


async def serve(project_path: Path) -> None:
    sock_path = paths.socket_path_for(project_path)
    sock_path.parent.mkdir(parents=True, exist_ok=True)

    if _already_serving(sock_path):
        LOG.info("daemon already serving %s; exiting", project_path)
        return

    sock_path.unlink(missing_ok=True)  # 1.8 stale-socket self-heal (server side)

    state = DaemonState(project_path)
    shutdown_event = asyncio.Event()

    async def _handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await _client_loop(reader, writer, state)

    server = await asyncio.start_unix_server(_handle, path=str(sock_path))
    loop = asyncio.get_running_loop()
    with contextlib.suppress(NotImplementedError):
        loop.add_signal_handler(signal.SIGTERM, shutdown_event.set)

    idle_task = asyncio.create_task(_idle_watchdog(state, shutdown_event))
    flush_task = asyncio.create_task(_flush_loop(state))
    drift_task = start_drift_watch(state)  # 2.6 — None on a non-meta-repo tenant
    try:
        async with server:
            await shutdown_event.wait()
    finally:
        idle_task.cancel()
        flush_task.cancel()
        if drift_task is not None:
            drift_task.cancel()
        with contextlib.suppress(Exception):  # noqa: BLE001 — best-effort final flush
            state.telemetry.flush(state.db_path)
        with contextlib.suppress(OSError):
            sock_path.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="R4-T06 daemon pilot server")
    parser.add_argument("--project-path", required=True)
    args = parser.parse_args(argv)
    logging.basicConfig(level=os.environ.get("NEXUS_DAEMON_LOG_LEVEL", "WARNING"))
    asyncio.run(serve(Path(args.project_path).resolve()))


if __name__ == "__main__":
    main()
