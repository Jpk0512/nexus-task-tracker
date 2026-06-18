"""Security regression tests for nexus-vault MCP — VAULT-1 through VAULT-5.

These tests are written FIRST (TDD) and are expected to FAIL against the current
code because all five bugs are unpatched. After the fix lands they must all PASS.

VAULT-3 (CRITICAL) — path traversal in _resolve_note (notes.py L23-35)
VAULT-1 (CRITICAL) — fence fail-open in vault_query domain=None (search.py L162)
VAULT-2 (HIGH)     — fence leak in vault_related domain=None (notes.py L135-157)
VAULT-5 (HIGH)     — path traversal in _resolve_repo_path (graph.py L34-47)
VAULT-4 (HIGH)     — unenforced fence in note_resource (prompts_resources.py L119-129)
"""
from __future__ import annotations

import json
import textwrap
from pathlib import Path
from typing import Any

import pytest

from broker.vault._server import AppConfig
from broker.vault.notes import _resolve_note, vault_get_note_impl, vault_related_impl
from broker.vault.graph import _resolve_repo_path
from broker.vault import policy as policy_mod


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(tmp_path: Path, access_mode: str) -> AppConfig:
    """Minimal AppConfig with an isolated vault root and db."""
    vault_root = tmp_path / "vault"
    vault_root.mkdir(exist_ok=True)
    db_path = tmp_path / "project.db"

    # Privacy rules
    (vault_root / ".privacy-rules.yaml").write_text(
        "version: 1\n"
        "fenced_domains: [personal, work]\n"
        "access_modes:\n"
        "  local_stdio:     { can_read_fenced: true }\n"
        "  elevated_bearer: { can_read_fenced: true }\n"
        "  web_default:     { can_read_fenced: false }\n"
        "enforcement:\n"
        "  vault_query:    { fenced_requires: [local_stdio, elevated_bearer], on_violation: return_empty }\n"
        "  vault_get_note: { fenced_requires: [local_stdio, elevated_bearer], on_violation: return_empty }\n"
    )

    # Seed a minimal sqlite db (vault_jobs table required by AppConfig init)
    import sqlite3
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        "CREATE TABLE IF NOT EXISTS vault_jobs ("
        "  job_id TEXT PRIMARY KEY, enqueued_at TEXT NOT NULL, kind TEXT NOT NULL,"
        "  payload TEXT NOT NULL, status TEXT NOT NULL, started_at TEXT,"
        "  finished_at TEXT, result TEXT, error TEXT"
        ");"
    )
    conn.commit()
    conn.close()

    return AppConfig(
        vault_root=vault_root,
        db_path=db_path,
        access_mode=access_mode,
        write_paths=("40-inbox/raw/",),
    )


def _write_note(vault_root: Path, rel: str, domain: str, body: str = "secret content") -> Path:
    """Create a vault note with frontmatter at vault_root/rel."""
    p = vault_root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        f"---\ntitle: {p.stem}\ndomain: {domain}\nmaturity: seedling\n---\n\n{body}\n"
    )
    return p


def _write_public_note(vault_root: Path) -> Path:
    """Create a non-fenced public note."""
    return _write_note(
        vault_root,
        "10-knowledge/general-knowledge/pub-note.md",
        domain="general-knowledge",
        body="public content",
    )


def _write_fenced_note(vault_root: Path, domain: str = "personal") -> Path:
    """Create a fenced (personal/work) note."""
    return _write_note(
        vault_root,
        f"10-knowledge/{domain}/secret-note.md",
        domain=domain,
        body="TOP SECRET fenced content",
    )


# ---------------------------------------------------------------------------
# VAULT-3: path traversal in _resolve_note
# ---------------------------------------------------------------------------

