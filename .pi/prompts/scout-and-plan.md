---
description: "Recon + DAG plan, no implementation. Use: /scout-and-plan <goal>"
---
Map and plan "$@" without implementing:

1. **Scout** — dispatch `scout` (single) for structured findings.
2. **Plan** — dispatch `planner` (single, opus) with scout's findings as context to produce a task DAG under `docs/plans/` — each node a `docs/agents/CONTRACT.md` brief with `depends_on` / `downstream_consumers` edges.
3. **Review** the plan; do NOT begin execution. Surface it to the user for confirmation before any implementer dispatch.

Every dispatch uses the `subagent` tool with `agentScope: "both"`.
