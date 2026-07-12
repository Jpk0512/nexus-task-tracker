"""Regression tests for SessionStart health-hook output shape (GAP-10 / ROUTER-04 / ROUTER-07).

Both router-health-check.sh and health-banner.sh, when they emit, MUST produce the
nested harness contract:

    {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "<text>"}}

NOT a flat {"hookSpecificOutput": "<string>"} form. The emission is built with
json.dumps, so an adversarial body containing quotes / backslashes / triple-quotes
must NOT corrupt the JSON or crash the hook (the old triple-quoted-literal +
2>/dev/null path faked a clean check on such input).

Run from nexus-package/:
    uv run pytest .claude/hooks/tests/test_health_hook_output.py -v
"""

from __future__ import annotations

import json
import shutil
import socket
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parent.parent
ROUTER_HEALTH = HOOKS_DIR / "router-health-check.sh"
HEALTH_BANNER = HOOKS_DIR / "health-banner.sh"

# A body that breaks naive shell-string interpolation AND triple-quoted python
# literals: embedded double/single quotes, a backslash, and a triple-quote run.
ADVERSARIAL = r'''evil " ' \ """ '''


def _assert_session_start_emission(stdout: str) -> str:
    """Parse stdout, assert the nested SessionStart contract, return additionalContext."""
    assert stdout.strip(), "hook emitted nothing"
    obj = json.loads(stdout)  # raises if the emission is not valid JSON
    hso = obj["hookSpecificOutput"]
    assert isinstance(hso, dict), f"hookSpecificOutput must be a nested object, got {type(hso)}: {hso!r}"
    assert hso["hookEventName"] == "SessionStart", hso
    ctx = hso["additionalContext"]
    assert isinstance(ctx, str) and ctx.strip(), f"additionalContext empty/non-str: {ctx!r}"
    return ctx


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _dead_chat_url() -> str:
    """A chat-completions URL on a closed port (so /v1/models is unreachable)."""
    return f"http://127.0.0.1:{_free_port()}/v1/chat/completions"


class _ModelsServer:
    """Tiny HTTP server returning a /v1/models body that omits the required model.

    The body string is caller-supplied so a test can inject an adversarial model id.
    """

    def __init__(self, model_id_in_body: str) -> None:
        body = json.dumps({"data": [{"id": model_id_in_body}]}).encode()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_a):  # silence
                pass

        self._server = HTTPServer(("127.0.0.1", 0), Handler)
        outer.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self) -> _ModelsServer:
        self._thread.start()
        return self

    def __exit__(self, *_exc) -> None:
        self._server.shutdown()
        self._server.server_close()

    @property
    def chat_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1/chat/completions"


# ---------------------------------------------------------------------------
# router-health-check.sh
# ---------------------------------------------------------------------------


def test_router_health_unreachable_emits_nested_json():
    """Normal case: LM Studio unreachable → nested SessionStart warning, not flat string."""
    env = {"_HOOK_QWEN_URL": _dead_chat_url()}
    r = subprocess.run(
        ["bash", str(ROUTER_HEALTH)],
        capture_output=True,
        text=True,
        env={**_base_env(), **env},
    )
    ctx = _assert_session_start_emission(r.stdout)
    assert "unreachable" in ctx.lower()


def test_router_health_missing_models_emits_nested_json():
    """Reachable but model absent → nested SessionStart 'missing models' warning."""
    with _ModelsServer(model_id_in_body="some-other-model") as srv:
        env = {
            "_HOOK_QWEN_URL": srv.chat_url,
            "_HOOK_ROUTER_MODEL": "granite-4.1-3b",
        }
        r = subprocess.run(
            ["bash", str(ROUTER_HEALTH)],
            capture_output=True,
            text=True,
            env={**_base_env(), **env},
        )
    ctx = _assert_session_start_emission(r.stdout)
    assert "missing models" in ctx.lower()
    assert "granite-4.1-3b" in ctx


