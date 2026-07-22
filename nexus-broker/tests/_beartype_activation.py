"""NEX-002: beartype runtime type contracts for the broker package.

Least-invasive activation via `beartype.claw.beartype_package` — an import
hook, not per-function decoration, so every annotated `broker.*` callable gets
its parameter/return types enforced at call time without touching a single
call site. MUST run before any `broker.*` submodule is first imported in this
interpreter: the claw only instruments modules imported AFTER the hook is
installed, so tests/conftest.py calls `activate()` before its own first
broker.*-touching fixture/import — the handful of stdlib/pytest imports ahead
of that call never reach broker.*, so ordering is preserved.

Scope is deliberately per-dotted-module rather than a single package-wide
call: `_ACTIVATED_MODULES` lists exactly the broker submodules covered.
Starting narrow at the DEC-100 pillar-5 high-value surface (capability-token
mint/verify, the broker-gate brief validator) rather than the whole ~85-module
`broker` package keeps this a proof of the contract, not a package-wide
type-debt audit — the latter is real follow-up work, not this task's scope.
"""
from __future__ import annotations

from beartype import BeartypeConf
from beartype.claw import beartype_package

# Dotted broker submodules to instrument. Each entry gets its own
# `beartype_package` call (the claw hooks are additive) so one module's
# pre-existing type debt can be dropped from this tuple without touching the
# others — narrowing the net without an extra exclusion mechanism.
_ACTIVATED_MODULES: tuple[str, ...] = (
    "broker.capability_token",
    "broker.server",
)


def activate() -> None:
    for module_name in _ACTIVATED_MODULES:
        beartype_package(module_name, conf=BeartypeConf())
