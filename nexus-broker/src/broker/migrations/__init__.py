"""Fleet migration-batch PoC (R5-T11 / plans/15-r5-dag.yaml N65).

Alembic-inspired (linear revision chain + a per-DB version-stamp table) but
dependency-free: real `alembic` is not in `nexus-broker`'s pyproject (adding it
is outside this node's write_scope), and the PoC's job is to prove the BATCH +
RESUME contract the future fleet-wide rollout (N68/N69, deferred) will drive —
not to reproduce Alembic's own migration-authoring surface.

Scope guard: this module NEVER touches the real `.memory/schema.sql` or a real
install. It operates only on caller-supplied sqlite DB paths (fixtures in
tests). Each target DB gets its OWN `alembic_version_poc` stamp table, mirroring
per-install version pinning as described in
`nexus-redesign/plans/16-fleet-rollback-plan.md`.
"""
from __future__ import annotations

from broker.migrations.batch import (
    BatchResult,
    BatchRunner,
    InstallResult,
    Migration,
    current_version,
    sql_step,
)

__all__ = [
    "BatchResult",
    "BatchRunner",
    "InstallResult",
    "Migration",
    "current_version",
    "sql_step",
]
