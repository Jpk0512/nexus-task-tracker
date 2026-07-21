"""broker.conductor.dag — schema_version-2 node-contract DAG conductor (R4-T03,
plan-13 N06).

Consumes a node-contract DAG document, validates it via `broker.node_contract`
BEFORE dispatching anything, and schedules the ready-set with a work-stealing
pool of worker threads pulling off one shared queue (an idle worker "steals"
whichever node is next-ready, regardless of which branch produced it — there
is no static per-branch assignment).

Warm-fork worker templates: every executor:claude node's `pool.WorkerTask` is
built ONCE at schedule-build time from the N02 pool module (`build_worker_templates`),
not lazily re-derived per dispatch.

The executor-dispatch switch (plans/11-codex-lane-design.md SS9.4 / SS10):
  executor == "claude" (default) -> a `claude -p` pool worker (broker.conductor.pool)
  executor == "codex"            -> a direct `codex exec` subprocess composing the
                                     documented argv (--output-schema, -s from
                                     write_scope, -C worktree, --json, brief on stdin)

Both arms report into the SAME `DispatchTelemetry` shape — for codex legs the
`turn.completed.usage` JSONL event fills `input_tokens`/`output_tokens`, and
`record_dispatch_telemetry` feeds both through the identical existing
`.memory/log.py dispatch record` CLI path ramp.py already uses for pool
workers (no new table, no schema change).
"""
from __future__ import annotations

import dataclasses
import json
import queue
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from broker import node_contract
from broker.conductor import checkpoint as checkpoint_mod
from broker.conductor import governance, pool
from broker.state import REPO_ROOT

_LOG_PY = REPO_ROOT / ".memory" / "log.py"

_BUDGET_TIMEOUT_S = {"S": 60.0, "M": 180.0, "L": 600.0, "XL": 1800.0}
_DEFAULT_TIMEOUT_S = 300.0


class DagValidationError(ValueError):
    """Raised by `run_dag` when the DAG fails `broker.node_contract.validate_dag`.

    Raised BEFORE any node is dispatched — the R4-T03 acceptance requirement
    that an invalid DAG rejects with zero dispatch side effects."""

    def __init__(self, errors: list[node_contract.ValidationError]) -> None:
        self.errors = errors
        summary = "; ".join(repr(e) for e in errors)
        super().__init__(f"{len(errors)} node-contract validation error(s): {summary}")


@dataclasses.dataclass
class DispatchTelemetry:
    """Uniform per-node telemetry — IDENTICAL shape for executor:claude (pool)
    and executor:codex legs (plan-13 N06: 'turn.completed.usage feeding the
    same dispatch_telemetry as pool workers'). `total_cost_usd` is populated
    for claude legs (from the envelope); `input_tokens`/`output_tokens` are
    populated for codex legs (from `turn.completed.usage`) — a leg only fills
    the fields its executor can observe, the other stays at its default."""

    node_id: str
    executor: str
    ok: bool
    duration_ms: int
    worker_id: str
    total_cost_usd: float = 0.0
    input_tokens: int | None = None
    output_tokens: int | None = None
    error: str | None = None


@dataclasses.dataclass
class NodeResult:
    node_id: str
    executor: str
    ok: bool
    worker_id: str
    telemetry: DispatchTelemetry
    payload: object | None = None
    argv: list[str] | None = None  # codex legs only — the composed exec argv
    error: str | None = None


@dataclasses.dataclass
class DagRunResult:
    results: dict[str, NodeResult]
    order: list[str]  # completion order actually observed (interleaved across workers)


def _node_result_from_checkpoint(record: dict[str, Any]) -> NodeResult:
    """Reconstruct a same-run cache-hit `NodeResult` from a checkpoint
    journal record (`checkpoint.load_checkpoint`) — never re-dispatched.
    The record already carries whatever the ORIGINAL dispatch (governance
    lens-gate included) decided; a cache hit does not re-run governance."""
    telemetry = DispatchTelemetry(
        node_id=record["node_id"], executor=record.get("executor", "claude"),
        ok=record["ok"], duration_ms=record.get("duration_ms") or 0,
        worker_id=record.get("worker_id", "resumed"),
        total_cost_usd=record.get("total_cost_usd") or 0.0,
        input_tokens=record.get("input_tokens"), output_tokens=record.get("output_tokens"),
        error=record.get("error"),
    )
    return NodeResult(
        node_id=record["node_id"], executor=record.get("executor", "claude"),
        ok=record["ok"], worker_id=record.get("worker_id", "resumed"),
        telemetry=telemetry, payload=record.get("payload"), error=record.get("error"),
    )


