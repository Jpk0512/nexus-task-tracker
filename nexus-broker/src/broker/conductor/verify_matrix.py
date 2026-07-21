"""broker.conductor.verify_matrix — the W1 3-gate verify-matrix conductor
tenant (R4-T02, plan-13 N03) + its harness-lane wall-clock baseline.

The verify matrix is 3 independently-scoped verification gates (lint / test /
contract) — each targets a DISJOINT path prefix, so the 3 legs run through
the N02 pool concurrently with zero write-conflict and no worktree question
(plan-13 SS7.2 N03 goal; R5-T15 precedent). Two entry points:

- `run_verify_matrix_tenant()` — the CONDUCTOR lane: dispatches the 3-gate
  batch through `broker.conductor.pool.run_pool` (concurrent). Stateless and
  side-effect-free beyond one best-effort telemetry write, so it is safe to
  call repeatedly — N13 re-measures it during the 1-week gate week.
- `capture_harness_baseline()` — the HARNESS lane: runs the SAME 3-gate
  batch sequentially (today's pre-conductor, one-worker-at-a-time dispatch
  shape), >=20 times, recording each run's wall-clock plus the median via
  the existing R1-T01 `dispatch_telemetry` path. This median is the
  DENOMINATOR plan-13 SS3's falsifiable wall-clock claim needs — N13 later
  compares the daemon-warm conductor number against this fixed baseline.

Both paths reuse the exact `.memory/log.py dispatch record` subprocess
convention `broker.conductor.ramp._record_telemetry` already established —
no new table, no schema change.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time

from broker.conductor.pool import WorkerTask, run_pool, run_worker
from broker.state import REPO_ROOT

# The 3 verify-matrix gates. Scopes are deliberately disjoint path prefixes
# (no gate's scope is a prefix of another's) so the batch can run concurrently
# through the pool with no shared-write surface — the "naturally-disjoint
# scopes, no worktree question" property named in plan-13 SS7.2's N03 goal.
_GATES = [
    {"gate_id": "lint", "scope": "nexus-broker/src/broker/conductor/**"},
    {"gate_id": "test", "scope": "nexus-broker/tests/**"},
    {"gate_id": "contract", "scope": "nexus-redesign/plans/**"},
]

_CONDUCTOR_TASK_ID = "R4-T02-verify-matrix-conductor"
_HARNESS_RUN_TASK_ID = "R4-T02-verify-matrix-harness-baseline"
_HARNESS_SUMMARY_TASK_ID = "R4-T02-verify-matrix-harness-baseline-summary"
_MIN_BASELINE_RUNS = 20


_GATE_TIMEOUT_S = 30.0
# P1 retry (RCA `.memory/scout-reports/1783912955/conductor-rca.md`): failed
# run `25182409948f4da1b473025fb8eb2f44` was exactly this — 1 of 3 synthetic
# gates hit a transient rc!=0/timeout and the whole tenant run recorded
# "failed" with no chance to shake off the blip. One bounded retry per gate.
_GATE_MAX_RETRIES = 1
_probe_cwd_cache: str | None = None


def _probe_cwd() -> str:
    """A neutral, project-free cwd for the bounded gate probes (R4-T06).
    Headless `claude --print` auto-loads CLAUDE.md/settings/hooks from its
    LAUNCH cwd exactly like an interactive session (sdk-workflow's
    documented cwd-as-governance landmine) — dispatching from the live
    nexus-installer repo triggers its `UserPromptSubmit` hooks, which
    inject orchestrator context (open tasks, invariants) ahead of the
    bounded prompt; the model answered THAT instead ("Ack.", "(idle)") at
    50-71s / $0.17-0.24 per call in a live smoke test. These probes do no
    filesystem work at all (`--tools=` denies every tool), so a plain OS
    temp dir with no `.claude/`/`CLAUDE.md` is strictly better than the
    repo root: same live smoke test went to 2.7s / $0.033 / on-prompt
    "PASS" from a neutral cwd. `_build_gate_tasks`'s own `cwd` argument is
    deliberately NOT used for subprocess dispatch — real governed gate
    work (N07) will need the real repo cwd; this timing/cost benchmark
    does not."""
    global _probe_cwd_cache
    if _probe_cwd_cache is None or not os.path.isdir(_probe_cwd_cache):
        _probe_cwd_cache = tempfile.mkdtemp(prefix="verify-matrix-probe-")
    return _probe_cwd_cache


def _gate_prompt(gate: dict) -> str:
    """Bounded, single-turn, mechanically-checkable (R4-T06): a real smoke
    probe against the OLD open-ended prompt took 251-264s and ~$1-1.50,
    blowing past the 120s timeout, and ignored the prompt entirely (one leg
    web-searched, one answered an unrelated topic) — an unbounded prompt
    invites unbounded (and expensive) reasoning/tool-use even though this
    is only a dispatch-timing probe, not a real lint/test/contract run.
    `--tools=` (pool._build_argv) structurally blocks tool use; this
    prompt's job is to make the ONE remaining degree of freedom — how much
    the model reasons/writes before answering — trivial too.

    Does NOT ask the model to attest "the {gate_id} gate PASSED" — a first
    live-smoke iteration asking exactly that got a well-founded REFUSAL on
    2 of 3 real calls ("I'm not going to output an unverified PASS
    attestation ... that's not a legitimate task"), since the model has no
    way to actually verify a real gate and correctly declined to rubber-
    stamp one. The check must be genuinely self-contained and already-true
    from the prompt text alone — evaluating a given trivial arithmetic
    statement, not attesting an unverifiable real-world claim — so PASS is
    an honest answer, not a rubber stamp."""
    return (
        f"This is a synthetic dispatch-timing benchmark (task label: "
        f"'{gate['gate_id']}-probe'), NOT a real code check and NOT a request "
        "to verify anything about any repository or gate. Evaluate this "
        "self-contained arithmetic statement: '2 + 2 = 4'. Do not investigate, "
        "search, or use any tool. Do not explain your answer. "
        "If the statement is true, output ONLY the single word PASS. "
        "If false, output ONLY the single word FAIL. No other output."
    )


def _build_gate_tasks(cwd: str) -> list[WorkerTask]:
    """One WorkerTask per gate, task_id-tagged by gate_id. `cwd` is accepted
    for signature/future compatibility (real governed gate work — N07 — will
    need the real repo cwd, asserted by the caller via write_scope, not by
    this fixture) but is NOT used for subprocess dispatch: these are bounded
    timing/cost PROBES, not real work, and dispatching from the repo cwd
    triggers CLAUDE.md/hook auto-load (see `_probe_cwd`) — use the neutral
    cwd instead. Bounded to `_GATE_TIMEOUT_S` (not pool.py's 120s default):
    a real single-turn, no-tool call that still hasn't answered in 30s
    should fail fast and visibly, not burn most of the old 120s ceiling on
    a misbehaving leg."""
    del cwd
    probe_cwd = _probe_cwd()
    return [
        WorkerTask(
            task_id=f"verify-matrix-{gate['gate_id']}",
            prompt=_gate_prompt(gate),
            cwd=probe_cwd,
            timeout_s=_GATE_TIMEOUT_S,
            max_retries=_GATE_MAX_RETRIES,
        )
        for gate in _GATES
    ]


def _dispatch_record(*, task_id: str, marker: str, duration_ms: int) -> None:
    """Best-effort write of one dispatch_telemetry row via the existing
    `dispatch record` CLI — same convention as `broker.conductor.ramp`."""
    cmd = [
        sys.executable, str(REPO_ROOT / ".memory" / "log.py"), "dispatch", "record",
        "--persona", "pipeline-async", "--model", "verify-matrix-probe",
        "--task-id", task_id, "--marker", marker,
        "--tokens", "0", "--token-source", "approx",
        "--duration-ms", str(int(duration_ms)),
    ]
    subprocess.run(cmd, capture_output=True, timeout=10, cwd=str(REPO_ROOT))


def run_verify_matrix_tenant(
    *, cwd: str = ".", claude_bin: str = "claude", run_label: str | None = None,
) -> dict:
    """Run the 3-gate verify matrix ONE time through the N02 pool (conductor
    lane, concurrent). Idempotent/re-runnable: every call is an independent
    pool dispatch with no shared mutable state and no cache to invalidate —
    N13 may invoke this as many times as the gate week needs."""
    tasks = _build_gate_tasks(cwd)
    start = time.monotonic()
    results = run_pool(tasks, max_workers=len(tasks), claude_bin=claude_bin)
    duration_ms = int((time.monotonic() - start) * 1000)

    gates = [
        {"gate_id": gate["gate_id"], "scope": gate["scope"],
         "ok": r.ok, "duration_ms": r.duration_ms, "error": r.error, "attempts": r.attempts}
        for gate, r in zip(_GATES, results, strict=True)
    ]
    passed = all(g["ok"] for g in gates)

    marker = f"verify-matrix conductor passed={passed}"
    if run_label:
        marker += f" label={run_label}"
    _dispatch_record(task_id=_CONDUCTOR_TASK_ID, marker=marker, duration_ms=duration_ms)

    return {"lane": "conductor", "passed": passed, "duration_ms": duration_ms, "gates": gates}


def capture_harness_baseline(
    *, cwd: str = ".", claude_bin: str = "claude", runs: int = _MIN_BASELINE_RUNS,
) -> dict:
    """Capture the harness-lane baseline for the SAME verify-matrix workload:
    the 3 gates dispatched sequentially (one worker at a time — today's
    pre-conductor dispatch shape, no pooling), repeated >=20 times. Each
    run's wall-clock is recorded via dispatch_telemetry, plus one summary
    row carrying the median — the value plan-13 SS3's falsifiable wall-clock
    claim divides the daemon-warm conductor number by."""
    if runs < _MIN_BASELINE_RUNS:
        raise ValueError(
            f"harness-lane baseline requires >={_MIN_BASELINE_RUNS} runs (plan-13 SS3); got {runs}"
        )

    durations_ms: list[int] = []
    for i in range(runs):
        tasks = _build_gate_tasks(cwd)
        start = time.monotonic()
        results = [run_worker(t, claude_bin=claude_bin) for t in tasks]
        duration_ms = int((time.monotonic() - start) * 1000)
        durations_ms.append(duration_ms)
        passed = all(r.ok for r in results)
        _dispatch_record(
            task_id=_HARNESS_RUN_TASK_ID,
            marker=f"verify-matrix harness run={i} passed={passed}",
            duration_ms=duration_ms,
        )

    median_ms = statistics.median(durations_ms)
    _dispatch_record(
        task_id=_HARNESS_SUMMARY_TASK_ID,
        marker=f"verify-matrix harness-lane BASELINE median_ms={median_ms:.1f} n={len(durations_ms)}",
        duration_ms=int(median_ms),
    )

    return {
        "lane": "harness", "median_ms": median_ms,
        "run_count": len(durations_ms), "durations_ms": durations_ms,
    }


def _cli() -> None:
    parser = argparse.ArgumentParser(prog="python -m broker.conductor.verify_matrix")
    sub = parser.add_subparsers(dest="cmd", required=True)

    tenant = sub.add_parser("tenant", help="run the verify-matrix tenant once through the pool")
    tenant.add_argument("--cwd", default=".")
    tenant.add_argument("--claude-bin", default="claude")
    tenant.add_argument("--label", default=None)

    baseline = sub.add_parser("baseline", help="capture the harness-lane baseline")
    baseline.add_argument("--cwd", default=".")
    baseline.add_argument("--claude-bin", default="claude")
    baseline.add_argument("--runs", type=int, default=_MIN_BASELINE_RUNS)

    args = parser.parse_args()
    if args.cmd == "tenant":
        result = run_verify_matrix_tenant(cwd=args.cwd, claude_bin=args.claude_bin, run_label=args.label)
    else:
        result = capture_harness_baseline(cwd=args.cwd, claude_bin=args.claude_bin, runs=args.runs)
    print(json.dumps(result))


if __name__ == "__main__":
    _cli()
