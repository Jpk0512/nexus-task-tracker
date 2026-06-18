"""Shared FastMCP app factory for nexus-vault (plan §7.1, §7.2).

Constructs one FastMCP instance and registers:
  - 4 read tools  : vault_query, vault_get_note, vault_related, vault_moc
  - 2 graph tools : vault_graph_query, vault_health
  - 4 write tools : vault_append_inbox, vault_capture_idea, ingest_url, ingest_repo
  - 2 prompts     : vault-state-summary, vault-graduate-suggestions
  - 2 resources   : note://<path>, job://<id>

Access-mode (local_stdio / elevated_bearer / web_default) is bound at
construction time via the AppConfig — stdio.py constructs with
access_mode="local_stdio"; the (future Phase 5b) http server will construct
with "web_default" or "elevated_bearer" depending on the bearer presented.

B3 single-writer: write tools INSERT into vault_jobs only — never touch
vault files or vec_memory. The writer daemon (broker.vault.writer) holds
the file/embedding capability.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastmcp import FastMCP

from broker.vault import policy as policy_mod
from broker.vault.graph import vault_graph_query_impl, vault_health_impl
from broker.vault.moc import vault_moc_impl
from broker.vault.notes import vault_get_note_impl, vault_related_impl
from broker.vault.prompts_resources import register_prompts_and_resources
from broker.vault.search import vault_query_impl
from broker.vault.writes import (
    ingest_repo_impl,
    ingest_url_impl,
    vault_append_inbox_impl,
    vault_capture_idea_impl,
)


@dataclass(frozen=True)
class AppConfig:
    vault_root: Path
    db_path: Path
    access_mode: policy_mod.AccessMode
    write_paths: tuple[str, ...]


def _default_vault_root() -> Path:
    env = os.environ.get("NEXUS_VAULT_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    here = Path(__file__).resolve()
    # Look for a sibling `research/` dir that contains the privacy-rules marker —
    # excludes the broker.vault package directory which has the same leaf name.
    for c in [here, *here.parents]:
        candidate = c / "research"
        if candidate.is_dir() and (candidate / ".privacy-rules.yaml").is_file():
            return candidate.resolve()
    return (Path.cwd() / "research").resolve()


def _default_db_path() -> Path:
    env = os.environ.get("NEXUS_VAULT_DB")
    if env:
        return Path(env).expanduser().resolve()
    here = Path(__file__).resolve()
    for c in [here, *here.parents]:
        cand = c / ".memory" / "project.db"
        if cand.is_file():
            return cand.resolve()
    return (Path.cwd() / ".memory" / "project.db").resolve()


_DEFAULT_WRITE_PATHS = (
    "40-inbox/raw/",
    "20-workshop/brainstorms/capsules/",
    "20-workshop/pulled/",
    "40-inbox/_jobs/",
)


def _default_write_paths() -> tuple[str, ...]:
    env = os.environ.get("NEXUS_VAULT_WRITE_PATHS")
    if env:
        return tuple(p.strip() for p in env.split(",") if p.strip())
    return _DEFAULT_WRITE_PATHS


def build_config(
    *,
    access_mode: policy_mod.AccessMode,
    vault_root: Path | None = None,
    db_path: Path | None = None,
    write_paths: tuple[str, ...] | None = None,
) -> AppConfig:
    return AppConfig(
        vault_root=(vault_root or _default_vault_root()).resolve(),
        db_path=(db_path or _default_db_path()).resolve(),
        access_mode=access_mode,
        write_paths=write_paths or _default_write_paths(),
    )


def build_app(config: AppConfig, *, register_writes: bool = True) -> FastMCP:
    """Construct a FastMCP app with read tools + 2 prompts + 2 resources bound.

    register_writes=True (default, stdio) → also binds 4 write tools (full §7.2
    surface: 10 tools).
    register_writes=False (http daemon)   → write tools omitted; HTTP surface is
    strict read-only per Plexus Phase-5b decision (elevated bearer denied →
    web surface never enqueues writes). Documented in nexus-broker README as
    an INTENTIONAL deviation from plan §7.2 for the web transport.
    """
    mcp = FastMCP("nexus-vault")

    # ---------- Read tools ----------

    @mcp.tool()
    async def vault_query(
        filters: dict[str, Any] | None = None,
        query: str | None = None,
        order_by: str | None = None,
        mode: str = "fast",
        limit: int = 10,
    ) -> dict[str, Any]:
        """Unified search/list/recent over the vault.

        filters: {domain?, kind?, maturity?, tags?, exclude_maturity?, min_confidence?}
          - exclude_maturity defaults to ('archived',)
          - min_confidence    defaults to 3
        query: semantic search string (optional)
        mode: 'fast' (semantic) — 'hybrid' falls back to 'fast' if FTS5 empty.
        Privacy-fenced when filters.domain is in fenced_domains.
        """
        return await vault_query_impl(
            config=config,
            filters=filters or {},
            query=query,
            order_by=order_by,
            mode=mode,
            limit=limit,
        )

    @mcp.tool()
    async def vault_get_note(path: str, include_body: bool = True) -> dict[str, Any]:
        """Return frontmatter + body + backlinks + outbound_links for a vault note.

        Privacy-fenced when the note's frontmatter.domain is in fenced_domains.
        """
        return await vault_get_note_impl(config=config, path=path, include_body=include_body)

    @mcp.tool()
    async def vault_related(path: str, limit: int = 10) -> dict[str, Any]:
        """Semantic-similarity neighbours of a vault note (uses note TL;DR or title)."""
        return await vault_related_impl(config=config, path=path, limit=limit)

    @mcp.tool()
    async def vault_moc(zone: str) -> dict[str, Any]:
        """Read a zone's _MOC.md: {curated, recent}.

        zone: relative path under research/, e.g. '10-knowledge/ai-techniques'.
        """
        return await vault_moc_impl(config=config, zone=zone)

    # ---------- Graph + health tools ----------

    @mcp.tool()
    async def vault_graph_query(repo_path: str, jq_expr: str | None = None) -> dict[str, Any]:
        """Read <repo_path>/knowledge-graph.json; optional server-side jq filter."""
        return await vault_graph_query_impl(config=config, repo_path=repo_path, jq_expr=jq_expr)

    @mcp.tool()
    async def vault_health() -> dict[str, Any]:
        """Aggregate vault health: file counts per zone, last backup, validator status."""
        return await vault_health_impl(config=config)

    # ---------- Write tools (enqueue only) ----------
    if register_writes:

        @mcp.tool()
        async def vault_append_inbox(
            title: str, body: str, source: str = "manual", tags: list[str] | None = None
        ) -> dict[str, Any]:
            """Enqueue an append-to-inbox job → writes to 40-inbox/raw/<id>.md."""
            return await vault_append_inbox_impl(
                config=config, title=title, body=body, source=source, tags=tags or []
            )

        @mcp.tool()
        async def vault_capture_idea(
            title: str,
            body: str,
            kind: str = "brainstorm",
            source_note_paths: list[str] | None = None,
        ) -> dict[str, Any]:
            """Enqueue a capture-idea job → 20-workshop/brainstorms/capsules/ or pulled/."""
            return await vault_capture_idea_impl(
                config=config,
                title=title,
                body=body,
                kind=kind,
                source_note_paths=source_note_paths or [],
            )

        @mcp.tool()
        async def ingest_url(url: str, domain: str, notes: str = "") -> dict[str, Any]:
            """Enqueue a URL ingest job → 40-inbox/_jobs/<id>.yaml."""
            return await ingest_url_impl(config=config, url=url, domain=domain, notes=notes)

        @mcp.tool()
        async def ingest_repo(repo_url_or_path: str, target: str = "") -> dict[str, Any]:
            """Enqueue a repo-analyzer job → 40-inbox/_jobs/<id>.yaml."""
            return await ingest_repo_impl(
                config=config, repo_url_or_path=repo_url_or_path, target=target
            )

    # ---------- Prompts + resources ----------
    register_prompts_and_resources(mcp, config)

    return mcp
