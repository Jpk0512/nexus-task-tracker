---
name: atlas-schema-patterns
description: "INTERNAL variant — Postgres schema design + Alembic migrations + pgvector. DDL, ALTER TABLE, HNSW/IVFFlat vector indexes, migration-doc structure."
---

# Atlas Schema Patterns — Postgres + pgvector variant

Canonical reference for schema design on Postgres. Atlas designs; Pipeline/Forge-wire executes the Alembic migration.

## Stack pin

- **PostgreSQL** as the system of record, with the **pgvector** extension for embeddings.
- **Alembic** for forward-only, reviewable migrations (managed alongside the SQLAlchemy models).
- DDL is plain SQL / SQLAlchemy DDL — `CREATE TABLE`, `ALTER TABLE`, `CREATE INDEX`.

## Bash is disabled for you

`disallowedTools: Bash`. You cannot run `psql` or `alembic`. Your output is design documents + the migration body. The executor runs `alembic upgrade head`.

## What a schema design doc looks like

A design doc at `apps/api/alembic/_designs/M-NNN-<slug>.md`:

1. **Goal** — one sentence, links to FEAT-XXX
2. **DDL / migration body** — the exact `op.create_table(...)` / `op.add_column(...)` / raw `op.execute("ALTER TABLE ...")` for the Alembic revision's `upgrade()`
3. **`downgrade()`** — the exact reverse operations (drop column / drop table / drop index)
4. **Apply plan** — `alembic revision --autogenerate -m "..."`, review, then `alembic upgrade head`
5. **Verification SQL** — `SELECT` / `\d <table>` checks the executor runs
6. **Open questions** — `## NEXUS:NEEDS-DECISION` if tradeoffs require user input

## Postgres DDL idioms

- New tables: `CREATE TABLE IF NOT EXISTS`. Primary keys are `BIGINT GENERATED ALWAYS AS IDENTITY` (or UUID where a natural key is required).
- Schema evolution is via `ALTER TABLE ... ADD COLUMN ... / DROP COLUMN ... / ALTER COLUMN ... TYPE ...`. Add columns nullable or with a default first, backfill, then add the `NOT NULL` constraint in a later step to avoid long table locks.
- Foreign keys are enforced: `REFERENCES <t>(<c>) ON DELETE <action>`. Choose the cascade action explicitly.
- JSON: prefer `JSONB`; index hot paths with a `GIN` index.
- Enums: native `CREATE TYPE ... AS ENUM` or a `CHECK` constraint — pick per spec.
- Timestamps: `TIMESTAMPTZ`, default `now()`.

## pgvector idioms

- Enable once: `CREATE EXTENSION IF NOT EXISTS vector;`.
- Vector column: `embedding vector(768)` — specify the dimension in the spec.
- Vector index (HNSW): `CREATE INDEX <name> ON <table> USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);` — name every parameter and the operator class.
- IVFFlat is an alternative for large static tables: `USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);`.
- Match the operator class to the distance used at query time (`vector_cosine_ops` ↔ `<=>`).

## Index changes need benchmarks

Adding/changing an index requires an `EXPLAIN (ANALYZE, BUFFERS)` before and after with a row-count assumption.

## Documentation rule

Every new column gets a `COMMENT ON COLUMN` (or a 1-line note in the design). Vector columns specify dimensionality + similarity metric. Migrations are forward-only with a tested `downgrade()`.

## Forbidden writes (Output-Dir STRICT)

Application source, `docker-compose*.yml`, `Caddyfile`, `.memory/`, `.Codex/`, anywhere outside the repo. Atlas writes design docs + the migration body only.
