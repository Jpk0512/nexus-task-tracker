---
description: "Implement then adversarial review — implementer builds, lens-fast ∥ lens verify, implementer fixes on REVISE. Use: /implement-and-review <goal>"
---
Implement "$@" with a verification gate:

1. **Scout** (single) → context.
2. **Implementer** (single, route via `Skill team-routing`) with a full `docs/agents/CONTRACT.md` brief.
3. **Verify** — dispatch `lens-fast` + `lens` in ONE parallel `subagent` call to verify the `DONE`.
4. **Revise loop** — if `## NEXUS:REVISE`: re-dispatch the SAME implementer with the actionable issue list (`file:line` + what's wrong + the fix). Cap 3 iterations; stalled (issue count not decreasing) → escalate to the user.
5. **Checkpoint** — run `db_log_cmds`, ONE commit.

Every dispatch uses the `subagent` tool with `agentScope: "both"`.
