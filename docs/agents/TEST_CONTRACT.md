# Test Contract

> Additive to `docs/agents/CONTRACT.md`. Defines Quill's scope and the test framework rules for all agents.

## Quill's Mandate

Quill writes test stubs **before** any production code exists (Constitution Article II). Stubs must:
1. Import the module under test
2. Call the function/endpoint being tested
3. Assert one observable behavior
4. **Fail** when run against a missing or empty implementation

Quill does **not** write mocks for code that can hit real infrastructure. See below.

## quill-ts vs quill-py

Quill is a split persona — dispatch the right variant for the stack under test:

| Variant | Language stack | Test directory | Framework |
|---|---|---|---|
| `quill-ts` | TypeScript / React (`app/`) | `app/__tests__/` | Vitest + React Testing Library |
| `quill-py` | Python ingestion (`ingestion/`) | `ingestion/tests/` | pytest + polars fixtures |

When a feature spans both stacks, dispatch both variants. Each owns tests for their respective layer only — `quill-ts` must not write to `ingestion/tests/` and `quill-py` must not write to `app/__tests__/`.

---

## Python Tests (ingestion/)

**Framework:** pytest  
**Runner:** `uv run pytest ingestion/tests/ -x --tb=short`  
**Coverage:** `uv run pytest ingestion/tests/ --cov=src --cov-report=term-missing`

### Thresholds

| Test type | Minimum coverage |
|---|---|
| Data pipeline (ingestion) | 80% line coverage |
| DuckDB loaders | All idempotency paths covered |
| REST API callers | Happy path + 4xx/5xx error path |

### Mock policy

| External service | Policy |
|---|---|
| External REST APIs | Mock with `respx` or `pytest-httpx` |
| DuckDB | Use in-memory instance (`duckdb.connect(':memory:')`) |
| Redis / Dramatiq | Mock broker in tests |
| Observability traces | Disable via env var (`ARIZE_TRACE_ENABLED=false` or project-specific equivalent — check `conftest.py` for the configured var name) |

### conftest.py must provide

- `tmp_duckdb` fixture: in-memory DuckDB connection with schema applied
- Sample API response fixtures matching the external service's response shape
- `monkeypatch` for env vars — the placeholder names below are per-project substitutions; replace with real var names from `.env.example`:
  - `{YOUR_API_SERVER_URL}` → e.g. `TABLEAU_SERVER_URL`
  - `{YOUR_API_TOKEN_NAME}` → e.g. `TABLEAU_PAT_NAME`

---

## TypeScript Tests (app/)

**Framework:** Vitest  
**Runner:** `rtk vitest run`  
**Config:** `app/vitest.config.ts`

### Thresholds

| Test type | Minimum coverage |
|---|---|
| API route handlers | Happy path + error branch |
| DuckDB query helpers | All exported functions |
| React components | Render without crash + key interactions |

### Mock policy

| External | Policy |
|---|---|
| DuckDB (via better-sqlite3 / duckdb-node) | Use `vi.mock` with fixture data |
| Anthropic / AI SDK | Use `vi.mock('@ai-sdk/anthropic')` |
| Next.js App Router | Use `@testing-library/react` with mocked `next/navigation` |
| Observability / tracing (Arize or project-specific) | Set `ARIZE_TRACE_ENABLED=false` (or project-equivalent) via `vi.stubEnv` in test setup; do NOT mock the tracer internals — disable at the env layer |

---

## Test Stub Templates

### Two-phase workflow

**Phase 1 — Stubs (before implementation):**

- Author stubs that FAIL hard with verbatim output in `verification_result`.
- Run them and confirm they exit non-zero.
- Return `## NEXUS:DONE` — Phase 1 is complete even though tests fail.

**Phase 2 — Verification (after implementation):**

- Re-run the same stubs against the completed implementation.
- All must PASS (exit 0).
- Return `## NEXUS:DONE` only if every stub passes; otherwise `## NEXUS:REVISE` to Forge/Pipeline.

### Split-workflow stub rule (quill → implementer TDD)

When a DIFFERENT persona writes the production code, the stub MUST be a complete
Given-When-Then test with REAL assertions — it fails only because the module is absent,
and goes GREEN automatically once the implementer lands the code. The implementer
cannot fill in a bare `pytest.fail()` placeholder (their boundary is source files, not
tests), so bare placeholders in split-workflow stubs cause permanent RED.

Reserve bare `pytest.fail("stub — implement after X exists")` ONLY for single-agent
TDD where Quill itself will return to fill the body.

### Python stub template — split-workflow (recommended)

