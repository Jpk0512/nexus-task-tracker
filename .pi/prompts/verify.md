---
description: "Verify already-implemented work — dispatch lens-fast ∥ lens against the changed files. Use: /verify <paths-or-task>"
---
Verify "$@":

1. **Identify** the changed files / task (from the task id, git diff, or the paths given).
2. **Dispatch** `lens-fast` + `lens` in ONE parallel `subagent` call with the changed files as `context_files` and the project's verification commands as `verification_required`.
3. **Route** on the verdict: `## NEXUS:DONE` → accept; `## NEXUS:REVISE` → surface the actionable issue list to the user (or re-dispatch the implementer if known).

Every dispatch uses the `subagent` tool with `agentScope: "both"`.
