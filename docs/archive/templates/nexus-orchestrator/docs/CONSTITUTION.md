# Project Constitution

**Version:** 1.0
**Authority:** Highest governance document. Supersedes all `docs/`, nested `CLAUDE.md` files, and agent contracts when they conflict. Only `project.db` has higher precedence (live runtime state).

---

## Article I — Spec-First Mandate

No implementation begins without a spec file at `docs/features/FEAT-XXX.md` that satisfies all of:
- User stories with GWT acceptance criteria written and accepted
- No `[NEEDS CLARIFICATION]` markers remaining
- Constitution check checklist completed (all 13 articles)

**Gate:** `python3 .memory/log.py planning-gate check --feat FEAT-XXX` must return PASS.

**Verification gating is non-bypassable for Simple+ tier.** Every Simple, Standard, and Complex task MUST pass Lens validation before the task is marked done. The Trivial tier (≤1 file, ≤5 LOC, no logic change, no design decision) is exempt from Lens gating — handled inline by Nexus and audit-logged via `context snapshot --action-type trivial-fix`. Trivial-tier tasks MUST NOT touch production logic; if any logic change is discovered, the tier MUST be promoted to Simple and Lens dispatched.

---

## Article II — Test-First Imperative

Test stubs written by Quill and confirmed failing *before* implementers write production code.

**Exception:** Integration tests requiring a live external service may follow implementation stubs but must be confirmed passing before the task is marked done.

**Gate:** Planning gate item 7 — test file existence check.

---

## Article III — SocratiCode-Before-Planning

Semantic search run before any multi-file design decision. The file watcher keeps the index current — this gate means *searching*, not re-indexing.

**Exempt:** Single-file changes with completely obvious scope.

---

## Article IV — Schema Lock

The primary database schema must be documented in the feature spec before any data-layer or query code begins. The schema section must contain the DDL (`CREATE TABLE` statements or equivalent for your database).

**Note:** This applies to any persistent data store in the project (SQL, NoSQL, DuckDB, etc.). Configure the database type in `nexus-config.json → stack.database`.

---

## Article V — Type Safety

Before any task is marked done, run the project's type-check and lint commands (configured in `nexus-config.json → personas[].verification_cmds`). Zero errors. No exceptions for "just a quick fix."

Examples by stack:
- TypeScript: `rtk tsc` → zero errors; `rtk lint` → zero warnings
- Python: `uv run ruff check src/` → zero warnings
- Go: `go vet ./...` → zero issues

---

## Article VI — Single Writer

The primary database has exactly one writer process at any time. Data-layer workers are the sole writers. Application-layer code connects in read-only mode (or equivalent). This is a data-integrity constraint, not a style preference.

**Project-specific:** Configure which persona owns writes vs. reads in `nexus-config.json → personas[].owned_dirs` and document in `docs/ARCHITECTURE.md`.

---

## Article VII — Context Preservation

Sub-agents receive file-based briefs and write file-based outputs. Agents must not rely on conversation context from prior turns or sessions. All durable state lives in files or `project.db`.

---

## Article VIII — Constitution Check

Every feature plan includes an explicit 13-article checklist before tasks are generated. No article may be skipped as "not applicable" without written justification in the spec.

The spec template at `docs/templates/SPEC_TEMPLATE.md` includes the checklist.

---

## Article IX — Idempotency

All data pipeline operations (ingestion, database writes, file generation) are safe to re-run without corrupting state. Use upsert patterns. No destructive-only operations.

---

## Article X — Root Cause Mandate

Every error-fix response MUST include a `## Root Cause Analysis` block with at least five levels of "why" chained from the symptom to the deepest underlying cause. A fix that resolves the symptom but not the cause is a contract violation. When uncertain whether the root cause has been found, continue investigating — do NOT mark the task done. If the architectural pattern that allowed the bug class to recur is identified, it must be flagged AND addressed before the task closes.

**Violation:** Closing a task with a symptom-only fix, or with a `## Root Cause Analysis` block containing fewer than 5 levels, is a CRITICAL contract violation. Nexus must reject the delivery and return it to the implementing agent.

---

## Article XI — No Deferral of Discovered Errors

When any agent or the Nexus orchestrator discovers an error, anomaly, or contract violation while doing other work, that issue MUST be fixed immediately in the same delivery. Filing it as a follow-up task is FORBIDDEN unless the user explicitly authorizes the defer via AskUserQuestion. The default is FIX, not FILE.

**Violation:** A follow-up task created without explicit user authorization is a contract violation. Nexus must reopen the deferring task and require the fix in the current delivery.

---

## Article XII — Visual & End-to-End Verification Gate

"Tests pass" is not done. Done requires evidence at the real process boundary:

- **UI changes:** an agent-browser screenshot showing the before AND after state, included in the PR body or implementation response.
- **API changes:** an end-to-end invocation (curl, agent-browser, or equivalent that exercises the real code path including process boundaries), with the result included in the response.
- **Container/Dockerfile changes:** a successful `docker build` + container start + in-container smoke test, with the output included in the response.

Tests that mock the boundary they validate (e.g., mocking `child_process` to test a shell-out) do NOT satisfy this gate.

**Violation:** Marking a task done without the required evidence is a contract violation. Nexus must re-open and require the screenshot, curl output, or smoke test before closing.

---

## Article XIII — Parallel-First Orchestration

When a task can be decomposed into N independent subtasks, the Nexus orchestrator MUST dispatch all N in a single message (one tool block, multiple Agent invocations). Investigation phases dispatch ≥3 parallel Scouts probing different angles (architecture, data flow, recent commits, related code). Sequential single-agent dispatches are FORBIDDEN unless serialized by a real dependency that the orchestrator can name in writing.

**Violation:** A sequential dispatch where parallelism was possible — without a written dependency justification — is a contract violation. The orchestrator must acknowledge the violation in the next session retrospective and log a lesson via `python3 .memory/log.py lesson add`.
