"""broker.conductor.ramp — empirical fan-out ramp protocol (R4-T01, plan-13 SS9).

Ramps pool size N over {1, 2, 4, 8, ...}, running the same disjoint task
batch at each level, and records per-N median wall-clock + failure rate +
rate-limit signal count via the EXISTING R1-T01 `dispatch_telemetry` path
(`.memory/log.py dispatch record` — the same subprocess-to-log.py convention
`broker.db.log_broker_validation` already uses; no new table, no schema
change). Stops at the first level where the failure rate exceeds 5% or the
median wall-clock stops improving over the prior level; the PRECEDING level
is the empirical fan-out ceiling (plan-13 SS9).
"""
from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys

from broker.conductor.pool import WorkerTask, run_pool
from broker.state import REPO_ROOT

_FAILURE_RATE_STOP = 0.05
_DEFAULT_LEVELS = [1, 2, 4, 8, 16, 32]


def _record_telemetry(*, n: int, median_ms: float, failure_rate: float, rate_limited_n: int) -> None:
    """Best-effort write of one ramp-level summary row via the existing
    `dispatch record` CLI. failure_rate/rate_limited_n have no dedicated
    columns in dispatch_telemetry, so they ride the free-text `marker`
    column rather than inventing a new table/schema."""
    marker = f"ramp n={n} fail_rate={failure_rate:.3f} rate_limited={rate_limited_n}"
    cmd = [
        sys.executable, str(REPO_ROOT / ".memory" / "log.py"), "dispatch", "record",
        "--persona", "pipeline-async", "--model", "ramp-probe",
        "--task-id", f"R4-T01-ramp-n{n}", "--marker", marker,
        "--tokens", "0", "--token-source", "approx",
        "--duration-ms", str(int(median_ms)),
    ]
    subprocess.run(cmd, capture_output=True, timeout=10, cwd=str(REPO_ROOT))


def ramp(
    prompt: str, *, cwd: str = ".", claude_bin: str = "claude",
    levels: list[int] | None = None, batch_size: int | None = None,
) -> dict:
    """Run the empirical ramp protocol; returns {"levels": [...], "ceiling_n": N}."""
    levels = levels or _DEFAULT_LEVELS
    rows: list[dict] = []
    prev_median: float | None = None
    for n in levels:
        batch = batch_size or n
        tasks = [WorkerTask(task_id=f"ramp-{n}-{i}", prompt=prompt, cwd=cwd) for i in range(batch)]
        results = run_pool(tasks, max_workers=n, claude_bin=claude_bin)

        durations = [r.duration_ms for r in results]
        failures = [r for r in results if not r.ok]
        rate_limited = [r for r in results if r.rate_limited]
        median_ms = statistics.median(durations) if durations else 0.0
        failure_rate = len(failures) / len(results) if results else 0.0

        rows.append({"n": n, "median_ms": median_ms, "failure_rate": failure_rate,
                      "rate_limited_count": len(rate_limited)})
        _record_telemetry(
            n=n, median_ms=median_ms, failure_rate=failure_rate,
            rate_limited_n=len(rate_limited),
        )

        stop = failure_rate > _FAILURE_RATE_STOP
        if prev_median is not None and median_ms >= prev_median:
            stop = True
        prev_median = median_ms
        if stop:
            break

    ceiling_n = rows[-1]["n"] if len(rows) == 1 else rows[-2]["n"]
    return {"levels": rows, "ceiling_n": ceiling_n}


def _cli() -> None:
    parser = argparse.ArgumentParser(prog="python -m broker.conductor.ramp")
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--cwd", default=".")
    parser.add_argument("--claude-bin", default="claude")
    parser.add_argument("--levels", default=",".join(str(n) for n in _DEFAULT_LEVELS))
    args = parser.parse_args()
    levels = [int(x) for x in args.levels.split(",") if x]
    result = ramp(args.prompt, cwd=args.cwd, claude_bin=args.claude_bin, levels=levels)
    print(json.dumps(result))


if __name__ == "__main__":
    _cli()
