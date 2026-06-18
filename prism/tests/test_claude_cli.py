"""Tests for the claude_cli backend (ClaudeCliClient + config defaults).

No real `claude` process is ever spawned: asyncio.create_subprocess_exec and
shutil.which are patched to return canned values. No network, no CLI, no model.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from prism.config import Config
from prism.sensor.pipeline import (
    ClaudeCliClient,
    ClaudeCliError,
    LMStudioClient,
    build_client,
)


class _FakeProc:
    """Stand-in for an asyncio subprocess transport."""

    def __init__(self, *, stdout: bytes, stderr: bytes, returncode: int) -> None:
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode
        self.killed = False

    async def communicate(self, _stdin: bytes | None = None) -> tuple[bytes, bytes]:
        return self._stdout, self._stderr

    def kill(self) -> None:
        self.killed = True

    async def wait(self) -> int:
        return self.returncode


def _patch_subprocess(
    monkeypatch: Any, proc: _FakeProc, captured: dict[str, Any]
) -> None:
    async def _fake_exec(*argv: str, **_kwargs: Any) -> _FakeProc:
        captured["argv"] = list(argv)
        return proc

    monkeypatch.setattr(
        "prism.sensor.pipeline.shutil.which", lambda _name: "/usr/bin/claude"
    )
    monkeypatch.setattr(
        "prism.sensor.pipeline.asyncio.create_subprocess_exec", _fake_exec
    )


def _envelope(
    result: str, *, is_error: bool = False, subtype: str = "success"
) -> bytes:
    return json.dumps(
        {"type": "result", "subtype": subtype, "is_error": is_error, "result": result}
    ).encode("utf-8")


def test_default_backend_is_claude_cli(monkeypatch):
    monkeypatch.delenv("PRISM_BACKEND", raising=False)
    monkeypatch.setattr("prism.config.shutil.which", lambda _name: "/usr/bin/claude")
    cfg = Config.from_env()
    assert cfg.backend == "claude_cli"


def test_build_client_returns_claude_cli_for_default(monkeypatch):
    monkeypatch.delenv("PRISM_BACKEND", raising=False)
    monkeypatch.setattr("prism.config.shutil.which", lambda _name: "/usr/bin/claude")
    cfg = Config.from_env()
    assert isinstance(build_client(cfg), ClaudeCliClient)


def test_build_client_honours_lmstudio_and_anthropic(monkeypatch):
    monkeypatch.setattr("prism.config.shutil.which", lambda _name: "/usr/bin/claude")
    base = Config.from_env()
    lm = build_client(Config(**{**base.__dict__, "backend": "lmstudio"}))
    assert isinstance(lm, LMStudioClient)


@pytest.mark.asyncio
async def test_claude_cli_parses_result_field(monkeypatch):
    captured: dict[str, Any] = {}
    proc = _FakeProc(
        stdout=_envelope('{"suspicious": false}'), stderr=b"", returncode=0
    )
    _patch_subprocess(monkeypatch, proc, captured)

    client = ClaudeCliClient(cli_path="claude", timeout=5.0)
    out = await client.complete("claude-haiku-4-5", "is this ok?")

    assert out == '{"suspicious": false}'
    argv = captured["argv"]
    assert argv[0] == "claude"
    assert "-p" in argv
    assert "--output-format" in argv and "json" in argv
    assert "--model" in argv and "claude-haiku-4-5" in argv


@pytest.mark.asyncio
async def test_claude_cli_raises_loudly_on_nonzero_exit(monkeypatch, caplog):
    captured: dict[str, Any] = {}
    proc = _FakeProc(stdout=b"", stderr=b"not authenticated", returncode=1)
    _patch_subprocess(monkeypatch, proc, captured)

    client = ClaudeCliClient(cli_path="claude", timeout=5.0)
    with caplog.at_level("WARNING", logger="prism"), pytest.raises(ClaudeCliError):
        await client.complete("claude-haiku-4-5", "prompt")
    assert any("exited 1" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_claude_cli_raises_on_error_envelope(monkeypatch):
    captured: dict[str, Any] = {}
    proc = _FakeProc(
        stdout=_envelope("", is_error=True, subtype="error_max_turns"),
        stderr=b"",
        returncode=0,
    )
    _patch_subprocess(monkeypatch, proc, captured)

    client = ClaudeCliClient(cli_path="claude", timeout=5.0)
    with pytest.raises(ClaudeCliError):
        await client.complete("claude-haiku-4-5", "prompt")


@pytest.mark.asyncio
async def test_claude_cli_raises_on_unparseable_json(monkeypatch):
    captured: dict[str, Any] = {}
    proc = _FakeProc(stdout=b"not json at all", stderr=b"", returncode=0)
    _patch_subprocess(monkeypatch, proc, captured)

    client = ClaudeCliClient(cli_path="claude", timeout=5.0)
    with pytest.raises(ClaudeCliError):
        await client.complete("claude-haiku-4-5", "prompt")


@pytest.mark.asyncio
async def test_claude_cli_raises_when_not_on_path(monkeypatch, caplog):
    monkeypatch.setattr("prism.sensor.pipeline.shutil.which", lambda _name: None)

    client = ClaudeCliClient(cli_path="claude", timeout=5.0)
    with caplog.at_level("WARNING", logger="prism"), pytest.raises(ClaudeCliError):
        await client.complete("claude-haiku-4-5", "prompt")
    assert any("not found on PATH" in r.message for r in caplog.records)


def test_validate_warns_when_claude_not_on_path(monkeypatch, caplog):
    monkeypatch.setattr("prism.config.shutil.which", lambda _name: None)
    cfg = Config(
        backend="claude_cli",
        anthropic_api_key="",
        claude_base_url="",
        triage_model="t",
        diagnose_model="d",
        rca_model="r",
        genome_path=":memory:",
        severity_threshold=5,
        lmstudio_base_url="http://localhost:1234/v1",
        claude_cli_path="claude",
        claude_cli_timeout=120.0,
    )
    with caplog.at_level("WARNING", logger="prism"):
        cfg.validate()
    assert any("not on PATH" in r.message for r in caplog.records)
