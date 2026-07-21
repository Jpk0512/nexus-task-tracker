# Stub Pairs — valid vs invalid

Copyable pairs for the two stub modes `tdd-core` distinguishes: **single-agent**
(same author returns to fill in the body) and **split-workflow** (a different
persona/agent implements the production code and never touches the test).

## Split-workflow stubs (author != implementer)

The RED stub is a COMPLETE Given-When-Then test with a real assertion on the
intended behavior. It fails only because the code doesn't exist yet, and goes
GREEN automatically once the implementer lands the code — nobody edits the
test after this point.

**VALID — Python:**
```python
def test_search_ranks_by_cosine() -> None:
    from app.search import rank_results  # eventual final path, not-yet-existing
    results = rank_results(query="alpha", candidates=FIXTURE_CANDIDATES)
    assert [r.id for r in results] == ["c3", "c1", "c2"]
```
Run now: fails with `ModuleNotFoundError: No module named 'app.search'`. That
verbatim FAIL is the RED proof. The implementer writes `app/search.py`; this
test goes GREEN with no edits.

**VALID — TypeScript / Vitest:**
```ts
test("ranks results by cosine similarity", () => {
  const { rankResults } = require("../src/search"); // not-yet-existing module
  const results = rankResults("alpha", FIXTURE_CANDIDATES);
  expect(results.map((r) => r.id)).toEqual(["c3", "c1", "c2"]);
});
```
Run now: fails with a module-resolution error naming `../src/search`. Capture
that verbatim output as the RED proof.

**INVALID — bare placeholder in split-workflow mode:**
```python
def test_search_ranks_by_cosine() -> None:
    pytest.fail("stub — not yet implemented")
```
Wrong for split-workflow: the implementer's write boundary is the source
files, not the test files — they cannot fill this body in, so it stays red
forever. (Bare placeholders like this are reserved for single-agent mode,
below.)

## Single-agent stubs (same author returns to write the body)

**VALID — Python:**
```python
def test_search_ranks_by_cosine() -> None:
    pytest.fail("stub — not yet implemented")
```

**VALID — TypeScript / Vitest:**
```ts
test.fails("ranks results by cosine similarity", () => {
  // filled in by the same author once app/search.ts exists
});
```
Both produce a real FAIL exit today, which the SAME author later replaces with
the real assertion at GREEN. This shape is invalid the moment a different
persona owns implementation — use the split-workflow shape above instead.

## Invalid stubs (either mode)

- `test('it works', () => { expect(true).toBe(true); })` — no-op; never fails,
  proves nothing.
- `expect(result).toEqual(something as any)` — shape is unenforced; `as any`
  defeats the type check the stub exists to pin.
- A stub that throws "module not found" *before reaching the assertion* when
  the intent was a single-agent bare placeholder — that's an accidental
  import-path typo, not a meaningful failure; fix the path.
- `@pytest.mark.xfail(strict=True)` used for a not-yet-implemented stub — BANNED.
  See the OPT-030/040 scar below.

## The xfail(strict=True) ban — OPT-030 / OPT-040 scar

**Never use `@pytest.mark.xfail(strict=True)` to mark a not-yet-implemented
stub.** The mechanism: `xfail(strict=True)` inverts to `XPASS(strict) = FAIL`
the instant the real implementation lands and makes the test pass — but if the
`xfail` marker is not hand-stripped at that moment, the suite now reports a
FAIL on a test that is actually passing correctly. This is the exact failure
mode behind incidents **OPT-030** and **OPT-040**: implementations that were
correct and green got reported red because a stub-era `xfail(strict=True)`
marker outlived the stub phase.

Reserve `xfail` ONLY for genuinely-and-permanently-expected failures carrying
a real bug/migration reason string — never "not yet implemented", "stubs
phase", "stub —", or any other stub-phase placeholder reason. A mechanical
guard (`test_no_stub_xfail.py`) enforces this and FAILs the suite if a
stub-reason `xfail` is detected.

## Analytics-DB fixture pattern (no mocking the database)

Mocking external services is fine; mocking the database is not. Use a real
in-memory analytics-DB instance or a fixture DB — never a mocked
connection/cursor returning canned rows, which lets schema drift, join
errors, and type-coercion bugs pass the test and fail in production.

```python
import pytest
# `db_module` here stands in for whichever analytics-DB client the project pins —
# connect to a real in-memory instance, not a mock.

@pytest.fixture
def db():
    conn = db_module.connect(":memory:")
    conn.execute(open("ingestion/src/schema.sql").read())  # or a relevant slice
    # seed fixtures
    yield conn
    conn.close()
```
