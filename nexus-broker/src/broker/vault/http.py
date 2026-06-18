"""nexus-vault HTTP MCP daemon — Streamable HTTP, bearer-authed, READ-ONLY.

Usage:
    python -m broker.vault.http

Binds 127.0.0.1:8848 (never 0.0.0.0). Tailscale serve fronts it with TLS at
the tailnet edge. Streamable HTTP transport mounted at /mcp.

Per plan §7.1 + §7.4 + Plexus Phase-5b decision (elevated bearer DENIED):

  - access_mode = "web_default" (privacy fence rejects fenced-domain reads).
  - register_writes = False     (write tools intentionally absent on the web
                                 surface — this is a documented deviation from
                                 plan §7.2's 10-tool count; web surface = read
                                 tools only).
  - Bearer auth: presented header `Authorization: Bearer <token>` is
    compared constant-time against the file
        ~/.config/nexus-vault/bearers.d/default.token
    Missing-file or empty-bearer => 401.
  - /health endpoint reports {status, build, bearer_loaded}.
  - Every MCP call logs one JSON line to research/_meta/vault-access.log:
        {ts, transport, auth, tool, decision}
    (tool/decision come from the X-Nexus-Tool / X-Nexus-Decision headers the
    privacy-fence layer sets; missing → "unknown".)

SIGHUP: reload bearer from disk without restarting (used by
bin/vault-rotate-bearer.py after a rotation).
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
import signal
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import uvicorn
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from broker.vault import policy as policy_mod
from broker.vault._server import build_app, build_config

LOG = logging.getLogger("nexus-vault.http")

DEFAULT_BIND_HOST = "127.0.0.1"
DEFAULT_BIND_PORT = 8848
DEFAULT_BEARER_PATH = Path(
    os.environ.get(
        "NEXUS_VAULT_BEARER_DEFAULT",
        str(Path.home() / ".config" / "nexus-vault" / "bearers.d" / "default.token"),
    )
).expanduser()


def _build_sha() -> str:
    sha = os.environ.get("NEXUS_VAULT_BUILD_SHA")
    if sha:
        return sha
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=Path(__file__).resolve().parent,
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        if out.returncode == 0:
            return out.stdout.strip() or "unknown"
    except Exception:  # noqa: BLE001 — startup must never crash on a git probe
        pass
    return "unknown"


def _load_bearer(path: Path) -> str | None:
    try:
        if not path.is_file():
            return None
        token = path.read_text().strip()
        return token or None
    except Exception:  # noqa: BLE001
        return None


def _access_log_path(vault_root: Path) -> Path:
    return vault_root / "_meta" / "vault-access.log"


def _append_access_log(vault_root: Path, entry: dict[str, Any]) -> None:
    log_path = _access_log_path(vault_root)
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, separators=(",", ":")) + "\n")
    except Exception as exc:  # noqa: BLE001
        LOG.warning("access log write failed: %s", exc)


class BearerAuthState:
    """Holds the in-process bearer; SIGHUP reloads it from disk."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.bearer: str | None = _load_bearer(path)

    def reload(self) -> None:
        self.bearer = _load_bearer(self.path)
        LOG.info("bearer reloaded; loaded=%s", self.bearer is not None)

    def loaded(self) -> bool:
        return bool(self.bearer)


