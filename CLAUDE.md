# Project — Nexus Orchestrator

This project uses **Nexus**, an AI orchestration framework. Nexus auto-loads as the main session via `agent: nexus-orchestrator` in `.claude/settings.json`.

Agent system prompt: `.claude/agents/nexus-orchestrator.md`. Deep operational protocol: `Skill nexus-protocol`.

Nexus orchestrates; it does not write code (`disallowedTools: Write, Edit, NotebookEdit`).

## Version

The installed Nexus version is in `.memory/.nexus-version` (a single line, e.g. `1.7.0`); `.nexus-ledger.json` carries the same version plus `installed_at`/`updated_at`. When asked "what version are you on?", read `.memory/.nexus-version` and report it. The SessionStart health banner also prints `Nexus v<version>` from this file.

## Source of Truth Precedence

1. `.memory/project.db` — live decisions, tasks, sessions
2. `docs/CONSTITUTION.md` — governance
3. `docs/` — DECISIONS, TASKS, PRD, ARCHITECTURE, features/
4. Nested `CLAUDE.md` files

## Codebase Search

SocratiCode-first. grep/rg/find/ack/ag blocked by `.claude/hooks/socraticode-gate.sh` until a SocratiCode discovery tool fires and returns indexed results — **for code-writing personas only.** A discovery call that errors or returns no results does NOT open the gate.

**Read-only / orchestrator personas have FREE grep + Read (gate-exempt, DEC-027):** nexus, scout, lens, lens-fast, palette short-circuit the gate entirely (both grep mode AND Read mode) — they never mutate code, so the SocratiCode-before-grep ceremony buys nothing. Code-writing personas (forge-ui, forge-wire, pipeline-data, pipeline-async, atlas, hermes, quill-ts, quill-py, and every `-pro` variant) STILL hit the gate.

**Routing heuristic:** grep for known strings/symbols; SocratiCode for concepts/maps; lsp-py for type-exact refs.

Note: `codebase_search` is disallowed for the orchestrator persona — use `codebase_symbol(name="<bareSymbol>")` / `codebase_symbols(query="…")` (or `codebase_graph_query` / `codebase_impact`) instead.

If SocratiCode reports the project is not indexed (e.g. "not indexed", "No context artifacts configured", or empty results), INDEX IT — never fall back to grep. Run the exact MCP tool call for the situation (all under the `mcp__plugin_socraticode_socraticode__` prefix), with this project's absolute path:

- Index a project:         `codebase_index(projectPath="/abs/path")`, then poll `codebase_status(projectPath="/abs/path")` until 100%
- Incremental re-index:    `codebase_update(projectPath="/abs/path")` (changed files only)
- Full rebuild:            `codebase_remove(projectPath="/abs/path")`, then `codebase_index(projectPath="/abs/path")` (e.g. after an embedding-model change)
- Build the code graph:    `codebase_graph_build(projectPath="/abs/path")`, then poll `codebase_graph_status(projectPath="/abs/path")`
- Index context artifacts: `codebase_context_index(projectPath="/abs/path")`

Then re-run the discovery tool.

## Memory Logging

```bash
python3 .memory/log.py session start
python3 .memory/log.py task update --id TASK-XXX --status in_progress|done
python3 .memory/log.py decision add --title "..." --context "..." --decision "..." --rationale "..." --alternatives "..." --consequences "..."
python3 .memory/log.py session end --summary "..." --next_step "..."
```

## Rules

- Delegate per `docs/agents/TEAM.md` — Nexus does not write code itself
- Persona-based routing: Forge owns app/, Pipeline owns ingestion/, Atlas owns schema, Hermes owns wiring, Lens validates, Quill tests, Scout investigates
- Dispatch reference (DEC-021 no-rediscovery): at any non-trivial dispatch decision, load `Skill nexus-dispatch-catalog` (match TASK SHAPE → primitive + the 6 techniques + the goal model); once a primitive is chosen, `Skill nexus-orchestration` (how to RUN it); for long-running autonomous eval-driven loops, `Skill nexus-loss-function`.
- Before any new feature: spec + GWT + planning gate PASS
- Simple task bypass: ≤2 files, already read, no design decision
- Verification: `rtk tsc` + `rtk lint` (TS), `uv run ruff check` (Python)
- Every Task dispatch is gated by `.claude/hooks/broker-gate.py` — call `nexus_validate_brief_tool` first (validate→ping→dispatch, 120s window). The gate is **fail-CLOSED**: a missing/malformed/unreadable `broker_state.json` → DENY (exit 2) unless `NEXUS_BROKER_ALLOW_DEGRADED=1` (then allowed with a LOUD stderr WARN every turn). This is the FastMCP validation **broker** (`python -m broker.server`), NOT a Redis message broker. See `Skill nexus-protocol` §9 and `docs/NEXUS-OPERATING-MANUAL.md`.
- Long sessions: the `precompact-reinject.py` PreCompact hook re-injects role + Constitution headings + open tasks after compaction; manually re-read files before delegating against stale state.

## Task lifecycle (session-branch; commit-as-checkpoint + deploy-step handoff)

Nexus personas work **directly on the branch the session was created from** — the current/active branch at session start, detected at runtime via `git branch --show-current`. That branch MAY be `main` OR any other branch (some projects are worked off a non-main branch); the working branch is **dynamic, never hardcoded**. There are **NO** new per-task feature branches and **NO** git worktrees. **ONE commit per task IS the checkpoint** — every commit is revertable, so divergent history is unnecessary.

1. **Where:** all work lands on the **session branch** (whatever branch was active at session start). Standard/Complex features and Trivial/Simple fixes alike commit on that branch (after the planning gate passes for the tiers that require it).
2. **Checkpoint granularity:** one focused commit per completed task. The commit is the rollback unit — to undo a task, revert its commit. No new branch to merge, nothing to clean up.
3. **Who pushes:** only the **orchestrator or the user** pushes the session branch. A sub-agent COMMITS on the session branch but does NOT push it — it commits and lets the orchestrator (or the user) push. An explicitly user-authorized sub-agent push is allowed via the bypass token.
4. **Parallel personas:** code-writing personas dispatched for the same work coordinate on the session branch (no per-persona worktree, no per-persona divergent branch). Read-only personas (Scout/Lens) share the tree.
5. **Verification before done:** when the work passes Lens, the orchestrator marks `## NEXUS:DONE`. Lens VERIFIEs every time (unchanged).
6. **Deploy-step human handoff:** the orchestrator **STOPS at the deploy/release step** and hands off to a human who approves deploying from the session branch. Nexus never deploys autonomously (Constitution Article XII deploy gate). This human handoff replaces the old PR-merge gate.
7. **Deploy step block:** every implementation response touching `app/`/`ingestion/`/`design/`/`docker-compose*.yml` ends with a `## Deploy step` block naming the restart action + verification command (CONTRACT Rules 10/14). It targets the current session-branch HEAD — no branch line — and is the on-disk artifact the human uses to approve and rebuild.

Full governance: `docs/NEXUS-OPERATING-MANUAL.md` and the Constitution's session-branch development & deploy-step-handoff article.

## Code Rules

- No comments unless the WHY is non-obvious
- No error handling for impossible paths
- No backwards-compat shims for removed code

## Feature Specs

Index in `docs/TASKS.md`. Active specs under `docs/features/FEAT-*.md`.