def test_router_health_adversarial_body_does_not_corrupt_json():
    """Adversarial model id (quotes/backslash/triple-quote) must survive json.dumps
    intact — the old triple-quoted-literal + 2>/dev/null path faked a clean check here."""
    with _ModelsServer(model_id_in_body=ADVERSARIAL) as srv:
        env = {
            "_HOOK_QWEN_URL": srv.chat_url,
            "_HOOK_ROUTER_MODEL": "granite-4.1-3b",
        }
        r = subprocess.run(
            ["bash", str(ROUTER_HEALTH)],
            capture_output=True,
            text=True,
            env={**_base_env(), **env},
        )
    # granite is still missing (body only carries the adversarial id) → a warning emits,
    # and it MUST be valid nested JSON despite the adversarial content on the wire.
    ctx = _assert_session_start_emission(r.stdout)
    assert "granite-4.1-3b" in ctx


def test_router_health_env_driven_model_no_hardcoded_qwen():
    """ROUTER-07: the required model comes from _HOOK_ROUTER_MODEL, not a hardcoded id."""
    custom = "my-custom-router-model-xyz"
    with _ModelsServer(model_id_in_body="unrelated") as srv:
        env = {"_HOOK_QWEN_URL": srv.chat_url, "_HOOK_ROUTER_MODEL": custom}
        r = subprocess.run(
            ["bash", str(ROUTER_HEALTH)],
            capture_output=True,
            text=True,
            env={**_base_env(), **env},
        )
    ctx = _assert_session_start_emission(r.stdout)
    assert custom in ctx, "health check did not use the env-supplied model id"


# ---------------------------------------------------------------------------
# health-banner.sh
# ---------------------------------------------------------------------------


def _make_banner_project(tmp_path: Path, health_json: str) -> Path:
    """Create a throwaway project tree with a stub .memory/log.py that prints
    `health_json`, plus a copy of the real health-banner.sh, and return the hook path."""
    mem = tmp_path / ".memory"
    mem.mkdir()
    stub = mem / "log.py"
    stub.write_text(
        "import sys\n"
        f"sys.stdout.write({health_json!r})\n"
    )
    hooks = tmp_path / ".claude" / "hooks"
    hooks.mkdir(parents=True)
    dest = hooks / "health-banner.sh"
    shutil.copy(HEALTH_BANNER, dest)
    return dest


def test_health_banner_green_emits_nothing(tmp_path):
    """When 0 FAIL / 0 WARN, the banner stays silent (no output)."""
    health = json.dumps({"summary": {"passes": 3, "warns": 0, "fails": 0}, "results": []})
    hook = _make_banner_project(tmp_path, health)
    r = subprocess.run(["bash", str(hook)], capture_output=True, text=True)
    assert r.stdout.strip() == "", f"green run should emit nothing, got: {r.stdout!r}"


def test_health_banner_not_green_emits_nested_json(tmp_path):
    """Normal not-green case → nested SessionStart banner, not raw print() stdout."""
    health = json.dumps(
        {
            "summary": {"passes": 1, "warns": 1, "fails": 1},
            "results": [
                {"severity": "FAIL", "name": "broker", "message": "down", "hint": "start it"},
                {"severity": "WARN", "name": "router", "message": "slow"},
                {"severity": "PASS", "name": "db", "message": "ok"},
            ],
        }
    )
    hook = _make_banner_project(tmp_path, health)
    r = subprocess.run(["bash", str(hook)], capture_output=True, text=True)
    ctx = _assert_session_start_emission(r.stdout)
    assert "broker" in ctx and "router" in ctx


def test_health_banner_adversarial_message_does_not_corrupt_json(tmp_path):
    """A health message containing quotes/backslashes/triple-quotes must survive
    json.dumps intact and still parse as valid nested JSON."""
    health = json.dumps(
        {
            "summary": {"passes": 0, "warns": 0, "fails": 1},
            "results": [
                {
                    "severity": "FAIL",
                    "name": "evil",
                    "message": ADVERSARIAL,
                    "hint": ADVERSARIAL,
                }
            ],
        }
    )
    hook = _make_banner_project(tmp_path, health)
    r = subprocess.run(["bash", str(hook)], capture_output=True, text=True)
    ctx = _assert_session_start_emission(r.stdout)
    # The literal adversarial run must round-trip unmangled inside additionalContext.
    assert ADVERSARIAL.strip() in ctx


def _base_env() -> dict:
    """A minimal PATH-bearing env so bash/python3/curl resolve in the subprocess."""
    import os

    return {"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", "")}


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