def _in_degree_and_dependents(
    nodes: dict[str, dict[str, Any]],
) -> tuple[dict[str, int], dict[str, list[str]]]:
    in_degree = {nid: len(node.get("depends_on") or []) for nid, node in nodes.items()}
    dependents: dict[str, list[str]] = {nid: [] for nid in nodes}
    for nid, node in nodes.items():
        for dep in node.get("depends_on") or []:
            dependents[dep].append(nid)
    return in_degree, dependents


def _budget_timeout_s(node: dict[str, Any]) -> float:
    return _BUDGET_TIMEOUT_S.get(node.get("budget"), _DEFAULT_TIMEOUT_S)


def build_worker_templates(
    nodes: dict[str, dict[str, Any]], *, cwd_root: str, model: str = "sonnet",
) -> dict[str, pool.WorkerTask]:
    """Warm-fork worker templates (N02 `pool.WorkerTask`) for every
    executor:claude node, built ONCE here — never re-derived per dispatch.

    `allowed_tools` is populated from `governance.allowed_tools_for_node`
    (R4-T04) — the per-leg grant derived from the node's `write_scope`/
    `agent_persona`; a node with no `write_scope` gets a read-only grant
    (no Edit/Write at all), matching the codex arm's read-only default for
    the identical input (SS9.5's mapping table, `broker.conductor.governance`)."""
    templates: dict[str, pool.WorkerTask] = {}
    for node_id, node in nodes.items():
        if node.get("executor", "claude") != "claude":
            continue
        templates[node_id] = pool.WorkerTask(
            task_id=node_id,
            prompt=node["goal"],
            cwd=cwd_root,
            model=model,
            timeout_s=_budget_timeout_s(node),
            allowed_tools=governance.allowed_tools_for_node(node),
        )
    return templates


def dispatch_claude(
    node: dict[str, Any], *, template: pool.WorkerTask, worker_id: str, claude_bin: str = "claude",
) -> NodeResult:
    """executor: claude arm of the dispatch switch — routes through the N02
    `claude -p` pool worker unchanged."""
    result = pool.run_worker(template, claude_bin=claude_bin)
    telemetry = DispatchTelemetry(
        node_id=node["node_id"], executor="claude", ok=result.ok, duration_ms=result.duration_ms,
        worker_id=worker_id, total_cost_usd=result.total_cost_usd, error=result.error,
    )
    return NodeResult(
        node_id=node["node_id"], executor="claude", ok=result.ok, worker_id=worker_id,
        telemetry=telemetry, payload=result.payload, error=result.error,
    )


def build_codex_argv(
    node: dict[str, Any], *, worktree: str, codex_bin: str = "codex",
    output_schema_path: str | None = None,
) -> list[str]:
    """Compose the documented `codex exec` argv (plans/11-codex-lane-design.md
    SS9.4/SS10): --output-schema, -s <sandbox derived from write_scope>,
    -C <worktree>, --json, brief on stdin (trailing '-' — the prompt itself is
    piped as subprocess stdin, never an argv element, per the CLI's own
    contract: "If '-' is used, instructions are read from stdin").

    Raises ValueError if write_scope has no expressible codex sandbox mode —
    should be unreachable via `run_dag` because `broker.node_contract`
    already rejects such a DAG before any dispatch happens; this is the
    direct-call defense for callers of `build_codex_argv`/`dispatch_codex`
    outside that path.
    """
    write_scope = node.get("write_scope") or []
    sandbox = node_contract._codex_sandbox_mode_for_write_scope(write_scope)
    if sandbox is None:
        raise ValueError(
            f"node {node.get('node_id')!r}: write_scope {write_scope!r} has no expressible codex "
            "sandbox mode (read-only or a bounded workspace-write scope)"
        )
    argv = [codex_bin, "exec", "--skip-git-repo-check", "-C", worktree, "-s", sandbox]
    executor_model = node.get("executor_model")
    if executor_model:
        argv += ["-m", executor_model]
    schema_path = output_schema_path or f"{worktree}/.nexus-codex-legs/{node.get('node_id')}.schema.json"
    argv += ["--output-schema", schema_path, "--json", "-"]
    return argv


