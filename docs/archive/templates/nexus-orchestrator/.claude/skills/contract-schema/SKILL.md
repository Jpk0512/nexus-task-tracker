---
name: contract-schema
description: Sub-agent I/O contract — required brief fields, return schema, completion-marker vocabulary, and the universal rules every persona must follow. Use this skill when preparing a sub-agent delegation, validating a returned response, or building a brief template. Canonical source is docs/agents/CONTRACT.md; this skill surfaces the parts Nexus needs at delegation time.
---

# Contract Schema (Nexus delegation contract)

Canonical source: `docs/agents/CONTRACT.md`. This skill is a JIT-loaded reference for Nexus to consult when building or validating a delegation.

## Required brief (all fields)

```json
{
  "agent_persona": "<persona-name from docs/agents/TEAM.md>",
  "goal": "<precise, single-sentence statement>",
  "context_files": ["<path>", "<path>"],
  "acceptance_criteria": [
    "<verifiable criterion — pass/fail, not subjective>"
  ],
  "verification_required": [
    "<type-check command>",
    "<lint command>",
    "<test command>"
  ],
  "do_not_touch": ["<files agent must not modify>"],
  "worktree_branch": "feat/<slug> or null",
  "constraints": ["<must NOT do X>", "<must use Y not Z>"],
  "db_log_cmds": ["<commands orchestrator runs on completion>"],
  "db_context": "<paste of: python3 .memory/log.py context dump>",
  "notepad_topic": "<TASK-NNN | FEAT-NNN | branch-name | freeform-kebab>",
  "skills_required": ["<skill-name-1>", "<skill-name-2>"]
}
```

**Required fields** (orchestrator rejects briefs missing any): `agent_persona`, `goal`, `context_files`, `acceptance_criteria`, `verification_required`, `do_not_touch`.

**`skills_required`** — Required for any code-writing persona. List the skill names the agent must load before their first non-Read tool call. See `docs/agents/SKILL_MAP.md` for the minimum set per `(persona, work_type)`. Optional for read-only personas (scout, lens).

**Strongly recommended:** `worktree_branch`, `constraints`, `db_log_cmds`, `db_context`.

The orchestrator REJECTS any brief missing a required field. Personas that receive an incomplete brief must return `## NEXUS:BLOCKED` with the missing field listed.

---

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
  "notepad_written": {
    "topic": "<notepad_topic from brief>",
    "agent": "<persona>",
    "kind": "fyi | nuance | reminder | gotcha | next-agent-action",
    "note": "<the insight written>"
  },
  "notes": "<for orchestrator>",
  "root_cause_analysis": {
    "symptom": "<one line describing what the user observed>",
    "why_chain": [
      "<Why 1: immediate cause>",
      "<Why 2: cause of Why 1>",
      "<Why 3: cause of Why 2>",
      "<Why 4: cause of Why 3>",
      "<Why 5 (root): architectural / contract / design defect that allowed this bug class>"
    ],
    "pattern_fix": "<how the codebase/process changes so this class cannot recur>"
  },
  "deploy_step": {
    "branch": "<branch-name>",
    "restart_action": "<none | HMR | restart <svc> | build+up <svc>>",
    "verification": "<command that confirms the new code is running>"
  }
}
```

**Field notes:**
- `root_cause_analysis` — REQUIRED for any error-fix or bug-investigation task. The `why_chain` must contain ≥ 5 entries.
- `deploy_step` — REQUIRED for any delivery touching source code or infrastructure. Omitting is a CONTRACT VIOLATION.
- `notepad_written` — REQUIRED in every agent response. Either the insight object or `{skipped: "no useful context to add"}`. Omitting entirely is a CONTRACT VIOLATION.

## Completion marker routing (orchestrator switch)

| Marker | Orchestrator action |
|---|---|
| `## NEXUS:DONE` | Verify verbatim verification_result passing → run db_log_cmds → task done. |
| `## NEXUS:BLOCKED` | Read blockers. Re-route to a different persona OR escalate to user. |
| `## NEXUS:NEEDS-DECISION` | AskUserQuestion with options from `decisions_needed`. Log decision_add. Re-spawn. |
| `## NEXUS:CHECKPOINT` | Write checkpoint summary to `.memory/`. Pause and resume next session. |
| `## NEXUS:REVISE` | Revision loop: re-spawn implementer with Lens issues YAML. Cap 3 iterations. Stall-detect. |
| `## NEXUS:DEFER-REQUEST` | Agent found out-of-scope error; orchestrator approves/rejects defer or instructs inline fix. |

## Universal Rules (all agents must follow)

1. **Read before edit** — always Read a file before Edit. Re-read after any other tool changes it.
2. **SocratiCode first** — semantic search (`codebase_search`, `codebase_symbol`, `codebase_graph_query`) must fire before grep/find/rg. Enforced by the socraticode-gate hook.
3. **Verify before done** — run every `verification_required` command; capture verbatim output. Claims without output are rejected.
4. **No silent failures** — failures go in `blockers`, not `notes`.
5. **Worktree work only on assigned branch** — never commit to `main` from a worktree agent.
6. **Return `db_log_cmds`** — orchestrator runs them; agent does not.
7. **No invented features** — ambiguity → `## NEXUS:NEEDS-DECISION` with `decisions_needed`. Do not design around ambiguity.
8. **Leaf executor — no recursion** — personas may NOT call the Task tool. All delegation flows through Nexus.
9. **Respect `do_not_touch`** — if a needed change is in a forbidden file, return `## NEXUS:NEEDS-DECISION`.
10. **Deploy-step disclosure** — every implementation response touching source code or infrastructure MUST end with a `## Deploy step` block naming the branch AND the restart action.
11. **Root cause in every fix response** — bug-fix responses MUST include a `## Root Cause Analysis` block with ≥5 why-chain entries.
12. **No deferral of discovered issues** — fix in the same delivery unless the user explicitly authorized defer via `## NEXUS:DEFER-REQUEST`.
13. **Visual and end-to-end verification** — tests that mock the boundary they validate do NOT satisfy this rule. Real-boundary invocation required.
14. **Skill triggers** — each persona has a skill-trigger table. Load triggered skills via `Skill <name>` before beginning work.
15. **Notepad read-write loop** — first action: `notepad list`. Last action: `notepad add` with a concise insight.
16. **Lens validates before NEXUS:DONE is accepted** — coding-agent NEXUS:DONE is CONDITIONAL until Lens validates.

## Brief template (copy into delegations)

```json
{
  "agent_persona": "[PERSONA]",
  "goal": "One precise sentence stating what to accomplish.",
  "context_files": [
    "docs/features/FEAT-XXX.md",
    "path/to/relevant/file"
  ],
  "acceptance_criteria": [
    "Given X, when Y, then Z (verifiable pass/fail)"
  ],
  "verification_required": ["<type-check>", "<lint>"],
  "do_not_touch": ["<persona-boundary paths>"],
  "worktree_branch": "feat/<slug>",
  "constraints": ["Use [framework], not [alternative]"],
  "db_log_cmds": ["python3 .memory/log.py task update --id TASK-XXX --status done"],
  "db_context": "<from context dump>",
  "notepad_topic": "TASK-XXX",
  "skills_required": ["[persona]-conventions"]
}
```
