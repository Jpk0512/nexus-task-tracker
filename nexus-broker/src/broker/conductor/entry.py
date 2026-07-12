"""broker.conductor.entry — the production conductor entrypoint (R4-T03/N34,
plans/14-cutover-activation-plan.md SS4).

Two callers converge on ONE journal (`.memory/files/conductor_runs.jsonl`,
append-only JSONL, no `.memory/schema.sql` change — the deterministic
liveness evidence the SS5 `check_liveness.py` registry probe reads):

- `run_dag_entry()` — validates a node-contract DAG via `broker.node_contract`
  BEFORE any dispatch and refuses (raises `dag.DagValidationError`, zero
  dispatch side effects, no journal line) on a failing DAG; a valid DAG runs
  through the existing `broker.conductor.dag.run_dag` work-stealing scheduler
  unchanged.
- `run_verify_matrix_entry()` — the R4-T02 verify-matrix tenant (plan-13 N03)
  promoted to a repeatable standing job invokable through this SAME entry;
  it wraps `broker.conductor.verify_matrix.run_verify_matrix_tenant`
  unchanged (idempotent/re-runnable, no shared mutable state).

Both wrap their underlying call with one journal line: {run_id, tenant,
status, started_at, wall_ms}. The journal write is best-effort in the sense
that a write failure never masks the underlying run's own success/failure —
but it is NOT best-effort in the sense of being skipped: this entry's whole
purpose is to make every run leave deterministic evidence behind (unlike
`record_dispatch_telemetry`'s per-node best-effort DB write, which this
entry does not replace or duplicate).

AVAILABILITY GATE (`conductor.enabled`, DEC-056): the conductor is a
SELECTIVE, opt-in engine for advanced/qualifying DAG work — never the
default execution path for general dispatch. Both entrypoints refuse
BEFORE any validation/dispatch/journal-write when the repo-root
`.claude/conductor.enabled` flag file is absent (raises
`ConductorDisabledError`, zero side effects). To run an advanced DAG
on-demand:

    touch .claude/conductor.enabled && python -m broker.conductor run <dag.yaml>

Same env-override / repo-root-walk convention as `broker.node_contract`'s
codex-lane flag (`NEXUS_CONDUCTOR_FLAG_PATH` overrides the resolved path —
used by tests/CLI to exercise the on/off gate without touching the real
`.claude/` tree).
"""
from __future__ import annotations

import json
import os
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from broker import node_contract
from broker.conductor import dag as dag_mod
from broker.conductor import verify_matrix
from broker.state import REPO_ROOT

CONDUCTOR_RUNS_JOURNAL: Path = REPO_ROOT / ".memory" / "files" / "conductor_runs.jsonl"
CONDUCTOR_FLAG_RELATIVE_PATH = ".claude/conductor.enabled"


class ConductorDisabledError(Exception):
    """Raised by both entrypoints when `conductor.enabled` is absent/off —
    zero dispatch side effects, no journal line (DEC-056: opt-in, never the
    default execution path)."""

    def __init__(self, flag_path: Path) -> None:
        self.flag_path = flag_path
        super().__init__(
            f"conductor is disabled — flag file not found: {flag_path}. "
            f"Enable on-demand with: touch {CONDUCTOR_FLAG_RELATIVE_PATH}"
        )


def _default_conductor_flag_path() -> Path:
    """Resolve the real repo's conductor-enabled flag file: NEXUS_CONDUCTOR_FLAG_PATH
    env-overrides the resolved path (same convention as node_contract's
    NEXUS_CODEX_LANE_FLAG_PATH), else repo-root (broker.state.REPO_ROOT, which
    already walks up from this tree to find the `.memory/` marker — the broker
    may run from `nexus-broker/`) / CONDUCTOR_FLAG_RELATIVE_PATH."""
    override = os.environ.get("NEXUS_CONDUCTOR_FLAG_PATH")
    if override:
        return Path(override)
    return REPO_ROOT / CONDUCTOR_FLAG_RELATIVE_PATH


def _resolve_conductor_flag_path(override: str | Path | None) -> Path:
    if override is not None:
        return Path(override)
    return _default_conductor_flag_path()


def _require_conductor_enabled(conductor_flag_path: str | Path | None) -> None:
    """The availability-gate chokepoint both entrypoints call FIRST, before any
    validation/dispatch/journal write. Raises `ConductorDisabledError` (clean,
    zero side effects) when the flag file does not exist."""
    flag_path = _resolve_conductor_flag_path(conductor_flag_path)
    if not flag_path.exists():
        raise ConductorDisabledError(flag_path)