def _decode_agent_message(text: str) -> object:
    """Same double-decode fallback pattern as `pool.run_worker`'s inner payload:
    strip a markdown fence, try JSON, and pass the raw text through (non-fatal)
    on a parse failure — never fabricate a structure that was not there."""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`").removeprefix("json").strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return text


def _parse_codex_jsonl(stdout: str) -> tuple[object | None, dict[str, Any] | None, str | None, str | None]:
    """Parse the `--json` JSONL event stream. Returns
    (payload, usage, thread_id, error) — `usage` is `turn.completed`'s `usage`
    object verbatim (the source of `input_tokens`/`output_tokens`); `error` is
    set on a `turn.failed`/`error` event and is never silently swallowed."""
    payload: object | None = None
    usage: dict[str, Any] | None = None
    thread_id: str | None = None
    error: str | None = None
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        etype = event.get("type")
        if etype == "thread.started":
            thread_id = event.get("thread_id")
        elif etype == "item.completed":
            item = event.get("item") or {}
            if item.get("type") == "agent_message":
                payload = _decode_agent_message(item.get("text", ""))
        elif etype == "turn.completed":
            usage = event.get("usage")
        elif etype in ("turn.failed", "error"):
            err = event.get("error") or event.get("message")
            error = json.dumps(err) if isinstance(err, dict) else str(err)
    return payload, usage, thread_id, error


def dispatch_codex(
    node: dict[str, Any], *, worktree: str, worker_id: str, codex_bin: str = "codex",
    output_schema_path: str | None = None,
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> NodeResult:
    """executor: codex arm of the dispatch switch — spawns `codex exec`
    directly (no Claude shim in the daemon/conductor lane). Fail-loud on
    nonzero rc / a `turn.failed` event, never a silent retry (Skill
    sdk-workflow). `run` is injectable so callers/tests stub the subprocess
    boundary without touching a real `codex` binary."""
    node_id = node["node_id"]
    argv = build_codex_argv(node, worktree=worktree, codex_bin=codex_bin, output_schema_path=output_schema_path)
    start = time.monotonic()
    try:
        proc = run(
            argv, input=node.get("goal", ""), capture_output=True, text=True,
            cwd=worktree, timeout=_budget_timeout_s(node),
        )
    except subprocess.TimeoutExpired as exc:
        duration_ms = int((time.monotonic() - start) * 1000)
        error = f"timeout: {exc}"
        telemetry = DispatchTelemetry(node_id, "codex", False, duration_ms, worker_id, error=error)
        return NodeResult(node_id, "codex", False, worker_id, telemetry, argv=argv, error=error)
    duration_ms = int((time.monotonic() - start) * 1000)

    payload, usage, _thread_id, parsed_error = _parse_codex_jsonl(proc.stdout)
    input_tokens = (usage or {}).get("input_tokens") if usage else None
    output_tokens = (usage or {}).get("output_tokens") if usage else None

    if proc.returncode != 0 or parsed_error:
        error = parsed_error or f"rc={proc.returncode}: {proc.stderr[:500]}"
        telemetry = DispatchTelemetry(
            node_id, "codex", False, duration_ms, worker_id,
            input_tokens=input_tokens, output_tokens=output_tokens, error=error,
        )
        return NodeResult(node_id, "codex", False, worker_id, telemetry, argv=argv, error=error)

    telemetry = DispatchTelemetry(
        node_id, "codex", True, duration_ms, worker_id,
        input_tokens=input_tokens, output_tokens=output_tokens,
    )
    return NodeResult(node_id, "codex", True, worker_id, telemetry, payload=payload, argv=argv)


def dispatch_node(
    node: dict[str, Any], *, worker_id: str, templates: dict[str, pool.WorkerTask],
    worktree_root: str, claude_bin: str = "claude", codex_bin: str = "codex",
    dispatch_claude_fn: Callable[..., NodeResult] = dispatch_claude,
    dispatch_codex_fn: Callable[..., NodeResult] = dispatch_codex,
) -> NodeResult:
    """THE executor-dispatch switch (plan-13 N06): executor claude -> pool
    worker (warm-fork template from N02); executor codex -> direct codex exec
    subprocess. Absence of `executor` defaults to claude, same as
    `broker.node_contract`."""
    executor = node.get("executor", "claude")
    if executor == "codex":
        return dispatch_codex_fn(node, worktree=worktree_root, worker_id=worker_id, codex_bin=codex_bin)
    template = templates[node["node_id"]]
    return dispatch_claude_fn(node, template=template, worker_id=worker_id, claude_bin=claude_bin)


def record_dispatch_telemetry(
    node: dict[str, Any], telemetry: DispatchTelemetry, *, cwd_root: str | Path = REPO_ROOT,
) -> None:
    """Feed one node's telemetry through the EXISTING `.memory/log.py dispatch
    record` CLI path — the same mechanism `broker.conductor.ramp` already uses
    for pool-worker telemetry (no new table, no schema change). Best-effort:
    a telemetry-recording failure never fails the DAG run."""
    if telemetry.input_tokens is not None or telemetry.output_tokens is not None:
        tokens = (telemetry.input_tokens or 0) + (telemetry.output_tokens or 0)
        token_source = "exact"
    else:
        tokens = 0
        token_source = "approx"
    marker = f"executor={telemetry.executor} ok={telemetry.ok}"
    if telemetry.error:
        marker += f" error={telemetry.error[:200]}"
    model = node.get("executor_model") or ("codex" if telemetry.executor == "codex" else "claude")
    cmd = [
        sys.executable, str(_LOG_PY), "dispatch", "record",
        "--persona", node.get("agent_persona", "pipeline-async"),
        "--model", model,
        "--task-id", telemetry.node_id,
        "--marker", marker,
        "--tokens", str(tokens), "--token-source", token_source,
        "--duration-ms", str(telemetry.duration_ms),
    ]
    subprocess.run(cmd, capture_output=True, timeout=10, cwd=str(cwd_root))


def run_dag(
    doc: dict[str, Any], *, max_workers: int = 2, cwd_root: str = ".",
    claude_model: str = "sonnet", claude_bin: str = "claude", codex_bin: str = "codex",
    codex_lane_flag_path: str | Path | None = None,
    validation_db_path: str | Path | None = None,
    dispatch_claude_fn: Callable[..., NodeResult] = dispatch_claude,
    dispatch_codex_fn: Callable[..., NodeResult] = dispatch_codex,
    telemetry_sink: Callable[[dict[str, Any], DispatchTelemetry], None] | None = None,
    run_id: str | None = None,
    checkpoint_journal_path: str | Path | None = None,
    resume: bool = False,
) -> DagRunResult:
    """Topologically schedule + work-steal a schema_version-2 node-contract
    DAG over `max_workers` threads pulling off ONE shared ready-queue — an
    idle worker "steals" whichever node is next-ready regardless of which
    branch produced it; there is no static per-branch assignment (R4-T03
    acceptance: work-stealing verified with >=2 workers on disjoint branches).

    Validates via `broker.node_contract` FIRST and raises `DagValidationError`
    — dispatching NOTHING — on any validation failure (R4-T03 acceptance).

    A node whose dispatch fails still unblocks its `downstream_consumers` in
    this v1 scheduler (no cascade-cancel policy yet).

    R4-T04/N07 in-process governance: a node that declares
    `required_lens_types` has its lens-gate v2 assertion
    (`governance.assert_lens_gate_v2`) checked HERE, before the node's own
    result is merged into `results`/`order` — a leg lacking its required
    distinct-lens PASS row(s) has its result overwritten with a blocked
    (`ok=False`) `NodeResult` (payload dropped, not merged) even though the
    underlying dispatch itself succeeded. `validation_db_path` defaults to
    the real repo's `.memory/project.db`; tests point it at a scratch DB.

    CHECKPOINT/RESUME (crash-resilience fix): when `checkpoint_journal_path`
    and `run_id` are both given, EVERY node's final result is durably
    journaled (`checkpoint.append_node_checkpoint`) the moment it completes
    — a crash loses at most the in-flight node, never a completed one. When
    `resume=True` too, the journal for `run_id` is read FIRST
    (`checkpoint.load_checkpoint`); any node_id already present there is a
    same-run cache hit — its cached `NodeResult` is reused and it is NEVER
    re-dispatched — and only node_ids with no journal line are scheduled.
    Both params default to `None`/`False`: a caller that passes neither gets
    byte-identical behavior to before this fix (no checkpointing at all).
    """
    errors = node_contract.validate_dag(doc, codex_lane_flag_path=codex_lane_flag_path)
    if errors:
        raise DagValidationError(errors)

    nodes: dict[str, dict[str, Any]] = {n["node_id"]: n for n in doc["nodes"]}
    in_degree, dependents = _in_degree_and_dependents(nodes)
    templates = build_worker_templates(nodes, cwd_root=cwd_root, model=claude_model)

    results: dict[str, NodeResult] = {}
    order: list[str] = []
    checkpointed_node_ids: set[str] = set()
    if resume and checkpoint_journal_path is not None and run_id is not None:
        cached = checkpoint_mod.load_checkpoint(checkpoint_journal_path, run_id)
        for node_id, record in cached.items():
            if node_id not in nodes:
                continue  # stale/foreign record — ignore, never fabricate a node not in THIS doc
            results[node_id] = _node_result_from_checkpoint(record)
            order.append(node_id)
            checkpointed_node_ids.add(node_id)
        for nid in checkpointed_node_ids:
            for dep_nid in dependents.get(nid, []):
                in_degree[dep_nid] -= 1

    ready: queue.Queue[str] = queue.Queue()
    for nid, deg in in_degree.items():
        if nid in checkpointed_node_ids:
            continue
        if deg == 0:
            ready.put(nid)

    lock = threading.Lock()
    remaining = len(nodes) - len(checkpointed_node_ids)

    def worker_loop(worker_id: str) -> None:
        nonlocal remaining
        while True:
            try:
                nid = ready.get(timeout=0.05)
            except queue.Empty:
                with lock:
                    if remaining <= 0:
                        return
                continue
            node = nodes[nid]
            try:
                result = dispatch_node(
                    node, worker_id=worker_id, templates=templates, worktree_root=cwd_root,
                    claude_bin=claude_bin, codex_bin=codex_bin,
                    dispatch_claude_fn=dispatch_claude_fn, dispatch_codex_fn=dispatch_codex_fn,
                )
            except Exception as exc:  # noqa: BLE001 — a dispatch-arm bug must not silently wedge the scheduler
                executor = node.get("executor", "claude")
                telemetry = DispatchTelemetry(nid, executor, False, 0, worker_id, error=str(exc))
                result = NodeResult(nid, executor, False, worker_id, telemetry, error=str(exc))
            if result.ok and node.get("required_lens_types"):
                db_path = validation_db_path or (REPO_ROOT / ".memory" / "project.db")
                gate_ok, gate_detail = governance.assert_lens_gate_v2(node, db_path=db_path)
                if not gate_ok:
                    error = f"lens-gate-v2 block (cannot merge): {gate_detail}"
                    telemetry = dataclasses.replace(result.telemetry, ok=False, error=error)
                    result = dataclasses.replace(result, ok=False, payload=None, error=error, telemetry=telemetry)
            if telemetry_sink is not None:
                telemetry_sink(node, result.telemetry)
            if checkpoint_journal_path is not None and run_id is not None:
                # Durable checkpoint BEFORE merging into in-memory results —
                # a crash between this write and the lock below still loses
                # nothing (the line is already flushed+fsynced to disk).
                record = checkpoint_mod.checkpoint_record(run_id, result)
                checkpoint_mod.append_node_checkpoint(checkpoint_journal_path, record)
            with lock:
                results[nid] = result
                order.append(nid)
                remaining -= 1
                for dep_nid in dependents.get(nid, []):
                    in_degree[dep_nid] -= 1
                    if in_degree[dep_nid] == 0:
                        ready.put(dep_nid)
            ready.task_done()

    threads = [
        threading.Thread(target=worker_loop, args=(f"w{i}",), daemon=True) for i in range(max_workers)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    return DagRunResult(results=results, order=order)
