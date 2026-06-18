---
name: vitest-rtl-idioms
description: "INTERNAL — invoke by explicit name only via `Skill vitest-rtl-idioms`. Do NOT auto-load. Vitest + React Testing Library idioms: render, userEvent, queries, async patterns, test.fails() stubs."
---

# Vitest + React Testing Library Idioms (canonical for `app/__tests__/**`)

## File structure

```ts
// app/__tests__/<feature>.test.tsx
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, it, expect, test } from 'vitest'
import { MyComponent } from '@/app/_components/MyComponent'
```

## Query priority (RTL best practice)

1. `getByRole` — most resilient, tests accessibility
2. `getByLabelText` — for form fields
3. `getByText` — for static text
4. `getByTestId` — last resort; add `data-testid` only when semantic queries fail

## User events

Use `@testing-library/user-event` v14, not `fireEvent`:

```ts
const user = userEvent.setup()
await user.click(screen.getByRole('button', { name: /submit/i }))
await user.type(screen.getByLabelText('Name'), 'Alice')
```

## Async patterns

```ts
await waitFor(() => expect(screen.getByText('Loaded')).toBeInTheDocument())
// or for async rendering:
await screen.findByText('Loaded')  // shorthand for waitFor + getBy
```

## Stubs-first with test.fails()

```ts
test.fails('renders the new metric card', async () => {
  render(<MetricCard value={42} label="Sessions" />)
  expect(screen.getByRole('heading', { name: /sessions/i })).toBeInTheDocument()
})
```

`test.fails()` marks the test as expected-to-fail during the stubs phase. Remove the `.fails()` modifier once the component is implemented.

## Mocking Server Components / RSC

RSC can't render in Vitest directly. Options:
1. Extract pure client logic into a separate component and test that.
2. Mock the RSC module: `vi.mock('@/app/page', () => ({ default: MockPage }))`.

Never test RSC server data-fetching logic in Vitest — use integration tests or playwright for that.

## DuckDB in tests

Don't mock DuckDB — use an in-memory fixture. See `duckdb-test-shims` skill.

## Forbidden

- `as any` in test assertions.
- `toMatchSnapshot()` against a freshly-generated baseline.
- `fireEvent` instead of `userEvent` for user interactions.
- Testing implementation details (internal state, private methods).