class TestVault3PathTraversal:
    """_resolve_note must never return a path outside vault_root."""

    def test_relative_traversal_dot_dot_returns_none(self, tmp_path: Path) -> None:
        """GWT: Given a raw_path with ../ sequences, _resolve_note must return None.

        Currently FAILS: _resolve_note resolves vault_root / '../../../../etc/passwd'
        and returns the candidate if it exists, leaking files outside the vault.
        """
        vault_root = tmp_path / "vault"
        vault_root.mkdir()
        # Place a canary outside the vault
        canary = tmp_path / "outside.md"
        canary.write_text("OUTSIDE VAULT")

        raw_path = "../outside.md"
        result = _resolve_note(vault_root, raw_path)
        # Fix: containment check must reject this
        assert result is None, (
            f"VAULT-3: _resolve_note returned {result!r} for traversal path {raw_path!r}; "
            "must return None — path escapes vault_root"
        )

    def test_absolute_path_returns_none(self, tmp_path: Path) -> None:
        """GWT: Given an absolute raw_path, _resolve_note must return None.

        Currently FAILS: _resolve_note blindly accepts absolute paths as candidates.
        """
        vault_root = tmp_path / "vault"
        vault_root.mkdir()
        # Create a real file outside the vault
        outside = tmp_path / "etc_passwd_sim.md"
        outside.write_text("root:x:0:0")

        raw_path = str(outside)  # absolute path
        result = _resolve_note(vault_root, raw_path)
        assert result is None, (
            f"VAULT-3: _resolve_note accepted absolute path {raw_path!r} -> {result!r}; "
            "must reject all absolute raw_path inputs"
        )

    @pytest.mark.asyncio
    async def test_vault_get_note_traversal_returns_not_found(self, tmp_path: Path) -> None:
        """GWT: vault_get_note with ../../../../etc/passwd must return error, not file content.

        Currently FAILS: _resolve_note is called without containment so the traversal
        candidate is accepted when the file exists.
        """
        config = _make_config(tmp_path, "local_stdio")
        # Simulate a file that exists outside the vault
        outside = tmp_path / "sensitive.md"
        outside.write_text("SENSITIVE DATA")

        result = await vault_get_note_impl(
            config=config,
            path=f"../sensitive.md",
            include_body=True,
        )
        assert result.get("error") == "not_found" or result.get("error") == "path_traversal", (
            f"VAULT-3: vault_get_note leaked traversal result: {result}"
        )
        assert "SENSITIVE DATA" not in result.get("body", ""), (
            "VAULT-3: sensitive content leaked via path traversal"
        )

    @pytest.mark.asyncio
    async def test_vault_get_note_absolute_path_denied(self, tmp_path: Path) -> None:
        """GWT: vault_get_note with absolute path must not return content outside vault."""
        config = _make_config(tmp_path, "local_stdio")
        outside = tmp_path / "abs-target.md"
        outside.write_text("ABSOLUTE TARGET CONTENT")

        result = await vault_get_note_impl(
            config=config,
            path=str(outside),
            include_body=True,
        )
        assert result.get("error") in ("not_found", "path_traversal"), (
            f"VAULT-3: vault_get_note accepted absolute path -> {result}"
        )
        assert "ABSOLUTE TARGET CONTENT" not in result.get("body", ""), (
            "VAULT-3: absolute path traversal leaked content"
        )


# ---------------------------------------------------------------------------
# VAULT-1: fence fail-open — vault_query domain=None on web_default
# ---------------------------------------------------------------------------

class TestVault1FenceFailOpen:
    """vault_query with domain=None must not return fenced notes under web_default."""

    @pytest.mark.asyncio
    async def test_unfiltered_query_excludes_fenced_notes_on_web(self, tmp_path: Path) -> None:
        """GWT: Given fenced note + public note, vault_query(domain=None) on web_default
        must return ONLY the public note.

        Currently FAILS: fence at search.py L162 only triggers when caller explicitly
        passes a fenced domain string. domain=None bypasses the fence entirely, so
        _list_recent walks the whole vault and returns fenced notes.
        """
        config = _make_config(tmp_path, "web_default")
        _write_public_note(config.vault_root)
        _write_fenced_note(config.vault_root, domain="personal")

        from broker.vault.search import vault_query_impl

        result = await vault_query_impl(
            config=config,
            filters={},        # domain=None — the fail-open path
            query=None,        # triggers _list_recent
            order_by=None,
            mode="fast",
            limit=50,
        )

        hits: list[dict[str, Any]] = result.get("hits", [])
        fenced_hits = [
            h for h in hits
            if h.get("domain") in ("personal", "work")
            or "personal" in (h.get("path") or "")
            or "work" in (h.get("path") or "")
        ]
        assert fenced_hits == [], (
            f"VAULT-1: unfiltered web_default query returned fenced notes: {fenced_hits}"
        )

    @pytest.mark.asyncio
    async def test_explicit_fenced_domain_is_still_blocked(self, tmp_path: Path) -> None:
        """Sanity: the existing explicit-domain fence must still work after fix."""
        config = _make_config(tmp_path, "web_default")
        _write_fenced_note(config.vault_root, domain="personal")

        from broker.vault.search import vault_query_impl

        result = await vault_query_impl(
            config=config,
            filters={"domain": "personal"},
            query=None,
            order_by=None,
            mode="fast",
            limit=50,
        )
        assert result.get("fenced") is True, (
            f"Explicit fenced-domain query should return fenced=True, got: {result}"
        )
        assert result.get("hits") == [], (
            f"Explicit fenced-domain query must return empty hits, got: {result}"
        )


