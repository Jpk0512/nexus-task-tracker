# Test Contract

> Additive to `docs/agents/CONTRACT.md`. Defines Quill's scope and the test framework rules for all agents.

## Quill's Mandate

Quill writes test stubs **before** any production code exists (Constitution Article II). Stubs must:
1. Import the module under test
2. Call the function/endpoint being tested
3. Assert one observable behavior
4. **Fail** when run against a missing or empty implementation

Quill does **not** write mocks for code that can hit real infrastructure. See below.

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
| Observability traces | Disable via env var (e.g. `{YOUR_TRACER}_TRACE_ENABLED=false`) |

### conftest.py must provide

- `tmp_duckdb` fixture: in-memory DuckDB connection with schema applied
- Sample API response fixtures matching the external service's response shape
- `monkeypatch` for env vars (e.g. `{YOUR_API_SERVER_URL}`, `{YOUR_API_TOKEN_NAME}`, etc.)

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

---

## Test Stub Template (Python)

```python
# ingestion/tests/test_external_catalog.py
"""Stubs for FEAT-001 TASK-002 — external_catalog.py"""
import pytest


def test_run_produces_workbooks_parquet(tmp_path, monkeypatch):
    """Given valid PAT credentials and a mock server,
    When run() is called,
    Then workbooks.parquet is written to the output directory."""
    pytest.fail("stub — implement after run() exists")


def test_run_is_idempotent(tmp_path, monkeypatch):
    """Running run() twice must produce the same output (no duplicates)."""
    pytest.fail("stub — implement after run() exists")


def test_signout_called_on_error(tmp_path, monkeypatch):
    """If _list_workbooks raises, _signout must still be called."""
    pytest.fail("stub — implement after run() exists")
```

## Test Stub Template (TypeScript)

```typescript
// app/__tests__/api/workbooks.test.ts
import { describe, it, expect } from "vitest";

describe("GET /api/workbooks", () => {
  it("returns workbook list from DuckDB", async () => {
    expect.fail("stub — implement after route exists");
  });

  it("returns 500 on DuckDB error", async () => {
    expect.fail("stub — implement after route exists");
  });
});
```

---

## Acceptance Gate

Before any task is marked `done`:

```bash
# Python
uv run pytest ingestion/tests/ --tb=short -q

# TypeScript
rtk vitest run
```

Both must exit 0 (or the stubs must exist and be confirmed failing for pre-implementation tasks).
