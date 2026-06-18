"""broker.vault — nexus-vault MCP server (Phase 5a: stdio reader + writer daemon).

Per SECOND_BRAIN_PLAN.md §7 (B3 single-writer architecture):
- broker.vault.stdio   — read-only stdio MCP server (Claude Code, Claude Desktop)
- broker.vault.writer  — single-writer daemon, drains vault_jobs serially
- broker.vault.policy  — privacy fence (.privacy-rules.yaml)
- broker.vault._server — shared FastMCP app factory + tool registration
"""