def _new_run_id() -> str:
    return uuid.uuid4().hex


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def append_run_record(
    *, run_id: str, tenant: str, status: str, started_at: str, wall_ms: int,
    journal_path: str | Path = CONDUCTOR_RUNS_JOURNAL,
) -> None:
    """Append ONE JSONL line to the conductor-run journal. Append-only,
    never truncates/rewrites — the SS5 registry probe reads recency off this
    file, so every call is additive."""
    path = Path(journal_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "run_id": run_id, "tenant": tenant, "status": status,
        "started_at": started_at, "wall_ms": wall_ms,
    }
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")


def run_dag_entry(
    dag_path: str | Path, *, max_workers: int = 2, cwd_root: str = ".",
    claude_model: str = "sonnet", claude_bin: str = "claude", codex_bin: str = "codex",
    journal_path: str | Path = CONDUCTOR_RUNS_JOURNAL,
    conductor_flag_path: str | Path | None = None,
    dispatch_claude_fn: Callable[..., dag_mod.NodeResult] = dag_mod.dispatch_claude,
    dispatch_codex_fn: Callable[..., dag_mod.NodeResult] = dag_mod.dispatch_codex,
) -> dict[str, Any]:
    """Validate `dag_path` via `broker.node_contract` BEFORE any dispatch —
    a failing DAG raises `dag.DagValidationError` with ZERO dispatch side
    effects and NO journal line (there was no run to record). A valid DAG
    dispatches through the unchanged `dag.run_dag` work-stealing scheduler
    (`dispatch_claude_fn`/`dispatch_codex_fn` pass straight through — same
    injectable-dependency convention `dag.run_dag` already exposes, used by
    tests to stand in for a real `claude`/`codex` binary); the whole call
    (validation + dispatch) is wrapped by one journal line tagged with the
    DAG's own id (falls back to the file stem).

    AVAILABILITY GATE (DEC-056): checked FIRST, before the DAG is even
    loaded — an absent/off `conductor.enabled` flag raises
    `ConductorDisabledError`, zero side effects (`conductor_flag_path`
    overrides the resolved flag path, same convention as `journal_path`)."""
    _require_conductor_enabled(conductor_flag_path)
    doc = node_contract.load_dag(dag_path)
    errors = node_contract.validate_dag(doc)
    if errors:
        raise dag_mod.DagValidationError(errors)

    tenant = doc.get("dag_id") or Path(dag_path).stem
    run_id = _new_run_id()
    started_at = _now_iso()
    start = time.monotonic()
    result = dag_mod.run_dag(
        doc, max_workers=max_workers, cwd_root=cwd_root,
        claude_model=claude_model, claude_bin=claude_bin, codex_bin=codex_bin,
        dispatch_claude_fn=dispatch_claude_fn, dispatch_codex_fn=dispatch_codex_fn,
    )
    wall_ms = int((time.monotonic() - start) * 1000)
    status = "ok" if all(r.ok for r in result.results.values()) else "failed"

    append_run_record(
        run_id=run_id, tenant=tenant, status=status, started_at=started_at,
        wall_ms=wall_ms, journal_path=journal_path,
    )
    return {"run_id": run_id, "tenant": tenant, "status": status, "wall_ms": wall_ms, "result": result}


def run_verify_matrix_entry(
    *, cwd: str = ".", claude_bin: str = "claude", run_label: str | None = None,
    journal_path: str | Path = CONDUCTOR_RUNS_JOURNAL,
    conductor_flag_path: str | Path | None = None,
) -> dict[str, Any]:
    """The verify-matrix tenant (R4-T02) as a repeatable standing job through
    this entry — wraps `verify_matrix.run_verify_matrix_tenant` unchanged
    (idempotent/re-runnable per its own docstring) and appends one journal
    line tagged tenant='verify-matrix'.

    AVAILABILITY GATE (DEC-056): checked FIRST, same chokepoint as
    `run_dag_entry` — an absent/off `conductor.enabled` flag raises
    `ConductorDisabledError` before the tenant ever runs."""
    _require_conductor_enabled(conductor_flag_path)
    run_id = _new_run_id()
    started_at = _now_iso()
    tenant_result = verify_matrix.run_verify_matrix_tenant(cwd=cwd, claude_bin=claude_bin, run_label=run_label)
    status = "ok" if tenant_result.get("passed") else "failed"
    wall_ms = int(tenant_result.get("duration_ms", 0))

    append_run_record(
        run_id=run_id, tenant="verify-matrix", status=status, started_at=started_at,
        wall_ms=wall_ms, journal_path=journal_path,
    )
    return {"run_id": run_id, "tenant": "verify-matrix", "status": status, "wall_ms": wall_ms, "result": tenant_result}
