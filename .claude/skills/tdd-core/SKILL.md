---
name: tdd-core
description: "The single home of stub-authoring rules — Constitution Article I (stubs before code), the valid/invalid stub anatomy, the xfail(strict=True) ban, the split-workflow two-phase rule, and the real-data-shape fixtures rule. Use when authoring a NEW not-yet-implemented test stub in any language, deciding single-agent vs split-workflow stub shape, or reviewing whether a stub is HARD-RED. Do NOT use for generic framework mechanics (parametrize, fixture scoping, conftest layout, RTL query priority, respx/httpx mocking) — those are training-known and deliberately undocumented (D7), not restated anywhere."
metadata: {tier: sonnet, token_budget: 1200, injectable: true}
---

# TDD Core

## When this fires

Authoring any not-yet-implemented test stub (Python or TS), deciding whether a
stub should be a bare failing placeholder or a complete real-assertion RED test,
or reviewing a stub for HARD-RED validity before it's allowed past the planning
gate. Generic test mechanics (parametrize, fixture scoping, conftest layout,
RTL query priority, `userEvent`, respx/httpx mocking) are NOT here — a capable
frontier model produces those unprompted; they are training-known and
deliberately undocumented (D7).

## Rules

- **Constitution Article I:** before any implementation lands, failing test
  stubs pin every acceptance criterion in GWT format. A spec without stubs
  cannot proceed past the planning gate.
- **A valid stub is HARD-RED:** imports the not-yet-existing module via the
  eventual final path, asserts the expected shape with real types (no `as
  any`), and produces a real FAIL exit — `pytest.fail("stub — not yet
  implemented")` in Python, `test.fails(...)` in Vitest/TS. Both are real RED
  that the implementer REPLACES with the real assertion at GREEN. Capture the
  verbatim FAIL output as the proof the stub is real, not a no-op. See
  `examples/stub-pairs.md` for the full valid/invalid pairs.
- **`xfail(strict=True)` is BANNED for not-yet-implemented stubs.** It inverts
  to `XPASS(strict)=FAIL` the moment the code lands and the marker is not
  hand-stripped — exactly the failure mode behind incidents **OPT-030** and
  **OPT-040**. Reserve `xfail` ONLY for genuinely-and-permanently-expected
  failures with a real bug/migration reason string — never "not yet
  implemented", "stubs phase", "stub —", or any stub-phase placeholder. A
  mechanical guard (`test_no_stub_xfail.py`) enforces this and FAILs the suite
  if a stub-reason xfail is detected.
- **Split-workflow rule (author ≠ implementer):** when a different persona
  writes the production code, the RED stub MUST be a COMPLETE Given-When-Then
  test with REAL assertions on the intended behavior — it fails ONLY because
  the code is absent, and goes GREEN automatically once the implementer lands
  the code (nobody touches the test after). Do NOT write a bare
  `pytest.fail("stub")` with no body in this mode: the implementer's boundary
  is the source files, so they cannot fill it in and it stays red forever.
  Reserve the bare placeholder ONLY for single-agent TDD where the same author
  returns to write the body.
- **Real data shapes, not magical mocks:** fixtures match what production
  actually emits — same fields, same types, same shape. For external APIs,
  record a real response once, scrub secrets, save as
  `tests/fixtures/<api>-<scenario>.json`; tests assert against that. Do not
  mock the boundary the test exists to validate (e.g. a shell-safety test must
  not stub `child_process` to return success).
