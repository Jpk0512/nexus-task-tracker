# Worked example — one full dispatch round trip

**Input (brief, Required Input schema):** the orchestrator dispatches a code-writing
persona with `agent_persona: <owning persona>`, `notepad_topic: TASK-042`,
`skills_required: [agent-protocol, <persona>-conventions]`, `do_not_touch: [<paths owned
by other personas this session>]`, and `verification_required: [<the project's lint
command>, <the project's targeted test command>]`.

**Action taken by the dispatched agent:** reads `notepad_topic` first (agent-protocol
binding), performs the write within its allowed surface only, runs the verbatim
`verification_required` commands, and captures their literal stdout.

**Output (Required Output schema):** the agent returns JSON with `completion_marker: "##
NEXUS:DONE"`, `files_changed` — a strict subset of its write scope — and
`verification_result` set to the VERBATIM command output (never a narrative "it passed").

```json
{
  "status": "complete",
  "completion_marker": "## NEXUS:DONE",
  "files_changed": ["app/api/health/route.ts"],
  "skills_loaded": ["agent-protocol", "forge-wire-conventions"],
  "verification_result": "$ rtk tsc\n\nNo errors found.\n\n$ rtk lint\n\n✔ No ESLint warnings or errors",
  "acceptance_met": [
    {"criterion": "GET /api/health returns 200 with {status, version}", "met": true, "evidence": "route.ts:14-18"}
  ],
  "notepad_written": {"topic": "TASK-042", "agent": "forge-wire", "kind": "fyi", "note": "health route reuses the existing response-shape helper — no new type needed"},
  "db_log_cmds": ["python3 .memory/log.py task update --id TASK-042 --status done"]
}
```

Per the `status ↔ completion_marker` mapping (`references/completion-marker-state-machine.md`),
`completion_marker: "## NEXUS:DONE"` requires `verification_result` to be non-empty and to
show the command actually ran; a marker with an empty or narrative-only
`verification_result` fails the evidence-ladder check and is treated as if BLOCKED.

**The non-obvious delta:** the completion marker alone is never sufficient evidence — the
Fallback Output Ladder check exists specifically because a bare `## NEXUS:DONE` with no
verbatim proof is indistinguishable from a fabricated pass. The schema requires the proof
to travel WITH the marker, not be re-derived later by re-running the command a second time.

---

## Worked example — a REVISE round trip

**Input:** Lens reviews `files_changed: ["app/api/health/route.ts"]` against the
brief's `acceptance_criteria`.

**Output (Lens's return):**
```
## NEXUS:REVISE

- file: "app/api/health/route.ts"
  line: 16
  issue: "response shape omits `version` — spec requires {status, version}, actual body is {status: 'ok'}"
  fix: "add `version: process.env.APP_VERSION ?? 'unknown'` to the returned JSON"
```

**Orchestrator action:** re-spawns the same persona with the actionable issue list as a
`context_files` entry — never a bare "fix it, Lens said no." The re-dispatch is a FRESH
`Task` call, not a `SendMessage` to the prior instance (agent-protocol's leaf-executor
rule — see the "fresh spawn per task" note in the parent skill's Rule 8).