```python
# ingestion/tests/test_external_catalog.py
"""Stubs for FEAT-001 TASK-002 — external_catalog.py"""
import pytest
from ingestion.src.external_catalog import run  # module does not exist yet — collection ERROR during Phase 1


@pytest.mark.xfail(strict=True, reason="not yet implemented: FEAT-001 TASK-002")
def test_run_produces_workbooks_parquet(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Given valid PAT credentials and a mock server,
    When run() is called,
    Then workbooks.parquet is written to the output directory."""
    monkeypatch.setenv("TABLEAU_SERVER_URL", "https://stub.example.com")
    monkeypatch.setenv("TABLEAU_PAT_NAME", "stub-pat")
    monkeypatch.setenv("TABLEAU_PAT_SECRET", "stub-secret")
    run(output_dir=tmp_path)
    assert (tmp_path / "workbooks.parquet").exists()


@pytest.mark.xfail(strict=True, reason="not yet implemented: FEAT-001 TASK-002")
def test_run_is_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Running run() twice must produce the same output (no duplicates)."""
    monkeypatch.setenv("TABLEAU_SERVER_URL", "https://stub.example.com")
    monkeypatch.setenv("TABLEAU_PAT_NAME", "stub-pat")
    monkeypatch.setenv("TABLEAU_PAT_SECRET", "stub-secret")
    run(output_dir=tmp_path)
    run(output_dir=tmp_path)
    result = pd.read_parquet(tmp_path / "workbooks.parquet")
    assert result["id"].duplicated().sum() == 0


@pytest.mark.xfail(strict=True, reason="not yet implemented: FEAT-001 TASK-002")
def test_signout_called_on_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """If _list_workbooks raises, _signout must still be called."""
    import ingestion.src.external_catalog as mod
    signout_calls: list[str] = []
    monkeypatch.setattr(mod, "_list_workbooks", lambda *_: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(mod, "_signout", lambda token: signout_calls.append(token))
    with pytest.raises(RuntimeError):
        run(output_dir=tmp_path)
    assert len(signout_calls) == 1
```

**Phase 1 behaviour (module absent):** the module-level `from ingestion.src.external_catalog import run`
causes a pytest **collection ERROR** (exit non-zero) — the `@pytest.mark.xfail` decorators on
individual functions are never evaluated because the module is never collected. This is the correct
Phase 1 failure mode: exit non-zero for the right reason.

**After implementation lands:** the module is importable, tests are collected and run. With
`xfail(strict=True)`, a test that passes produces `XPASS(strict)=FAIL` (pytest exits non-zero) until
the decorator is manually removed. **Remove `@pytest.mark.xfail` after implementation, then rerun —
tests must show `PASS` and exit 0 before Phase 2 is done.**

The reason string `"not yet implemented: FEAT-001 TASK-002"` matches the `test_no_stub_xfail.py`
guard pattern `"not yet implemented"` (case-insensitive). The guard will flag any such xfail that
outlives its implementation.

**Do NOT use `xfail` with a stub-phase reason for any other purpose.** `xfail` is reserved for the split-workflow stub phase only; after implementation, remove the decorator. A mechanical guard (`test_no_stub_xfail.py`) scans for stub-reason xfail markers that outlive their implementation — reason strings matching patterns in `STUB_REASON_PATTERNS` (e.g. `"not yet implemented"`) will be flagged. Use a matching pattern in the `reason=` argument so the guard can detect stale markers.

### TypeScript stub template

```typescript
// app/__tests__/api/workbooks.test.ts
import { describe, it, expect, vi } from "vitest";

// Module does not exist yet — import fails RED during Phase 1
import { GET } from "../../app/api/workbooks/route";

vi.mock("@ai-sdk/anthropic");
vi.mock("next/navigation");

describe("GET /api/workbooks", () => {
  it("returns workbook list from DuckDB", async () => {
    // Given: DuckDB contains seeded workbook rows
    // When: GET handler is called
    // Then: response body is an array with an id field
    const response = await GET(new Request("http://localhost/api/workbooks"));
    const body = await response.json();
    expect(Array.isArray(body)).toBe(true);
    expect(body[0]).toHaveProperty("id");
  });

  it("returns 500 on DuckDB error", async () => {
    // Given: DuckDB throws
    // When: GET handler is called
    // Then: response status is 500
    const response = await GET(new Request("http://localhost/api/workbooks"));
    expect(response.status).toBe(500);
  });
});
```

During Phase 1 the import of the non-existent route module causes a real RED failure — no need for `test.fails()` when the module is genuinely absent. If the module file exists but is a stub export, use `test.fails(async () => { ... }, { reason: "stub — not yet implemented" })` instead.

---

## Acceptance Gate

Before any task is marked `done`:

```bash
# Phase 1 (stubs): confirm tests EXIT NON-ZERO (real RED)
uv run pytest ingestion/tests/ --tb=short -q   # expected: collection ERROR (module absent)
rtk vitest run                                  # expected: failures (import errors)

# Phase 2 (after implementation): remove @pytest.mark.xfail decorators, then:
uv run pytest ingestion/tests/ --tb=short -q   # expected: all PASS
rtk vitest run                                  # expected: all pass
```

Phase 1 done condition: tests exit non-zero for the right reason (absent module causes collection
ERROR, not a lint/syntax error in the stub itself). The `@pytest.mark.xfail` decorators are present
in the stub but irrelevant during Phase 1 — the module never loads, so functions are never collected.

Phase 2 done condition: after implementation lands, **remove the `@pytest.mark.xfail` decorators**,
then rerun — every stub must show `PASS` and exit 0 from both runners. Do NOT declare Phase 2 done
while the decorators are still present: `xfail(strict=True)` on a passing test produces
`XPASS(strict)=FAIL` (pytest exits non-zero), directly contradicting the exit-0 requirement.

Stubs with lint or syntax errors are NOT valid Phase 1 stubs — fix them before returning `## NEXUS:DONE` for Phase 1.
