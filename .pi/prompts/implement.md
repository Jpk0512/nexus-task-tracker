---
description: "Full implementation workflow — scout gathers context, then the implementer builds, then lens-fast ∥ lens verify. Use: /implement <goal>"
---
Implement "$@" end-to-end:

1. **Scout** — dispatch `scout` (single) with `agentScope:"both"` to map all code relevant to the goal. Read its findings.
2. **Route** — pick the implementer persona from `Skill team-routing` (UI → `forge-ui`; API/server → `forge-wire`; data transforms → `pipeline-data`; async → `pipeline-async`; schema → `atlas`; integration → `hermes`; tests → `quill-ts`/`quill-py`). For visual work, dispatch `palette` before `forge-ui`.
3. **Implement** — dispatch that ONE implementer (single) with a full `docs/agents/CONTRACT.md` brief built from scout's findings (`context_files`, `acceptance_criteria`, `verification_required`, `do_not_touch`, `notepad_topic`, `skills_required`). Load `Skill contract-schema` while building it.
4. **Verify** — when the implementer returns `## NEXUS:DONE`, dispatch `lens-fast` + `lens` in ONE parallel `subagent` call. Early-fail on `lens-fast` → re-dispatch the implementer with the issue list.
5. **Checkpoint** — run the returned `db_log_cmds`, then ONE focused commit on the session branch.

Every dispatch uses the `subagent` tool with `agentScope: "both"`.