# ---------------------------------------------------------------------------
# VAULT-2: fence leak in vault_related domain=None
# ---------------------------------------------------------------------------

class TestVault2RelatedFenceLeak:
    """vault_related must not return fenced neighbours under web_default."""

    @pytest.mark.asyncio
    async def test_related_excludes_fenced_neighbours_on_web(self, tmp_path: Path) -> None:
        """GWT: vault_related on a public note must not return fenced neighbours
        when access_mode=web_default.

        Currently FAILS: _recall_fast is called with domain=None (notes.py L152),
        which does not filter by fence — fenced neighbours leak through.
        """
        config = _make_config(tmp_path, "web_default")
        pub = _write_public_note(config.vault_root)
        _write_fenced_note(config.vault_root, domain="personal")
        _write_fenced_note(config.vault_root, domain="work")

        # vault_related calls _recall_fast which calls vault.ingest.recall —
        # that import path may not be available in this isolated fixture.
        # We verify the fence post-filter logic by patching _recall_fast to
        # simulate it returning fenced notes (the real bug).
        from unittest.mock import patch

        fenced_hit: dict[str, Any] = {
            "path": "10-knowledge/personal/secret-note.md",
            "ref_id": "10-knowledge/personal/secret-note.md",
            "domain": "personal",
            "kind": "research",
            "text_blob": "TOP SECRET fenced content",
            "distance": 0.1,
            "confidence": 3,
            "maturity": "seedling",
            "created_at": "2026-01-01T00:00:00+00:00",
        }
        public_hit: dict[str, Any] = {
            "path": "10-knowledge/general-knowledge/pub-note.md",
            "ref_id": "10-knowledge/general-knowledge/pub-note.md",
            "domain": "general-knowledge",
            "kind": "research",
            "text_blob": "public content",
            "distance": 0.2,
            "confidence": 4,
            "maturity": "evergreen",
            "created_at": "2026-01-01T00:00:00+00:00",
        }

        with patch("broker.vault.notes._recall_fast", return_value=[fenced_hit, public_hit]):
            result = await vault_related_impl(
                config=config,
                path=pub.relative_to(config.vault_root).as_posix(),
                limit=10,
            )

        hits: list[dict[str, Any]] = result.get("hits", [])
        fenced_hits = [h for h in hits if h.get("domain") in ("personal", "work")]
        assert fenced_hits == [], (
            f"VAULT-2: vault_related returned fenced neighbours on web_default: {fenced_hits}"
        )


# ---------------------------------------------------------------------------
# VAULT-5: path traversal in _resolve_repo_path
# ---------------------------------------------------------------------------

