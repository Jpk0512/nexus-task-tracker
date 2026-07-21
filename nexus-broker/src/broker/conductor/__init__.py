"""broker.conductor — the `claude -p` worker pool + empirical ramp protocol (R4-T01)
plus the schema_version-2 node-contract DAG conductor (R4-T03).

Four modules:
- pool.py — WorkerTask/WorkerResult + run_worker()/run_pool(): spawns N headless
  `claude --print` workers, double-JSON-decodes each envelope (Skill sdk-workflow),
  and passes through per-worker cwd + allowedTools.
- ramp.py — ramp(): ramps pool size N over {1, 2, 4, ...}, recording per-N median
  wall-clock, failure rate, and rate-limit signals via the existing R1-T01
  dispatch_telemetry path, until the empirical fan-out ceiling is found.
- dag.py — run_dag(): validates a node-contract DAG via broker.node_contract before
  any dispatch, work-steals the ready-set over a pool of worker threads, and routes
  each node through the executor-dispatch switch (claude -> pool worker, codex ->
  direct `codex exec` subprocess) into one shared DispatchTelemetry shape.
- entry.py — run_dag_entry()/run_verify_matrix_entry() (R4-T03/N34): the production
  entrypoint (`python -m broker.conductor run|tenant`) that wraps dag.run_dag and the
  verify-matrix tenant with one append-only journal line each to
  `.memory/files/conductor_runs.jsonl` — the SS5 liveness-registry evidence.
"""
from __future__ import annotations

from broker.conductor.checkpoint import (
    append_node_checkpoint,
    checkpoint_record,
    load_checkpoint,
)
from broker.conductor.dag import (
    DagRunResult,
    DagValidationError,
    DispatchTelemetry,
    NodeResult,
    build_codex_argv,
    build_worker_templates,
    dispatch_claude,
    dispatch_codex,
    dispatch_node,
    record_dispatch_telemetry,
    run_dag,
)
from broker.conductor.entry import (
    CONDUCTOR_CHECKPOINTS_JOURNAL,
    CONDUCTOR_RUNS_JOURNAL,
    ConductorDisabledError,
    append_run_record,
    run_dag_entry,
    run_verify_matrix_entry,
)
from broker.conductor.pool import WorkerResult, WorkerTask, run_pool, run_worker
from broker.conductor.ramp import ramp

__all__ = [
    "CONDUCTOR_CHECKPOINTS_JOURNAL",
    "CONDUCTOR_RUNS_JOURNAL",
    "ConductorDisabledError",
    "DagRunResult",
    "DagValidationError",
    "DispatchTelemetry",
    "NodeResult",
    "WorkerResult",
    "WorkerTask",
    "append_node_checkpoint",
    "append_run_record",
    "build_codex_argv",
    "build_worker_templates",
    "checkpoint_record",
    "dispatch_claude",
    "dispatch_codex",
    "dispatch_node",
    "load_checkpoint",
    "ramp",
    "record_dispatch_telemetry",
    "run_dag",
    "run_dag_entry",
    "run_pool",
    "run_verify_matrix_entry",
    "run_worker",
]
