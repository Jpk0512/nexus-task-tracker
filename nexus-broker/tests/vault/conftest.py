"""Shared fixtures for broker.vault tests.

Builds an isolated vault + sqlite DB so the suite is hermetic — no shared state
with the real .memory/project.db or the production vault.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from broker.vault._server import AppConfig


def _seed_db(db_path: Path) -> None:
    """Minimal vault_jobs schema so tests run without the full M-002 setup."""
    import sqlite3

    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS vault_jobs (
              job_id TEXT PRIMARY KEY,
              enqueued_at TEXT NOT NULL,
              kind TEXT NOT NULL,
              payload TEXT NOT NULL,
              status TEXT NOT NULL,
              started_at TEXT,
              finished_at TEXT,
              result TEXT,
              error TEXT
            );
            CREATE INDEX IF NOT EXISTS vault_jobs_status_enqueued
              ON vault_jobs(status, enqueued_at);
            """
        )
        conn.commit()
    finally:
        conn.close()


def _seed_vault(vault_root: Path) -> None:
    """Skeleton vault zones + minimal fixture notes."""
    zones = [
        "00-meta",
        "10-knowledge/personal",
        "10-knowledge/work",
        "10-knowledge/general-knowledge",
        "10-knowledge/ai-techniques",
        "10-knowledge/nexus",
        "10-knowledge/plexus",
        "15-code-knowledge",
        "20-workshop/brainstorms/capsules",
        "20-workshop/pulled",
        "30-projects",
        "35-ai-techniques",
        "40-inbox/raw",
        "40-inbox/_jobs",
        "99-archive",
        "_meta",
    ]
    for z in zones:
        (vault_root / z).mkdir(parents=True, exist_ok=True)

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

    (vault_root / "00-meta" / "_MOC.md").write_text(
        "# Meta MOC\n\n## Curated notes\n\n- [[golden-note]]\n\n"
        "<!-- BEGIN AUTO -->\n- recent autogen 1\n<!-- END AUTO -->\n"
    )

    (vault_root / "10-knowledge" / "general-knowledge" / "golden-note.md").write_text(
        "---\n"
        "id: golden-note\n"
        "title: Golden note\n"
        "kind: research\n"
        "domain: general-knowledge\n"
        "maturity: evergreen\n"
        "secondary_domains: []\n"
        "ai-first: true\n"
        "source: manual\n"
        "captured: 2026-01-01T00:00:00Z\n"
        "confidence: 4\n"
        "tags: [test]\n"
        "---\n\n"
        "TL;DR: a golden test note. See also [[other-note]].\n"
    )
    (vault_root / "10-knowledge" / "personal" / "personal-secret.md").write_text(
        "---\n"
        "id: personal-secret\n"
        "title: Personal secret\n"
        "kind: research\n"
        "domain: personal\n"
        "maturity: seedling\n"
        "secondary_domains: []\n"
        "ai-first: true\n"
        "source: manual\n"
        "captured: 2026-01-01T00:00:00Z\n"
        "confidence: 3\n"
        "tags: [private]\n"
        "---\n\n"
        "TL;DR: a personal note that must be fenced from web_default.\n"
    )


@pytest.fixture()
def vault_env(tmp_path: Path) -> dict:
    """Builds a fresh vault_root + db_path for one test. Returns config dict."""
    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    db_path = tmp_path / "project.db"
    _seed_vault(vault_root)
    _seed_db(db_path)
    return {"vault_root": vault_root, "db_path": db_path, "tmp_path": tmp_path}


@pytest.fixture()
def config_local(vault_env) -> AppConfig:
    return AppConfig(
        vault_root=vault_env["vault_root"],
        db_path=vault_env["db_path"],
        access_mode="local_stdio",
        write_paths=(
            "40-inbox/raw/",
            "20-workshop/brainstorms/capsules/",
            "20-workshop/pulled/",
            "40-inbox/_jobs/",
        ),
    )


@pytest.fixture()
def config_web(vault_env) -> AppConfig:
    return AppConfig(
        vault_root=vault_env["vault_root"],
        db_path=vault_env["db_path"],
        access_mode="web_default",
        write_paths=(
            "40-inbox/raw/",
            "20-workshop/brainstorms/capsules/",
            "20-workshop/pulled/",
            "40-inbox/_jobs/",
        ),
    )