class BearerAuthMiddleware(BaseHTTPMiddleware):
    """Constant-time bearer check on /mcp routes.

    /health is the only path that bypasses bearer enforcement so that a
    healthcheck (Tailscale, launchd, ops curl) doesn't need credentials.
    """

    def __init__(self, app, state: BearerAuthState, vault_root: Path) -> None:
        super().__init__(app)
        self.state = state
        self.vault_root = vault_root

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        path = request.url.path
        if path == "/health" or path.startswith("/health/"):
            return await call_next(request)

        # Bearer requirement applies to ALL non-health routes (esp /mcp).
        presented = self._extract_bearer(request)
        if not policy_mod.bearer_matches(presented, self.state.bearer):
            _append_access_log(
                self.vault_root,
                {
                    "ts": datetime.now(UTC).isoformat(),
                    "transport": "http",
                    "auth": "default",
                    "tool": "<auth>",
                    "decision": "deny",
                },
            )
            return JSONResponse(
                {"error": "unauthorized", "detail": "bearer required"},
                status_code=401,
                headers={"WWW-Authenticate": 'Bearer realm="nexus-vault"'},
            )

        # VAULT-6: FastMCP/Starlette does not set X-Nexus-Tool on responses, so
        # we extract the JSON-RPC method from the request body instead. This is
        # the only reliable source of the actual MCP tool name on the HTTP path.
        tool_name = await self._extract_mcp_method(request)
        response = await call_next(request)
        decision = response.headers.get("X-Nexus-Decision", "allow")
        _append_access_log(
            self.vault_root,
            {
                "ts": datetime.now(UTC).isoformat(),
                "transport": "http",
                "auth": "default",
                "tool": tool_name,
                "decision": decision,
            },
        )
        return response

    @staticmethod
    async def _extract_mcp_method(request: Request) -> str:
        """Extract the JSON-RPC method from the request body for access logging.

        Falls back to '<mcp>' if the body is absent, not JSON, or has no method.
        Uses request.body() which Starlette caches so call_next still reads it.
        """
        try:
            body_bytes = await request.body()
            if not body_bytes:
                return "<mcp>"
            payload = json.loads(body_bytes)
            method = payload.get("method", "<mcp>")
            return str(method) if method else "<mcp>"
        except Exception:  # noqa: BLE001
            return "<mcp>"

    @staticmethod
    def _extract_bearer(request: Request) -> str | None:
        auth = request.headers.get("authorization") or request.headers.get("Authorization")
        if not auth:
            return None
        parts = auth.split(None, 1)
        if len(parts) != 2 or parts[0].lower() != "bearer":
            return None
        return parts[1].strip() or None


def build_http_app(
    *,
    bearer_path: Path | None = None,
    vault_root: Path | None = None,
    db_path: Path | None = None,
):
    """Construct the bearer-authed Starlette ASGI app for the read-only HTTP MCP.

    Returns a Starlette app ready for uvicorn.run().
    """
    config = build_config(
        access_mode="web_default",
        vault_root=vault_root,
        db_path=db_path,
    )
    mcp = build_app(config, register_writes=False)

    state = BearerAuthState(bearer_path or DEFAULT_BEARER_PATH)

    @mcp.custom_route("/health", methods=["GET"])
    async def health(_request: Request) -> Response:
        return JSONResponse(
            {
                "status": "ok",
                "build": _build_sha(),
                "bearer_loaded": state.loaded(),
            }
        )

    # FastMCP builds a Starlette app with /mcp mounted; we add the bearer
    # middleware in front of it. /health bypasses bearer (see middleware).
    app = mcp.http_app(path="/mcp", transport="http")
    app.add_middleware(
        BearerAuthMiddleware, state=state, vault_root=config.vault_root
    )

    # SIGHUP → reload bearer file from disk (used after vault-rotate-bearer.py).
    def _sighup_handler(_signum, _frame):  # noqa: ANN001
        state.reload()

    # Signal install may fail in some embedded contexts (e.g. tests).
    with contextlib.suppress(ValueError, OSError):
        signal.signal(signal.SIGHUP, _sighup_handler)

    return app


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("NEXUS_VAULT_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    host = os.environ.get("NEXUS_VAULT_HTTP_HOST", DEFAULT_BIND_HOST)
    port = int(os.environ.get("NEXUS_VAULT_HTTP_PORT", str(DEFAULT_BIND_PORT)))
    if host not in ("127.0.0.1", "localhost", "::1"):
        # Hard refusal — plan §7.4 + Phase-5b constraint: NEVER bind to 0.0.0.0.
        raise SystemExit(
            f"refusing to bind non-loopback host {host!r}; Tailscale serve "
            "must front this daemon, not direct exposure"
        )

    app = build_http_app()
    LOG.info("nexus-vault HTTP daemon listening on http://%s:%d/mcp", host, port)
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
