"""broker.observability.eval_job — graduates the R1-T06 B4 eval suite from
a one-shot manual CLI invocation to a repeatable job with recorded runs
(N58's acceptance criterion #3: "eval-infra job re-runnable with a
recorded run artifact (run id cited)").

`research/scripts/b4_eval.py` is the single home of the eval SCORING logic
(the deterministic keyword-overlap ranker, the anti-cheat-triad-compliant
recall/citation metrics) — this module never reimplements it, only
subprocess-invokes its real CLI and layers a run ledger on top. `--no-
history` is always passed: `research/_meta/` sits outside this node's
write_scope, so this job owns its OWN recorded-run artifact (via
`runs_path`) rather than mutating `eval-history.jsonl` as a side effect.
"""
from __future__ import annotations

import json
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[4]
B4_SCRIPT_REL = "research/scripts/b4_eval.py"
JOB_TIMEOUT_S = 60


def run_eval_job(
    *,
    split: str = "dev",
    top_k: int = 5,
    repo_root: Path | None = None,
    runs_path: Path | None = None,
    python_executable: str | None = None,
) -> dict[str, Any]:
    """Run the REAL `b4_eval.py run` CLI as a subprocess and record one
    ledger row. `result.returncode` of 1 is a valid completed run (the
    script's own breach-threshold exit code, per its docstring — not a job
    failure); any other non-zero code means the subprocess itself broke.
    Returns the recorded run dict; also appends it to `runs_path` (one JSON
    line per call) when given, so a second call with the same `runs_path`
    proves re-runnability against a growing, readable ledger.
    """
    root = repo_root if repo_root is not None else REPO_ROOT
    script = root / B4_SCRIPT_REL
    if not script.is_file():
        raise FileNotFoundError(f"b4 eval script not found: {script}")

    executable = python_executable or sys.executable
    result = subprocess.run(
        [executable, str(script), "run", "--split", split, "--top-k", str(top_k), "--no-history"],
        cwd=str(root),
        capture_output=True,
        text=True,
        timeout=JOB_TIMEOUT_S,
    )
    if result.returncode not in (0, 1):
        raise RuntimeError(
            f"b4 eval job failed (rc={result.returncode}): {result.stderr[-2000:]}"
        )
    try:
        eval_result = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"b4 eval job produced non-JSON stdout: {result.stdout[:500]!r}"
        ) from exc

    record: dict[str, Any] = {
        "run_id": f"b4-run-{uuid.uuid4().hex[:12]}",
        "recorded_at": datetime.now(UTC).isoformat(),
        "split": split,
        "top_k": top_k,
        "breach": eval_result.get("breach"),
        "recall_at_k_overall": eval_result.get("recall_at_k_overall"),
        "citation_precision": eval_result.get("citation_precision"),
        "eval_result": eval_result,
    }
    if runs_path is not None:
        _append_run(runs_path, record)
    return record


def _append_run(runs_path: Path, record: dict[str, Any]) -> None:
    runs_path.parent.mkdir(parents=True, exist_ok=True)
    with runs_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, sort_keys=True) + "\n")


def read_runs(runs_path: Path) -> list[dict[str, Any]]:
    """Read back every recorded run — the 're-runnable with a recorded run
    artifact' proof: each `run_eval_job(..., runs_path=P)` call appends one
    more line, never overwrites a prior run.
    """
    if not Path(runs_path).is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in Path(runs_path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows
