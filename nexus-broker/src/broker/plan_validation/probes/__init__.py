"""Plan-validation gate — opt-in probes (R3-T04, node N09).

Three probe modules, wired together only through `run_probes` in `gate.py`:

  * `citation`      — every leaf's context_files entries actually exist on disk.
  * `stub_mutation`  — mutate one leaf to a stub, prove the probe MUST fail
                       (falsifiability: a check that can never fail is not a gate).
  * `diversity`      — K=2 structural-divergence between two plan samples.
                       Model-judged: the caller supplies the second sample
                       (produced elsewhere by a model != the plan's own
                       planner — the N01 rule is enforced as a hard runtime
                       check inside `diversity.check_k2_diversity`, never as a
                       live model call made by this package).

OPT-IN (SPEED, C2, binding): these probes run ONLY when
`gate.gate_requires_probes(doc)` is True (max risk_tier T2, or any node
irreversible:true) or the caller passes `force=True`. `gate.run_probes`
imports each probe submodule LAZILY, inside the branch that fires only when
that condition holds — the default invocation (N08's deterministic core
alone) never imports `citation`, `stub_mutation`, or `diversity`.

DETERMINISTIC: no module in this package imports a network/model client
library. The diversity probe's "second sample" is caller-supplied data, not a
live call made from here.
"""
from __future__ import annotations

from broker.plan_validation.probes.gate import gate_requires_probes, run_probes

__all__ = ["gate_requires_probes", "run_probes"]
