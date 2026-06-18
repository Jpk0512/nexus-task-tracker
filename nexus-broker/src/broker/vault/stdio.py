"""nexus-vault stdio entry point — local trusted reader (Claude Code / Desktop).

Usage:
    python -m broker.vault.stdio

Reads env: NEXUS_VAULT_ROOT, NEXUS_VAULT_DB, NEXUS_VAULT_WRITE_PATHS.

Per plan §7.1 (B3 architecture):
  - Tools defined here NEVER touch vault files or vec_memory directly.
  - Read tools read from sqlite-vec via broker.vault.search + filesystem walks.
  - Write tools INSERT into vault_jobs only; broker.vault.writer drains them.

Per plan §7.4: local stdio is implicitly trusted — access_mode='local_stdio',
which can_read_fenced=True for domains personal + work.
"""
from __future__ import annotations

from broker.vault._server import build_app, build_config


def main() -> None:
    config = build_config(access_mode="local_stdio")
    app = build_app(config)
    app.run()


if __name__ == "__main__":
    main()
