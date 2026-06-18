# Persona Boundaries

Defines the cut criteria for split personas. When a brief touches both sides of a boundary, dispatch both personas as a pair (see pairing rules in TEAM.md).

---

## forge-ui ↔ forge-wire

**Cut**: the data shape returned by a server boundary.

- **forge-wire** owns everything that *produces* data at a server boundary: `app/api/**` route handlers, `app/actions/**` server actions, `app/lib/ai/**` AI SDK calls, DuckDB read queries.
- **forge-ui** owns everything that *consumes* that shape: `app/components/**`, `app/(routes)/**` RSC pages, Tremor charts, Tailwind styling, theme, motion, interaction states.

**Practical rule**: if the PR touches a `"use server"` or an `app/api/` file, that file is forge-wire's. If it touches a `.tsx` component or RSC page with no server-action body, that file is forge-ui's. Files that are both (e.g., a route segment that also renders) → dispatch both and coordinate via brief.

---

## pipeline-data ↔ pipeline-async

**Cut**: whether work is synchronous or queue/externally-triggered.

- **pipeline-data** owns synchronous data transforms and writes: `ingestion/src/transforms/**`, `ingestion/src/writers/**`, Polars pipelines, DuckDB write transactions, embedding computation.
- **pipeline-async** owns queued or externally-triggered work: `ingestion/src/workers/**` Dramatiq actors, `ingestion/src/clients/**` external API clients, Redis broker config, AI enrichment calls via @ai-sdk/anthropic.

**Practical rule**: if the work is invoked by `.send()` or a cron/webhook → pipeline-async. If it runs inline as part of a data pipeline step → pipeline-data.

---

## quill-ts ↔ quill-py

**Cut**: language stack of the code under test.

- **quill-ts** tests TypeScript/React code: Vitest + React Testing Library. Tests live in `app/__tests__/`.
- **quill-py** tests Python ingestion code: pytest + polars fixtures. Tests live in `ingestion/tests/`.

When a feature spans both stacks, dispatch both quill variants. Each owns tests for their respective layer only.

---

## Ambiguous brief escalation

If the brief content does not clearly resolve which side of a boundary owns the work, return `## NEXUS:NEEDS-DECISION` rather than guessing. The persona-alias-resolver hook also enforces this mechanically for stale persona names.