- **No mocking the analytics DB:** mocking external services is fine; mocking
  the database is not. Use a real in-memory instance (e.g. an analytics DB
  opened against `:memory:`, schema loaded from the project's schema file) or
  a fixture DB. A test that mocks a DB connection/cursor to return canned rows
  isn't exercising the query production actually runs — schema drift, join
  errors, and type-coercion bugs all pass a mocked-DB test and fail in prod.
- **Assert properties, not prose (DEC-068).** For a test that validates a
  GENERATED ARTIFACT (rendered doc, rendered agent file, a build/snapshot
  output, a templated config) — assert the STRUCTURAL PROPERTY the artifact
  must hold (a section header exists, a placeholder token is fully resolved,
  a JSON block parses and has the required keys, byte-for-byte parity between
  two rendered copies), never a brittle substring match on the exact prose
  wording. Prose changes on every rewrite; the property is what the artifact
  actually promises. This is the authoring-side twin of the Lens verify
  test-quality criterion in `Skill verification` (flags implementation-coupled
  assertions on review) — write the property-assertion up front and there is
  nothing for that criterion to flag later.

## Article I contract-split (DEC-068)

The stubs-first sequencing in Article I applies AS WRITTEN to **split-workflow**
work (a different persona implements the code after the stub lands) — the RED
stub is the CONTRACT the implementer codes to, so it must exist, complete and
real-asserting, before implementation starts.

**Single-agent legs may use vertical tracer-bullet cycles instead.** When the
SAME agent authors both the stub and the implementation in one dispatch, a
strict "all stubs first, then all implementation" ordering is not required —
the agent may cycle test-then-code one vertical slice at a time (stub A → RED
→ implement A → GREEN → stub B → RED → implement B → GREEN → …), as long as
every slice individually passes through a real RED before its GREEN. What
Article I actually forbids is skipping RED, not a particular batching of
stubs vs. code within a single author's own dispatch.

## Optional refactor-after-GREEN phase (DEC-068)

Once all stubs for a leg are GREEN, an OPTIONAL refactor pass may follow —
tightening naming, removing duplication, extracting a shared helper — with
the constraint that the test suite must STAY green throughout (re-run after
each refactor step, not just at the end) and no assertion may be loosened or
removed to make refactored code pass. This phase is optional, not a new gate:
skip it when the GREEN implementation is already clean.

## Two phases (split-workflow)

**Phase 1 — Stubs (before implementation):** author the stubs, run them,
confirm they FAIL with verbatim output in `verification_result`, return
`## NEXUS:DONE` (the phase-1 done condition).

**Phase 2 — Verification (after implementation):** re-run the same stubs, all
must PASS, capture verbatim PASS output. Return `## NEXUS:DONE` only if every
stub passes; `## NEXUS:REVISE` otherwise.

## Worked example

```python
# VALID — HARD-RED, split-workflow (author != implementer)
def test_search_ranks_by_cosine() -> None:
    from app.search import rank_results  # eventual final path, not-yet-existing
    results = rank_results(query="alpha", candidates=FIXTURE_CANDIDATES)
    assert [r.id for r in results] == ["c3", "c1", "c2"]  # real assertion, real shape
```
Run it now: it fails with `ModuleNotFoundError: No module named 'app.search'` —
capture that verbatim as the RED proof. Nobody edits this test again; it goes
GREEN unattended once `app/search.py` lands. See `examples/stub-pairs.md` for
the matching INVALID pairs and the single-agent bare-placeholder variant.

## Decision path

| Situation | What to do |
|---|---|
| Same author writes both the stub and the eventual implementation (single-agent TDD) | Bare placeholder is acceptable: `pytest.fail("stub — not yet implemented")` / `test.fails(...)`. |
| A DIFFERENT persona will implement the code (split-workflow) | The stub MUST be a complete Given-When-Then test with REAL assertions — a bare placeholder stays red forever because the implementer's boundary is source files, not tests. |
| A failure is genuinely-and-permanently expected (real bug/migration reason) | `xfail` is allowed WITH a real reason string. |
| A failure exists only because the stub-phase code isn't written yet | `xfail(strict=True)` is BANNED — it flips to `XPASS(strict)=FAIL` the moment the code lands (OPT-030/OPT-040 failure mode). Use a real FAIL/`pytest.fail`, not xfail. |
| Test needs a fixture for an external API | Record a real response once, scrub secrets, save to `tests/fixtures/<api>-<scenario>.json` — never a magical/hand-invented mock shape. |
| Test needs to exercise a database | Use a real in-memory instance or fixture DB — never mock the DB connection/cursor to return canned rows. |
| Currently in Phase 1 (before implementation) | Author stubs, run them, confirm verbatim FAIL, return `## NEXUS:DONE` for phase 1. |
| Currently in Phase 2 (after implementation lands) | Re-run the SAME stubs; all must PASS with verbatim output — `## NEXUS:DONE` only if every stub passes, else `## NEXUS:REVISE`. |
| Testing a generated artifact (rendered doc/agent/config, build output) | Assert the structural PROPERTY it must hold, never a brittle prose/substring match (DEC-068 "assert properties, not prose"). |
| Single agent authors both stub and implementation, one vertical slice at a time | Vertical tracer-bullet cycles are allowed (stub→RED→implement→GREEN per slice) — Article I only forbids skipping RED, not a particular stub-vs-code batching (DEC-068). |
| All stubs for a leg are GREEN and the code could be tightened | An OPTIONAL refactor-after-GREEN pass is allowed — tests must stay green throughout, no assertion loosened (DEC-068). |

Default when none of the rows match: treat the work as split-workflow (the safer assumption) — write a complete Given-When-Then stub with real assertions, never a bare placeholder.

## References

- `examples/stub-pairs.md` — read before writing ANY test stub; the full
  valid/invalid pairs for both single-agent and split-workflow TDD.
