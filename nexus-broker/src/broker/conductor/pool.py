"""broker.conductor.pool — headless `claude -p` worker pool (R4-T01, plan-13 N02).

Spawns N headless `claude --print` workers concurrently and collects each
one's double-JSON-decoded envelope, per Skill sdk-workflow's confirmed shape:
the outer envelope carries `result`/`model`/`total_cost_usd`, and `result`
is itself a JSON *string* needing a second decode to reach the real payload.
Every worker gets its own `cwd` (cwd-as-governance — sdk-workflow) and its
own `allowedTools` grant; neither is inherited from a parent session.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor

_RATE_LIMIT_SIGNALS = ("rate_limit", "rate limit", "429", "overloaded")


@dataclasses.dataclass
class WorkerTask:
    task_id: str
    prompt: str
    cwd: str
    allowed_tools: list[str] = dataclasses.field(default_factory=list)
    model: str = "sonnet"
    timeout_s: float = 120.0


@dataclasses.dataclass
class WorkerResult:
    task_id: str
    ok: bool
    duration_ms: int
    envelope: dict | None = None
    payload: object | None = None
    error: str | None = None
    rate_limited: bool = False
    total_cost_usd: float = 0.0


def _build_argv(task: WorkerTask) -> list[str]:
    """`allowed_tools` is a per-leg grant, never inherited (sdk-workflow). An
    EMPTY grant must produce an EXPLICIT deny-all, not an omitted flag: a
    live smoke probe with no `--allowedTools` on the argv let the CLI fall
    back to its permissive default and the worker went off-prompt doing a
    live web search — `--tools ""` is the confirmed hard "no tools visible
    to the model at all" flag (`claude --help`), stronger than an empty
    `--allowedTools` allow-list which only scopes which of the DEFAULT
    tools are usable and does not itself remove tool availability.

    Both `--tools` and `--allowedTools` are VARIADIC (`<tools...>` per
    `claude --help`) — passed as a separate `["--flag", value]` pair they
    greedily consume the NEXT argv token too, swallowing the trailing
    prompt positional and failing with "Input must be provided either
    through stdin or as a prompt argument" (confirmed live: both the
    empty-string deny-all and a real comma-joined allow-list reproduce
    it). The `--flag=value` single-token form is the only form confirmed
    NOT to swallow the following positional — required, not cosmetic."""
    argv = ["claude", "--print", "--output-format", "json", "--model", task.model]
    if task.allowed_tools:
        argv.append("--allowedTools=" + ",".join(task.allowed_tools))
    else:
        argv.append("--tools=")
    argv.append(task.prompt)
    return argv


def run_worker(task: WorkerTask, *, claude_bin: str = "claude") -> WorkerResult:
    """Run one headless claude -p leg. Fail-loud on rc!=0 or non-JSON stdout
    (sdk-workflow) — never a silent retry or a salvage parse at this layer."""
    argv = _build_argv(task)
    argv[0] = claude_bin
    start = time.monotonic()
    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True, cwd=task.cwd, timeout=task.timeout_s
        )
    except subprocess.TimeoutExpired as exc:
        duration_ms = int((time.monotonic() - start) * 1000)
        return WorkerResult(task.task_id, False, duration_ms, error=f"timeout: {exc}")
    duration_ms = int((time.monotonic() - start) * 1000)

    if proc.returncode != 0:
        stderr = proc.stderr[:2000]
        rate_limited = any(sig in stderr.lower() for sig in _RATE_LIMIT_SIGNALS)
        return WorkerResult(
            task.task_id, False, duration_ms,
            error=f"rc={proc.returncode}: {stderr[:500]}", rate_limited=rate_limited,
        )

    try:
        envelope = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return WorkerResult(
            task.task_id, False, duration_ms,
            error=f"non-JSON envelope: {proc.stdout[:500]}",
        )

    inner_text = envelope.get("result", "")
    payload: object = inner_text
    if isinstance(inner_text, str):
        stripped = inner_text.strip()
        if stripped.startswith("```"):
            stripped = stripped.strip("`").removeprefix("json").strip()
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            payload = inner_text  # non-JSON prose result: pass through, not fatal

    return WorkerResult(
        task.task_id, True, duration_ms,
        envelope=envelope, payload=payload,
        total_cost_usd=float(envelope.get("total_cost_usd", 0.0) or 0.0),
    )


def run_pool(
    tasks: list[WorkerTask], *, max_workers: int, claude_bin: str = "claude"
) -> list[WorkerResult]:
    """Spawn up to `max_workers` concurrent claude -p legs over `tasks`."""
    if not tasks:
        return []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(run_worker, t, claude_bin=claude_bin) for t in tasks]
        return [f.result() for f in futures]


def _cli() -> None:
    parser = argparse.ArgumentParser(prog="python -m broker.conductor.pool")
    parser.add_argument("--n", type=int, required=True, help="pool size (worker count)")
    parser.add_argument("--prompt", required=True, help="prompt for every worker")
    parser.add_argument("--cwd", default=".", help="cwd for every worker (governance)")
    parser.add_argument("--allowed-tools", default="", help="comma-separated allowedTools")
    parser.add_argument("--model", default="sonnet")
    parser.add_argument("--claude-bin", default="claude")
    args = parser.parse_args()

    allowed = [t for t in args.allowed_tools.split(",") if t]
    tasks = [
        WorkerTask(
            task_id=f"w{i}", prompt=args.prompt, cwd=args.cwd,
            allowed_tools=allowed, model=args.model,
        )
        for i in range(args.n)
    ]
    results = run_pool(tasks, max_workers=args.n, claude_bin=args.claude_bin)
    print(json.dumps([dataclasses.asdict(r) for r in results]))


if __name__ == "__main__":
    _cli()
