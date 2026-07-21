# Worked example — a revision loop that succeeds on iteration 2

**Iteration 0:**
```
implementer returns: ## NEXUS:DONE
Lens dispatched to validate → verdict: ## NEXUS:REVISE
  issues: [
    { file: "app/api/settings/route.ts", line: 22,
      issue: "PATCH handler doesn't validate the body against the Zod schema",
      fix: "call settingsSchema.parse(body) before writing" }
  ]
prev_count = 1
```

**Iteration 1:**
```
re-spawn implementer (fresh Task call, NOT SendMessage) with
  context_files += [lens_revision_report.md]
implementer returns: ## NEXUS:DONE
Lens re-validates → verdict: ## NEXUS:DONE (GREEN)
→ break (success)
```

**Orchestrator's closing actions:**
1. Check `verification_result` is verbatim passing (not narrative).
2. Check every `acceptance_met` entry is `true` with evidence.
3. Run `db_log_cmds` (task update to `done`).
4. Mark the task done.

**Contrast — what a STALL would have looked like at iteration 1:**
```
Lens re-validates → verdict: ## NEXUS:REVISE
  issues: [ <same issue count: 1> ]
current_count (1) >= prev_count (1) → STALL
→ escalate("revision loop stalled at iteration 1 — issue count not decreasing")
```
This is a DIFFERENT outcome from the max-iteration cap (`iteration == 3`) — both are
legitimate escalation paths, and both fire from the SAME `revision loop` structure, never
from silently accepting the second REVISE as "good enough."
