"""R3-T02/N05 — nexus_discover / nexus_prepare / nexus_run groundwork tools.

Covers:
  - unit behavior of the three *_impl functions (discovery.py)
  - the transport-agnostic proof (C1.a): the impl module imports with ZERO
    transport (fastmcp/starlette/uvicorn) loaded, so the future R4-T06 daemon
    can import broker.discovery directly without pulling in stdio-MCP.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import broker.state as state_mod


def _patch_state_path(monkeypatch, tmp_path: Path) -> Path:
    target = tmp_path / "broker_state.json"
    monkeypatch.setattr(state_mod, "STATE_PATH", target)
    return target


class TestNexusDiscover:
    def test_lists_dispatchable_personas(self) -> None:
        from broker.discovery import nexus_discover_impl
        from broker.registry import ALLOWED_PERSONAS, PERSONA_INTENTS

        result = nexus_discover_impl()

        assert set(result["personas"]) == set(ALLOWED_PERSONAS)
        assert result["persona_intents"] == {
            p: list(intents) for p, intents in PERSONA_INTENTS.items()
        }

    def test_is_pure_read_only(self, tmp_path: Path, monkeypatch) -> None:
        """nexus_discover must not touch broker_state.json at all."""
        target = _patch_state_path(monkeypatch, tmp_path)
        from broker.discovery import nexus_discover_impl

        nexus_discover_impl()

        assert not target.exists()


class TestNexusPrepare:
    def test_valid_persona_intent_prepares_ok(self, tmp_path: Path, monkeypatch) -> None:
        _patch_state_path(monkeypatch, tmp_path)
        from broker.discovery import nexus_prepare_impl

        result = nexus_prepare_impl(persona="scout", intent="investigate", turn_id="turn-1")

        assert result["ok"] is True
        assert result["errors"] == []
        assert result["prepared_at"] is not None

    def test_invalid_persona_rejected(self, tmp_path: Path, monkeypatch) -> None:
        _patch_state_path(monkeypatch, tmp_path)
        from broker.discovery import nexus_prepare_impl

        result = nexus_prepare_impl(persona="not-a-persona", intent="investigate", turn_id="turn-1")

        assert result["ok"] is False
        assert any("dispatch registry" in e for e in result["errors"])
        assert result["prepared_at"] is None

    def test_illegal_intent_for_valid_persona_rejected(self, tmp_path: Path, monkeypatch) -> None:
        _patch_state_path(monkeypatch, tmp_path)
        from broker.discovery import nexus_prepare_impl

        result = nexus_prepare_impl(persona="scout", intent="implement_api", turn_id="turn-1")

        assert result["ok"] is False
        assert any("is not legal for persona" in e for e in result["errors"])

    def test_empty_turn_id_rejected(self, tmp_path: Path, monkeypatch) -> None:
        _patch_state_path(monkeypatch, tmp_path)
        from broker.discovery import nexus_prepare_impl

        result = nexus_prepare_impl(persona="scout", intent="investigate", turn_id="  ")

        assert result["ok"] is False
        assert any("turn_id must be non-empty" in e for e in result["errors"])

    def test_writes_state_via_the_shared_state_file(self, tmp_path: Path, monkeypatch) -> None:
        """No resident hook-process state (C1.d) — persistence is the shared file."""
        target = _patch_state_path(monkeypatch, tmp_path)
        from broker.discovery import nexus_prepare_impl

        nexus_prepare_impl(persona="scout", intent="investigate", turn_id="turn-9")

        assert target.exists()
        written = state_mod.read_state()
        assert written["prepared_turn_id"] == "turn-9"
        assert written["prepared_persona"] == "scout"


class TestNexusRun:
    def test_run_without_prepare_fails(self, tmp_path: Path, monkeypatch) -> None:
        _patch_state_path(monkeypatch, tmp_path)
        from broker.discovery import nexus_run_impl

        result = nexus_run_impl(turn_id="turn-1")

        assert result["ok"] is False
        assert any("no matching prepared dispatch" in e for e in result["errors"])

    def test_run_after_prepare_succeeds(self, tmp_path: Path, monkeypatch) -> None:
        _patch_state_path(monkeypatch, tmp_path)
        from broker.discovery import nexus_prepare_impl, nexus_run_impl

        nexus_prepare_impl(persona="scout", intent="investigate", turn_id="turn-42")
        result = nexus_run_impl(turn_id="turn-42")

        assert result["ok"] is True
        assert result["started_at"] is not None
        assert result["errors"] == []

    def test_run_with_mismatched_turn_id_fails(self, tmp_path: Path, monkeypatch) -> None:
        _patch_state_path(monkeypatch, tmp_path)
        from broker.discovery import nexus_prepare_impl, nexus_run_impl

        nexus_prepare_impl(persona="scout", intent="investigate", turn_id="turn-a")
        result = nexus_run_impl(turn_id="turn-b")

        assert result["ok"] is False
        assert any("no matching prepared dispatch" in e for e in result["errors"])

    def test_run_with_stale_prepare_fails(self, tmp_path: Path, monkeypatch) -> None:
        """Uses NEXUS_TURN_STALE_SECONDS=0-equivalent (smallest positive: 1s) plus
        a manually-backdated prepared_at, so the test runs instantly (no real
        sleep) while still exercising the staleness branch."""
        _patch_state_path(monkeypatch, tmp_path)
        monkeypatch.setenv("NEXUS_TURN_STALE_SECONDS", "1")
        from datetime import UTC, datetime, timedelta

        from broker.discovery import nexus_prepare_impl, nexus_run_impl

        nexus_prepare_impl(persona="scout", intent="investigate", turn_id="turn-stale")
        state = state_mod.read_state()
        backdated = (datetime.now(tz=UTC) - timedelta(seconds=5)).isoformat()
        state["prepared_at"] = backdated
        state_mod.write_state(state)

        result = nexus_run_impl(turn_id="turn-stale")

        assert result["ok"] is False
        assert any("stale" in e for e in result["errors"])

    def test_run_respects_env_override_window(self, tmp_path: Path, monkeypatch) -> None:
        """A generous NEXUS_TURN_STALE_SECONDS admits an otherwise-stale prepare."""
        _patch_state_path(monkeypatch, tmp_path)
        monkeypatch.setenv("NEXUS_TURN_STALE_SECONDS", "3600")
        from datetime import UTC, datetime, timedelta

        from broker.discovery import nexus_prepare_impl, nexus_run_impl

        nexus_prepare_impl(persona="scout", intent="investigate", turn_id="turn-fresh")
        state = state_mod.read_state()
        backdated = (datetime.now(tz=UTC) - timedelta(seconds=5)).isoformat()
        state["prepared_at"] = backdated
        state_mod.write_state(state)

        result = nexus_run_impl(turn_id="turn-fresh")

        assert result["ok"] is True


class TestTransportAgnostic:
    """C1.a — broker.discovery must import with ZERO transport imported.

    Runs in a fresh subprocess (not just checking sys.modules in-process, which
    could already have fastmcp loaded from an earlier import in the same test
    session) so the assertion is a genuine cold-import proof.
    """

    def test_discovery_module_imports_with_zero_transport(self) -> None:
        broker_src = Path(__file__).resolve().parents[1] / "src"
        script = (
            "import sys\n"
            "import broker.discovery\n"
            "transport_markers = ('fastmcp', 'starlette', 'uvicorn', 'mcp')\n"
            "loaded = [m for m in sys.modules if m.split('.')[0] in transport_markers]\n"
            "assert not loaded, f'transport modules leaked into broker.discovery import: {loaded}'\n"
            "print('ZERO-TRANSPORT-OK')\n"
        )
        proc = subprocess.run(
            [sys.executable, "-c", script],
            cwd=str(broker_src),
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "ZERO-TRANSPORT-OK" in proc.stdout
