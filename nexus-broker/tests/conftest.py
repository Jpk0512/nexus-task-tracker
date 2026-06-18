"""Root broker test config — OPT-053 non-empty-collection gate.

THE FALSE-GREEN TRAP this closes: `pyproject.toml` declares
`testpaths = ["tests"]`. If that directory is ever empty, renamed, or a glob
silently matches nothing, pytest collects ZERO tests and exits 0 — a green run
that proves nothing. OPT-051 fixed the testpaths declaration; this gate is the
belt-and-braces partner: it turns "0 tests collected" from a silent green into a
HARD ERROR, so the suite can never again pass by collecting nothing.

Implementation: `pytest_collection_finish` fires once per session AFTER the full
collection — including every `pytest_collection_modifyitems` deselection (-k/-m
keyword/mark filtering runs there). `session.items` at that point is the final
selected set, so an empty `session.items` is the genuine 0-collected condition.
(`pytest_collection_modifyitems` is the WRONG hook for this: this plugin's impl
can run BEFORE the builtin -k deselection, seeing a pre-filter non-empty list.)
When the final set is empty we raise `pytest.UsageError`, which aborts the
session with a non-zero exit and a clear message — never a green exit.

NON-INTERFERENCE with normal runs:
  * A normal full run collects 200+ items → the branch never triggers.
  * `--collect-only` introspection is exempt (it is a deliberate inspection, not
    a verification run that could be mistaken for green).
  * The gate is opt-OUTable for the gate's OWN self-test via the
    `--allow-empty-collection` flag this conftest registers, so the test that
    proves the gate fires on an empty selection (-k __nonexistent__) can also
    prove the escape hatch leaves an empty run un-erroring. Production CI never
    passes that flag, so the trap stays armed.
"""
from __future__ import annotations

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    """Register the gate's own escape hatch.

    Only the gate's self-test passes --allow-empty-collection; it exists so the
    test suite can assert BOTH directions (armed → errors on empty; disarmed →
    tolerates empty) without the gate aborting its own verification.
    """
    parser.addoption(
        "--allow-empty-collection",
        action="store_true",
        default=False,
        help=(
            "OPT-053 escape hatch: tolerate a 0-test collection instead of "
            "erroring. For the non-empty-collection gate's self-test only — "
            "never pass this in CI."
        ),
    )


def pytest_collection_finish(session: pytest.Session) -> None:
    """OPT-053: fail the session loudly when 0 tests were collected.

    Fires after the WHOLE collection (post -k/-m deselection), so an empty
    `session.items` means the session genuinely has nothing to run — the precise
    false-green condition we refuse to let exit 0.
    """
    if session.items:
        return  # the common case — never interfere with a populated run

    config = session.config
    # `--collect-only` is an explicit inspection, not a verification run; and the
    # gate's own self-test disarms via --allow-empty-collection.
    if config.getoption("--collect-only", default=False):
        return
    if config.getoption("--allow-empty-collection", default=False):
        return

    raise pytest.UsageError(
        "OPT-053 non-empty-collection gate: 0 tests were collected. A green exit "
        "here would be a FALSE PASS (the silent-zero trap). Check that "
        "testpaths/-k/-m actually select tests. To intentionally allow an empty "
        "run (gate self-test only), pass --allow-empty-collection."
    )
