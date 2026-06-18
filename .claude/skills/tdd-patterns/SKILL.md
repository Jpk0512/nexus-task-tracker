---
name: tdd-patterns
description: Test-first / stubs-first patterns for Quill. Constitution Article I enforcement, real-data-shape fixtures rule, in-memory DuckDB pattern, Vitest + RTL idioms, pytest idioms, coverage-threshold gate. Preloaded into Quill; useful for any test author.
---

# TDD Patterns (Quill-canonical)

## Constitution Article I — stubs first, code second

Before any implementation lands, failing test stubs pin every acceptance criterion in GWT format. A spec without stubs cannot proceed past the planning gate.

### Anatomy of a valid stub

A valid stub is HARD-RED:
1. Imports the not-yet-existing module via the eventual final path
2. Asserts the expected shape with real types — NO `as any`
3. Produces a real FAIL exit: `pytest.fail("stub — not yet implemented")` in Python; `test.fails(...)` in Vitest (TS). Both are real RED that the implementer REPLACES with the real assertion at GREEN.
4. Captured verbatim FAIL output → that's the proof the stub is real, not a no-op

**WARNING — do NOT use `@pytest.mark.xfail(strict=True)` for not-yet-implemented stubs.**
`xfail(strict=True)` inverts to `XPASS(strict)=FAIL` the moment the code lands and the marker is not hand-stripped (exactly the failure mode in OPT-030 and OPT-040). Reserve `xfail` ONLY for genuinely-and-permanently-expected failures with a real bug/migration reason string — never "not yet implemented", "stubs phase", "stub —", or any stub-phase placeholder. A mechanical guard (`test_no_stub_xfail.py`) enforces this and will FAIL the suite if a stub-reason xfail is detected.

**SPLIT-WORKFLOW RULE (quill→implementer TDD):** when a DIFFERENT persona writes the production code, your RED stub MUST be a COMPLETE Given-When-Then test with REAL assertions on the intended behavior — it fails ONLY because the code is absent, and goes GREEN automatically once the implementer lands the code (nobody touches the test). Do NOT write a bare `pytest.fail("stub")` with no body: the implementer's boundary is the source files, so they cannot fill it and it stays red forever. Reserve bare `pytest.fail()` placeholders ONLY for single-agent TDD where Quill itself returns to write the body.

**Invalid stubs:**
- `test('it works', () => { expect(true).toBe(true); })` — no-op
- `expect(result).toEqual(something as any)` — shape is unenforced
- Stubs that throw "module not found" before reaching the assertion — that's not a meaningful failure

## Two phases

**Phase 1 — Stubs (before implementation):**
- Author the stubs.
- Run them.
- Confirm they FAIL with verbatim output in `verification_result`.
- Return `## NEXUS:DONE` (this is the phase 1 done condition).

**Phase 2 — Verification (after implementation):**
- Re-run the same stubs.
- All must PASS.
- Capture verbatim PASS output.
- Return `## NEXUS:DONE` only if every stub passes. If any fail, `## NEXUS:REVISE` to Forge/Pipeline.

## Real data shapes, not magical mocks

- Fixtures match what production actually emits — same fields, same types, same shape.
- For external APIs (Tableau, Azure-routed Anthropic, OpenAI/Azure embeddings), record a real response once, scrub secrets, save as `tests/fixtures/<api>-<scenario>.json`. Tests assert against that.
- Mocking external services is OK; **mocking the database is not.** Use in-memory DuckDB or a fixture DB.

## In-memory DuckDB pattern (Python)

```python
import duckdb
import pytest

@pytest.fixture
def db():
    conn = duckdb.connect(":memory:")
    conn.execute(open("ingestion/src/schema.sql").read())  # OR a relevant slice
    # seed fixtures
    yield conn
    conn.close()
```

## Vitest + RTL idioms

- Test file path: `app/__tests__/<feature>.test.ts(x)`.
- For RSC components, use a server-aware harness (`@testing-library/react` with `act` from React 19).
- Avoid `toMatchSnapshot()` against freshly-generated baselines you authored seconds ago. Snapshots are only valid when a HUMAN has reviewed and committed them.
- Prefer `getByRole` / `getByLabelText` (semantic) over `getByTestId` (brittle).

## pytest idioms

- Test file path: `ingestion/tests/test_<feature>.py`.
- `pytest.mark.asyncio` for async functions.
- `httpx.MockTransport` for HTTP mocking (NOT `requests-mock`).
- One concept per test; multiple assertions for the same concept are OK.

## Coverage threshold rule

Reducing a coverage threshold requires:
1. `## NEXUS:NEEDS-DECISION` to the user with the proposed delta + justification.
2. After approval: a `decision add` row with the rationale.
3. Then the threshold change lands in the same commit as the rationale link.

Silent threshold reductions are a CARDINAL violation.

## Output discipline

`acceptance_met` is per-criterion with `evidence` being the test name + status (e.g., `"test_search_ranks_by_cosine — PASS"`). The orchestrator reads this to confirm criteria are pinned to actual tests.

## Verification commands

```bash
# TS:
rtk vitest run <test_path>
# Python:
uv run pytest ingestion/tests/test_<feature>.py -v
```

Verbatim output → `verification_result`. Phase-appropriate FAIL or PASS.

## Forbidden writes (Output-Dir STRICT)

Production code under `app/`, `ingestion/`, `models/`. `vitest.config.*`, `pyproject.toml` (especially coverage thresholds — requires `decision add` first). `.memory/`. `.claude/`. Anywhere outside the repo.

---

## Mandatory Discipline (2026-05-13)

### Tests cannot mock the boundary they validate
- If the test is named `*-shell-safety.test.ts`, it MUST NOT mock `child_process`
  with a stub that returns success — that defeats the test.
- If the test is named `*-bg.test.tsx`, it MUST inspect the rendered DOM via
  RTL, not assert on `className` strings.
- Quill response MUST cite the actual boundary the test exercises and confirm
  no mock at that boundary.

### Visual gate for UI tests
- UI behavior tests should at minimum query `getBoundingClientRect()` or use
  Playwright/agent-browser. Pure class-string assertions are documentation,
  not validation.

## Lint-clean stubs rule (anti-stall — HARD)

Generated test stubs MUST pass the project's CONFIGURED lint + type-check gates BEFORE Quill returns DONE — `rtk lint` + `rtk tsc` for TypeScript stubs, `uv run ruff check` for Python stubs. Why this is load-bearing: a lint-failing stub is picked up by the implementer (Forge/Pipeline), who hits the SAME gate and REVISEs the work back to Quill — but Quill's write boundary is the test files, not the source, so neither side owns the fix and the REVISE cycle STALLS (this is the gate_revise_stall fleet pain). PREVENT IT: before marking DONE, run the project lint + type-check on the test files you generated and confirm exit 0; fix any violations in your stubs. N/A carve-out: only gates that are actually CONFIGURED for the project must pass — if the project has no lint/type-check configured, exit-0/N-A is acceptable (mirror lens-fast's configured-gates-only detection).
