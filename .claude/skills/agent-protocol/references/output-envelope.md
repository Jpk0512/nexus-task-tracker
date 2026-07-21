# Output Envelope — verbatim return schema

Two tiers. **LEAN (T0/T1):** return only `completion_marker` + `files_changed` +
`verification_result`. **FULL (T2 / error-fix / risky):** the complete object below, every
field. This fenced JSON block is the routing signal in both tiers (F1-08 cutover) — `status`
when present (FULL), else `completion_marker` (LEAN, which carries no `status` field by
design). The `## NEXUS:<STATUS>` H2 heading stays mandatory as the human-readable convention
twin — never itself what a gate branches on.

Canonical master: `docs/agents/CONTRACT.md` (Required Output). If this file and CONTRACT
disagree, CONTRACT wins — report the drift via feedback, do not silently follow either.

```json
{
  "status": "DONE | BLOCKED | NEEDS-DECISION | CHECKPOINT | REVISE | DEFER-REQUEST",
  "completion_marker": "## NEXUS:DONE | ## NEXUS:BLOCKED | ## NEXUS:NEEDS-DECISION | ## NEXUS:CHECKPOINT | ## NEXUS:REVISE | ## NEXUS:DEFER-REQUEST",
  "files_changed": ["<path>"],
  "skills_loaded": ["agent-protocol", "<persona>-conventions"],
  "verification_result": "<verbatim output of each verification_required command>",
  "acceptance_met": [
    {"criterion": "<text from input>", "met": true, "evidence": "<line numbers or verbatim output>"}
  ],
  "blockers": ["<description if status=blocked>"],
  "decisions_needed": [
    {"question": "<…>", "options": ["A", "B"], "recommendation": "A"}
  ],
  "db_log_cmds": [
    "python3 .memory/log.py task update --id TASK-001 --status done",
    "python3 .memory/log.py decision add ..."
  ],
  "notepad_written": {
    "topic": "<notepad_topic from brief>",
    "agent": "<persona>",
    "kind": "fyi | nuance | reminder | gotcha | next-agent-action",
    "note": "<the insight written>"
  },
  "notes": "<anything the orchestrator must know>",
  "root_cause_analysis": {
    "symptom": "<one line describing what the user observed>",
    "why_chain": [
      "<Why 1: immediate cause>",
      "<Why N (root): the architectural / contract / design defect that allowed this bug class — as many intermediate steps as the cause actually needs, no minimum, no padding>"
    ],
    "pattern_fix": "<how the codebase/process changes so this class cannot recur>"
  },
  "deploy_step": {
    "restart_action": "<none | HMR | restart <svc> | build+up <svc>>",
    "verification": "<command that confirms the new code is running>"
  }
}
```

## Field notes

- `root_cause_analysis` — **error-fix ONLY**, and **not required during the redesign**
  (no-RCA, DEC-039). Never required for new construction, docs, or trivial changes.
- `deploy_step` — REQUIRED for any delivery that touches a runtime-service surface (app /
  ingestion / design trees, or `docker-compose*.yml`). It DOCUMENTS the restart/rebuild
  action; a LOCAL rebuild/restart to verify already-committed code is part of verification
  and may be run directly. The human handoff is reserved for REMOTE/PRODUCTION releases
  (Constitution Articles XII / XIV).
- `notepad_written` — **FULL envelope only** (T2 / error-fix). Omit in LEAN.
- `verification_result` — verbatim terminal text of every `verification_required` command.
  Narrative is not evidence (see references/s12-never-fabricate.md).

Output ONLY the JSON object. No prose before or after it (haiku-tier terminator; higher
tiers may precede it with the completion-marker heading and a short evidence block).
