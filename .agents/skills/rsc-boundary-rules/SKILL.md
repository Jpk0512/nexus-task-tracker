---
name: rsc-boundary-rules
description: "INTERNAL — invoke by explicit name only via `Skill rsc-boundary-rules`. Do NOT auto-load. React Server Component vs client boundary rules, serialization constraints, data flow discipline."
---

# RSC Boundary Rules (canonical for `app/`)

## Default: Server Component

Every file in `app/` is a Server Component unless it has `'use client'` at the top. This is the default — do not add `'use client'` without a documented reason.

## When `'use client'` is required

Add it ONLY for:
- `useState`, `useEffect`, `useRef`, `useContext`, or any React hook
- Browser-only APIs (`window`, `document`, `navigator`)
- Tremor interactive components (tabs, selects, dropdowns, animated charts)
- Event handlers (`onClick`, `onChange`) that are non-trivial (trivial handlers can stay in RSC via Server Actions)

Always add a 1-line comment above the directive:
```tsx
// useSearchParams requires a client boundary
'use client'
```

## Serialization constraints

Props passed from Server to Client Components must be serializable:
- Primitives, plain objects, arrays: OK
- `Date` objects: OK (serialized as ISO string)
- Class instances, functions, Symbols: NOT OK — convert to plain data in the RSC
- Never pass DuckDB row objects directly — map to plain typed objects first

## Data fetching discipline

- DB queries (DuckDB) live in Server Components or Server Actions only.
- Client Components receive data as props — never trigger a DB fetch from a client component.
- `fetch()` in Server Components: use `cache: 'no-store'` for live data; `next: { revalidate: N }` for ISR.

## Server Actions

- File must have `'use server'` directive (or individual function exported with it).
- Validate all input at the action boundary with Zod or explicit guards.
- Actions live in `app/actions/<name>.ts` or co-located `_actions.ts` within a route segment.
- Return typed results, not raw DB rows.

## Forbidden

- `async` Client Components — not supported in React 18/19 RSC model.
- Importing Server-only modules (DuckDB driver, `fs`, env-var reads) into Client Components.
- `'use client'` at the top of a `page.tsx` unless the entire page is purely interactive (rare).
