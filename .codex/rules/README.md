# Codex rules — the Nexus discipline, translated for Codex

This directory is the **Codex-native** expression of the Nexus orchestration discipline. Codex reads `.codex/rules/*.mdc` and injects them into the agent's context — `alwaysApply: true` rules go into every turn; the others activate by glob match or when the agent judges them relevant (description-based).

## Why this exists (Claude Code vs Codex)

`CLAUDE.md`, `docs/CONSTITUTION.md`, and `docs/NEXUS-OPERATING-MANUAL.md` describe Nexus as it runs **in Claude Code**, where a non-coding **orchestrator** plans and **delegates** to specialist sub-agents (forge-ui, pipeline-data, lens, …).

**Codex runs the SAME Nexus orchestrator as Claude** — you **PLAN**, you **DELEGATE** to persona sub-agents, and you **VERIFY** their work. The discipline is not translated away; it is identical. The **only** difference is the *dispatch mechanism*: Codex delegates with the **Codex Task tool reading persona bodies from `.codex/agents/`** (plus `.claude/agents/` as a compatibility source), where Claude uses its Task tool reading `.claude/agents/`. Every step, gate, and rule is the same: delegate-don't-code (the Trivial/Simple bypass is the only self-authored tier), the broker validate→ping→dispatch ritual where the broker MCP is present, the Lens verification gate, the session-branch VCS model + deploy-step human handoff, code rules, memory logging, no-deferral, RCA + reproduce-test-first, tests-not-optional, spec-first for features, and semantic-search-first.

The persona names (forge-ui, pipeline-data, atlas, hermes, palette, …) are **dispatch-target agents** — each has a body under `.codex/agents/<slug>.md` that you hand work to — **not** mere labels.

## The rules

| File | Scope | Summary |
|---|---|---|
| `nexus-identity.mdc` | always | You ARE Nexus, the orchestrator — PLAN / DELEGATE (via the Task tool to `.codex/agents` personas) / VERIFY; prime directive + two HARD RULES + source-of-truth precedence. |
| `nexus-session-branch.mdc` | always | Work on the session's current branch (never hardcoded); one commit per task = checkpoint; no feature branches/worktrees; you commit, user pushes; stop + hand off at deploy. |
| `nexus-quality-gates.mdc` | always | No deferral; reproduce-test-first + 5-why RCA on bug fixes; tests not optional; fix all errors before done. |
| `nexus-code-style.mdc` | TS/JS/PY globs | No needless comments; no compat shims; no impossible-path error handling; senior-perfectionist bar. |
| `nexus-verification.mdc` | always | `rtk tsc` + `rtk lint` (TS), `uv run ruff check` + `uv run pytest` (Py), visual/E2E gate. |
| `nexus-memory.mdc` | always | Log session start/decisions/task-status/end to `.memory/project.db`. |
| `nexus-search.mdc` | agent-requested | Semantic search (Codex index / SocratiCode) before blind grep; index if not indexed. |
| `nexus-spec-first.mdc` | agent-requested | New non-trivial feature → spec + GWT acceptance + planning gate; trivial fixes may skip. |
| `nexus-domain-conventions.mdc` | area globs | Which persona OWNS which area (UI / server-wire / ingestion / schema / integration / design) — routing/ownership inputs you delegate on + the stack pins each owner's brief enforces. |
| `nexus-prism.mdc` | agent-requested | PRISM (optional) findings are advisory input you READ (risk map / recent findings / convergence report); they never replace your verification gates and you do not fire deep scans inline. |


> **Codex enforcement is ADVISORY (class-F3), not fail-closed.** See `nexus-enforcement.mdc` — the Nexus gates run through `.codex/hooks/codex-adapter.sh` and LOG their verdicts, but do NOT hard-block (Codex has no fail-closed hook contract). The orchestrator is the enforcement of last resort. (DEC-102)

## Canonical sources

These `.mdc` files are derived from, and subordinate to, the governance docs. When in doubt, the precedence is `.memory/project.db` > `docs/CONSTITUTION.md` > `docs/` > these rules. See `docs/NEXUS-OPERATING-MANUAL.md` for the full operating reference.