class TestVault5GraphPathTraversal:
    """_resolve_repo_path must not resolve to paths outside vault_root."""

    def test_relative_traversal_is_rejected(self, tmp_path: Path) -> None:
        """GWT: _resolve_repo_path with ../../.. must NOT resolve outside vault_root.

        Currently FAILS: _resolve_repo_path at graph.py L38 does
        `return (vault_root / repo_path).resolve()` with no containment check.
        """
        vault_root = tmp_path / "vault"
        vault_root.mkdir()

        traversal = "../../../etc"
        result = _resolve_repo_path(vault_root, traversal)

        # After fix: should raise ValueError or return a path inside vault_root
        try:
            result.relative_to(vault_root.resolve())
        except ValueError:
            pytest.fail(
                f"VAULT-5: _resolve_repo_path({traversal!r}) resolved to {result!r} "
                "which escapes vault_root — containment check missing"
            )

    def test_absolute_path_is_rejected(self, tmp_path: Path) -> None:
        """GWT: _resolve_repo_path with an absolute path must not return it unchanged.

        Currently FAILS: graph.py L35-36 short-circuits for absolute paths —
        `if p.is_absolute(): return p` — no containment enforced.
        """
        vault_root = tmp_path / "vault"
        vault_root.mkdir()

        absolute_repo = "/etc"
        result = _resolve_repo_path(vault_root, absolute_repo)

        # After fix: absolute paths must be rejected (raise or return a safe sentinel)
        try:
            result.relative_to(vault_root.resolve())
        except ValueError:
            pytest.fail(
                f"VAULT-5: _resolve_repo_path accepted absolute path {absolute_repo!r} "
                f"-> {result!r} without containment check"
            )


# ---------------------------------------------------------------------------
# VAULT-4: unenforced fence in note_resource (prompts_resources.py)
# ---------------------------------------------------------------------------

class TestVault4NoteResourceFence:
    """note_resource must apply the privacy fence; fenced notes must return stub on web_default."""

    def test_note_resource_fenced_note_returns_stub_on_web(self, tmp_path: Path) -> None:
        """GWT: note_resource for a fenced domain note on web_default must not return content.

        Currently FAILS: note_resource at prompts_resources.py L119-129 does path containment
        but NO fence check — it reads and returns the raw note text regardless of domain.
        """
        config = _make_config(tmp_path, "web_default")
        fenced = _write_fenced_note(config.vault_root, domain="personal")
        rel_path = fenced.relative_to(config.vault_root).as_posix()

        # Call note_resource directly (it's a plain sync function created by register_*).
        # We replicate its closure logic here to test the fence path in isolation.
        candidate = (config.vault_root / rel_path).resolve()
        try:
            candidate.relative_to(config.vault_root.resolve())
        except ValueError:
            pytest.fail("Test setup error: fenced note path should be inside vault")

        assert candidate.is_file()
        raw_text = candidate.read_text(encoding="utf-8", errors="ignore")

        # Simulate what note_resource currently does (no fence):
        # It just returns the raw text. After fix it should return a fenced stub.
        # Parse domain from frontmatter
        import re
        m = re.search(r"^domain:\s*(\S+)", raw_text, re.MULTILINE)
        domain = m.group(1) if m else None

        decision = policy_mod.enforce("vault_get_note", domain, config.access_mode)
        assert decision in ("return_empty", "deny"), (
            f"VAULT-4: policy says '{decision}' for domain={domain!r} on web_default; "
            "note_resource must honour this but currently does not"
        )

        # The actual current implementation leaks: it returns raw_text.
        # Test that it does NOT contain the secret body text.
        # This assertion proves the bug exists (the current impl returns raw content).
        # After fix, note_resource must return a fenced stub JSON, not raw_text.
        from broker.vault.prompts_resources import register_prompts_and_resources

        # Capture what note_resource returns by building a minimal mock MCP
        captured: dict[str, Any] = {}

        class _MockMCP:
            def prompt(self, *, name: str):
                def decorator(fn):
                    return fn
                return decorator

            def resource(self, pattern: str):
                def decorator(fn):
                    # Capture by pattern so job_resource does not overwrite note_resource
                    captured[pattern] = fn
                    return fn
                return decorator

        register_prompts_and_resources(_MockMCP(), config)  # type: ignore[arg-type]

        note_fn = captured.get("note://{rel_path}")
        assert note_fn is not None, f"note_resource not registered; captured keys={list(captured)}"

        returned = note_fn(rel_path)

        # After fix: returned must NOT contain raw fenced body content
        assert "TOP SECRET fenced content" not in returned, (
            f"VAULT-4: note_resource on web_default returned raw fenced content for "
            f"domain={domain!r}. Returned: {returned[:200]!r}"
        )
