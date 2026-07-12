---
name: forge-ui-conventions
description: "INTERNAL variant — Next.js App Router + shadcn/ui + Tailwind. forge-ui stack pin, RSC/client boundary rules, file layout, visual verification gate."
---

# Forge-UI Conventions — Next.js + shadcn/Tailwind variant

Canonical for the dashboard app's `src/components/**` and route files.

## Stack pin

- **Next.js 15 App Router.** No Pages Router code. Server Components default; `'use client'` only with a documented reason in a 1-line comment above the directive.
- **TypeScript strict.** `tsconfig.json` `strict: true`. No `any`. Generics over type assertions. Discriminated unions over boolean flags.
- **shadcn/ui** components (Radix primitives + `class-variance-authority`) on **Tailwind CSS 4**. Components are copied into `src/components/ui/` and owned by the repo — edit them in place; do not treat them as an external package.
- **Tests:** Vitest + React Testing Library + Jest-DOM.

## Server vs client (RSC)

- Server Component is the default. Data fetching and env-var reads live in Server Components or server actions.
- `'use client'` is required for: hook-based interactivity (`useState`, `useEffect`), shadcn interactive primitives that manage open/close state (Dialog, Popover, DropdownMenu), event handlers.
- Pass server-rendered data DOWN as props; never re-fetch in the client.

## shadcn / Tailwind conventions

- Compose with the `cn()` helper (`clsx` + `tailwind-merge`) for conditional classes; never concatenate class strings by hand.
- Variants via `cva()`. Keep design tokens in the Tailwind theme / CSS custom properties — no hard-coded hex in components.
- Prefer semantic Radix-backed components over raw `<div>` for interactive widgets (accessibility comes for free).

## File layout

- Pages: `src/app/<route>/page.tsx` (RSC by default).
- shadcn primitives: `src/components/ui/<name>.tsx`.
- Feature components: `src/components/<feature>/<Name>.tsx`.

## Read-before-edit + edit-budget rules

- Re-read a file before any Edit. Re-read after any other tool changed it.
- Cap any single Read at 2,000 lines; for files >500 LOC use `offset` + `limit`.
- Never batch >3 edits to the same file without an interleaved Read.

## Verification gate (`## NEXUS:DONE` requires)

```bash
rtk tsc        # full type-check
rtk lint       # eslint, no warnings ignored
```

Both verbatim-passing in `verification_result`.

## Code rules (project)

- No comments unless the WHY is non-obvious.
- No error handling for impossible paths. Validate only at system boundaries.
- No backwards-compat shims when you remove code. Delete fully.

## Visual verification gate (Mandatory Discipline 2026-05-13)

- UI changes require `aside` before+after screenshots in your response (load `Skill aside-browser`; use `Bash(aside:*)`). Gate enforced by `visual-evidence-gate.sh` — accountable-skip via `verification_result.visual_skip_reason`.
- Class-string assertions in vitest do NOT satisfy this — they prove source shape, not rendered behavior.

## Deploy step (always)

End every implementation response with `## Deploy step` containing an HMR/restart action targeting the current session-branch HEAD.

No branch line — the block targets the session-branch HEAD, not a feature branch.

## Forbidden writes

Backend (`apps/api/**`), `docker-compose*.yml`, `Caddyfile`, `.memory/`, `.Codex/`, anywhere outside the repo.
