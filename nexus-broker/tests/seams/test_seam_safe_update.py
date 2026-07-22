"""Seam test: safe_update's atomic pre-flight -> apply -> health-gate pipeline
(TASK-118).

Real integration boundary: the actual `safe_update()` orchestration function
(nexus-package/tools/safe_update.py) driven end to end against a temp-dir
"existing install" and a temp-dir package tree — real `_snapshot_surfaces`,
real `_apply_update`, real `_run_health_gate`, real `_restore_surfaces`; none
of safe_update's own machinery is mocked. Only `render_install` — a heavy
full-template renderer independently exercised for real by
test_seam_render_install.py — is swapped for a minimal-but-structurally-real
stand-in (same technique this repo's own test_safe_update_atomic.py already
uses) so this seam stays fast and hermetic. The health-gate FAILURE case
injects a controlled failure at that one seam (mirroring this codebase's own
AC-4 pattern) specifically to prove the rollback wiring — not the health-gate
implementation — restores prior bytes.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_NEXUS_PACKAGE = _REPO_ROOT / "nexus-package"
_TOOLS_DIR = _NEXUS_PACKAGE / "tools"

if not _NEXUS_PACKAGE.is_dir():
    pytest.skip(
        "nexus-package/ absent — this tree is an installed target, not the Plexus "
        "meta-repo; this seam only has a real safe_update() source tree to drive "
        "where nexus-package/ ships",
        allow_module_level=True,
    )

if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

import safe_update as _su  # noqa: E402

_OLD_PROJECT_DB_BYTES = b"SQLITE3-BINARY-BLOB-SEAM-TASK-118"
_OLD_CLAUDE_MD = "# Project CLAUDE.md — must survive\n"


def _write_json(path: Path, obj: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2) + "\n")


def _make_dest(root: Path, version: str = "1.6.0") -> Path:
    """A minimal but structurally real 'existing install' safe_update updates."""
    dest = root / "dest"
    broker_src = dest / "nexus-broker" / "src" / "broker"
    broker_src.mkdir(parents=True)
    (broker_src / "server.py").write_text("# old server\n")
    (broker_src / "__init__.py").write_text("")

    hooks = dest / ".claude" / "hooks"
    hooks.mkdir(parents=True)
    (hooks / "health-banner.sh").write_text("#!/bin/bash\necho 'old'\n")

    (dest / "CLAUDE.md").write_text(_OLD_CLAUDE_MD)

    memory = dest / ".memory"
    memory.mkdir(parents=True)
    (memory / "log.py").write_text("# old log.py\n")
    (memory / "project.db").write_bytes(_OLD_PROJECT_DB_BYTES)
    (memory / ".nexus-version").write_text(version + "\n")

    _write_json(
        dest / ".mcp.json",
        {"mcpServers": {"nexus-broker": {"command": "uv", "args": []}}},
    )
    _write_json(
        dest / ".nexus-ledger.json",
        {"version": version, "installed_at": "2026-01-01T00:00:00+00:00", "source": "plexus"},
    )
    return dest


def _make_pkg(root: Path, version: str = "1.9.0") -> Path:
    """A minimal but structurally real src_root safe_update reads from."""
    pkg = root / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "VERSION").write_text(version + "\n")
    (pkg / ".memory").mkdir(parents=True)
    (pkg / ".memory" / "log.py").write_text("# new log.py\n")

    broker_src = pkg / "nexus-broker" / "src" / "broker"
    broker_src.mkdir(parents=True)
    (broker_src / "server.py").write_text("# new server\n")
    (broker_src / "__init__.py").write_text("")
    return pkg


def _stub_render_install(profile: dict, src_root: Path, staging: Path) -> None:
    """Minimal render_install stand-in — real render_install is exercised
    end-to-end by test_seam_render_install.py; this only needs to satisfy
    safe_update's own apply/roster-coverage preconditions."""
    agents = staging / ".claude" / "agents"
    agents.mkdir(parents=True, exist_ok=True)
    for persona in ("nexus-orchestrator", "scout", "lens", "lens-fast"):
        (agents / f"{persona}.md").write_text(f"# New {persona} agent\n")

    hooks = staging / ".claude" / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    (hooks / "health-banner.sh").write_text("#!/bin/bash\necho 'new'\n")

    (staging / ".claude" / "skills").mkdir(parents=True, exist_ok=True)
    (staging / ".claude" / "settings.json").write_text(
        '{"agent":"nexus-orchestrator","mcpServers":{}}\n'
    )

    _write_json(
        staging / ".mcp.json",
        {"mcpServers": {"nexus-broker": {"command": "uv", "args": []}}},
    )
    (staging / ".memory").mkdir(parents=True, exist_ok=True)
    (staging / ".memory" / "nexus-stack.json").write_text('{"profile":"new"}\n')


@pytest.fixture()
def dest(tmp_path: Path) -> Path:
    return _make_dest(tmp_path)


@pytest.fixture()
def pkg(tmp_path: Path) -> Path:
    return _make_pkg(tmp_path)


@pytest.fixture()
def profile(dest: Path) -> dict:
    return {"project_path": str(dest), "persona_set": ["nexus-orchestrator"], "stack_skills": []}


@pytest.fixture(autouse=True)
def _patch_render_install():
    with patch.object(_su, "render_install", side_effect=_stub_render_install):
        yield


def test_seam_preflight_apply_health_gate_all_succeed(dest: Path, pkg: Path, profile: dict) -> None:
    """GIVEN a valid existing install and a valid package, WHEN safe_update
    runs its real preflight -> snapshot -> apply -> health-gate pipeline,
    THEN it reports ok=True/rolled_back=False, delivers the new content, and
    stamps the version to match the package."""
    result = _su.safe_update(profile, pkg, dest)

    assert result["ok"] is True, result
    assert result["rolled_back"] is False
    assert result["error"] is None

    assert (dest / "nexus-broker" / "src" / "broker" / "server.py").read_text() == "# new server\n"
    assert (dest / ".memory" / ".nexus-version").read_text().strip() == "1.9.0"

    # never-clobber surfaces must survive a successful apply byte-for-byte
    assert (dest / ".memory" / "project.db").read_bytes() == _OLD_PROJECT_DB_BYTES
    assert (dest / "CLAUDE.md").read_text() == _OLD_CLAUDE_MD


def test_seam_health_gate_failure_rolls_back_to_prior_bytes(
    dest: Path, pkg: Path, profile: dict
) -> None:
    """GIVEN a clean apply, WHEN the post-apply health gate is forced to fail
    (an injected failure), THEN safe_update auto-rolls-back: content AND the
    version stamp are restored to their pre-update bytes, and the report
    carries rolled_back=True — the atomic all-or-nothing guarantee the
    daemon-corruption-class incidents depend on."""
    old_server = (dest / "nexus-broker" / "src" / "broker" / "server.py").read_text()
    old_version = (dest / ".memory" / ".nexus-version").read_text()

    def _injected_failure(*_args, **_kwargs) -> dict:
        return {"ok": False, "reason": "seam-injected failure (TASK-118)"}

    with patch.object(_su, "_run_health_gate", side_effect=_injected_failure):
        result = _su.safe_update(profile, pkg, dest)

    assert result["ok"] is False
    assert result["rolled_back"] is True

    assert (dest / "nexus-broker" / "src" / "broker" / "server.py").read_text() == old_server
    assert (dest / ".memory" / ".nexus-version").read_text() == old_version
    assert (dest / ".memory" / "project.db").read_bytes() == _OLD_PROJECT_DB_BYTES
    assert (dest / "CLAUDE.md").read_text() == _OLD_CLAUDE_MD
