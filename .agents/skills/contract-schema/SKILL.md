---
name: contract-schema
description: Sub-agent I/O contract — required brief fields, return schema, completion-marker vocabulary, and the 19 universal rules every persona must follow. Use this skill when preparing a sub-agent delegation, validating a returned response, or building a brief template. Canonical source is docs/agents/CONTRACT.md; this skill surfaces the parts Nexus needs at delegation time.
---

# Contract Schema (Nexus delegation contract)

Canonical source: `docs/agents/CONTRACT.md`. This skill is a JIT-loaded reference for Nexus to consult when building or validating a delegation.

## Required brief (all fields)

```json
{
  "agent_persona": "scout|forge-ui|forge-ui-pro|forge-wire|forge-wire-pro|pipeline-data|pipeline-data-pro|pipeline-async|pipeline-async-pro|atlas|hermes|palette|lens|lens-fast|quill-ts|quill-py",
  "goal": "<precise, single-sentence statement>",
  "context_files": ["<path>", "<path>"],
  "acceptance_criteria": [
    "<verifiable criterion — pass/fail, not subjective>"
  ],
  "verification_required": [
    "rtk tsc",
    "rtk lint",
    "uv run ruff check"
  ],
  "do_not_touch": ["<files agent must not modify>"],
  "constraints": ["<must NOT do X>", "<must use Y not Z>"],
  "db_log_cmds": ["<commands orchestrator runs on completion>"],
  "db_context": "<paste of: python3 .memory/log.py context dump>"
}
```

**Required fields** (orchestrator rejects briefs missing any): `agent_persona`, `goal`, `context_files`, `acceptance_criteria`, `verification_required`, `do_not_touch`.

## Required return

```json
{
  "status": "complete|partial|blocked|needs-decision|revise-requested",
  "completion_marker": "## NEXUS:DONE|BLOCKED|NEEDS-DECISION|CHECKPOINT|REVISE",
  "files_changed": ["<path>"],
  "verification_result": "<verbatim output of each verification_required command>",
  "acceptance_met": [{"criterion": "<text>", "met": true, "evidence": "<line numbers>"}],
  "blockers": ["<if blocked>"],
  "decisions_needed": [{"question": "<…>", "options": ["A","B"], "recommendation": "A"}],
  "db_log_cmds": ["python3 .memory/log.py task update --id TASK-XXX --status done"],
  "notes": "<for orchestrator>"
}
```

## Completion marker routing (orchestrator switch)

| Marker | Orchestrator action |
|---|---|
| `## NEXUS:DONE` | Verify verbatim verification_result passing → run db_log_cmds → task done. |
| `## NEXUS:BLOCKED` | Read blockers. Re-route to a different persona OR escalate to user. |
| `## NEXUS:NEEDS-DECISION` | AskUserQuestion with options from `decisions_needed`. Log decision_add. Re-spawn. |
| `## NEXUS:CHECKPOINT` | Write checkpoint summary to `.memory/`. Pause and resume next session. |
| `## NEXUS:REVISE` | Revision loop: re-spawn implementer with Lens issues YAML. Cap 3 iterations. Stall-detect (issue_count non-decreasing → escalate). |

## 9 Universal Rules

1. **Read before edit** — always Read a file before Edit. Re-read after any other tool changes it.
2. **SocratiCode first — programmatically enforced** by `.Codex/hooks/socraticode-gate.sh`. grep/rg/find/ack/ag/fgrep/egrep are denied at command position unless `codebase_search` (or other SocratiCode discovery tool) has fired earlier in the session.
3. **Verify before done** — run every `verification_required` command and capture verbatim output. Enforced by `verify-deliverables.sh` (SubagentStop) which scans the `verification_result` block for each required command's signature.
4. **No silent failures** — failures go in `blockers`, not `notes`.
5. **Commit on the session branch — commit-only, never push** — all work lands on the session branch (the branch active at session start, detected at runtime via `git branch --show-current`; never hardcoded). One focused commit per task IS the checkpoint; no new feature branch and no `git worktree`. A sub-agent commits but does NOT push — only the orchestrator or the user pushes.
6. **Return db_log_cmds** — orchestrator runs them, agent doesn't.
7. **No invented features** — ambiguity → `## NEXUS:NEEDS-DECISION` with `decisions_needed`. Do not design around ambiguity.
8. **Leaf executor — no recursion** — personas may NOT call the Task tool. All delegation flows through Nexus.
9. **Respect do_not_touch + Output-Dir STRICT** — escalate via `## NEXUS:NEEDS-DECISION` if a needed change is forbidden. Enforced by `verify-deliverables.sh` via `forbidden_paths` + `must_not_modify` glob checks against agent's `files_changed`.

> *Note:* The "agent-browser for web tasks" rule was replaced by `aside` (CLI exec/repl + mcp) per DEC-037 (2026-06-26). The Article XII visual gate is now enforced by `visual-evidence-gate.sh` (deny-capable, accountable-skip via `verification_result.visual_skip_reason`). Supersedes DEC-036's drop of the unguarded rule.

## Brief template (copy into delegations)

```json
{
  "agent_persona": "forge-ui",
  "goal": "Add an /api/health endpoint to app/api/ that returns {status: 'ok', version}.",
  "context_files": [
    "app/api/mcp/route.ts",
    "docs/features/FEAT-001-tableau-workbook-catalog.md"
  ],
  "acceptance_criteria": [
    "GET /api/health returns 200 with {status, version}",
    "Response type is application/json"
  ],
  "verification_required": ["rtk tsc", "rtk lint"],
  "do_not_touch": ["ingestion/", "models/", "docker-compose*.yml"],
  "constraints": ["use Next.js 15 App Router, not Pages API"],
  "db_log_cmds": ["python3 .memory/log.py task update --id TASK-XXX --status done"],
  "db_context": "<from context dump>"
}
```
