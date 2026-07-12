"""`python -m broker.conductor` — the production conductor entrypoint CLI
(R4-T03/N34, plans/14-cutover-activation-plan.md SS4).

    python -m broker.conductor run <dag.yaml>      # validate + dispatch a DAG
    python -m broker.conductor tenant verify-matrix  # the standing verify-matrix job

Both subcommands route through `broker.conductor.entry` and append one line
to `.memory/files/conductor_runs.jsonl` per run. A DAG failing
`broker.node_contract` validation is refused (nonzero exit, printed to
stderr) BEFORE any dispatch and before any journal write.

AVAILABILITY GATE (`conductor.enabled`, DEC-056): the conductor is a
SELECTIVE, opt-in engine — NEVER the default execution path for general
dispatch. Both subcommands refuse (exit 2, clear stderr message, zero
dispatch side effects, no journal write) unless the repo-root
`.claude/conductor.enabled` flag file exists. To enable and run an advanced
DAG on-demand:

    touch .claude/conductor.enabled && python -m broker.conductor run <dag.yaml>

Importing this module (`import broker.conductor.__main__`) must NEVER itself
invoke the CLI/argparse or any dispatch — only running it as `__main__` does
(the `if __name__ == "__main__":` guard below), same convention as
`broker.node_contract`, `broker.conductor.pool`, and `broker.conductor.ramp`.
"""
from __future__ import annotations

import argparse
import json
import sys

from broker.conductor import dag as dag_mod
from broker.conductor import entry


def _cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m broker.conductor")
    sub = parser.add_subparsers(dest="cmd", required=True)

    run_p = sub.add_parser("run", help="validate + dispatch a node-contract DAG file")
    run_p.add_argument("dag_path")
    run_p.add_argument("--max-workers", type=int, default=2)
    run_p.add_argument("--cwd-root", default=".")
    run_p.add_argument("--claude-model", default="sonnet")
    run_p.add_argument("--claude-bin", default="claude")
    run_p.add_argument("--codex-bin", default="codex")

    tenant_p = sub.add_parser("tenant", help="run a standing conductor tenant job")
    tenant_p.add_argument("name", choices=["verify-matrix"])
    tenant_p.add_argument("--cwd", default=".")
    tenant_p.add_argument("--claude-bin", default="claude")
    tenant_p.add_argument("--label", default=None)

    args = parser.parse_args(argv)

    if args.cmd == "run":
        try:
            outcome = entry.run_dag_entry(
                args.dag_path, max_workers=args.max_workers, cwd_root=args.cwd_root,
                claude_model=args.claude_model, claude_bin=args.claude_bin, codex_bin=args.codex_bin,
            )
        except entry.ConductorDisabledError as exc:
            print(f"REFUSED: {exc} — no dispatch:", file=sys.stderr)
            return 2
        except dag_mod.DagValidationError as exc:
            print(f"REFUSED: {args.dag_path} failed node-contract validation — no dispatch:", file=sys.stderr)
            for e in exc.errors:
                print(f"  {e!r}", file=sys.stderr)
            return 1
        print(json.dumps({k: v for k, v in outcome.items() if k != "result"}))
        return 0 if outcome["status"] == "ok" else 1

    # cmd == "tenant" (only "verify-matrix" is registered above)
    try:
        outcome = entry.run_verify_matrix_entry(cwd=args.cwd, claude_bin=args.claude_bin, run_label=args.label)
    except entry.ConductorDisabledError as exc:
        print(f"REFUSED: {exc} — no dispatch:", file=sys.stderr)
        return 2
    print(json.dumps({k: v for k, v in outcome.items() if k != "result"}))
    return 0 if outcome["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(_cli())
