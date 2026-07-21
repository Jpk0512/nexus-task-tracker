---
name: agent-protocol
description: "The universal execution protocol every code-writing agent carries. Named FIRST
  in each agent's frontmatter skills:, it delivers the leaf-executor rule, SocratiCode-first
  discovery, skill-invocation binding, the Agent Notepad ritual, Friction Signals, the
  write-boundary deny-tail, and pointers to the output envelope / completion markers / never-
  fabricate rule — the boilerplate that was byte-identical across every persona. Use when
  operating as ANY dispatched persona — code-writing rules bind code-writers; read-only
  personas obey the leaf-executor, marker, and notepad sections. Do NOT use for persona-specific
  ownership, gate commands, or conventions — those live in the agent file and its
  <persona>-conventions skill."
metadata: {tier: sonnet, token_budget: 1600, injectable: true}
---

# Agent Protocol

The layer every code-writing persona shares. It is the single home of these blocks; agents
name it first in `skills:` and never restate its contents. Persona signal — ownership,
scars, gate commands — stays in the agent file.

## When this fires

You are a dispatched code-writing agent (a leaf persona). This protocol arrives with you by
composition — obey it before your first non-Read tool call. It never overrides a
persona-specific rule in your own file; it fills the gaps every persona shares.

## Single Home (consumer-side)

Each hoisted block has ONE physical master, named at its pointer. If two documents disagree,
**the declared master wins** — report the drift via Friction Signals feedback, never
silently follow either copy. This skill points; it does not fork.

## Rules

### Proportional ceremony (default-fast, DEC-039, restated DEC-068)
Match ceremony to task tier. **T0 (docs/config) / T1 (trivial single-file) dispatches SKIP the
notepad bracket** (owner-approved TRADE, DEC-068) — along with RCA and Friction unless there's a
real signal; return the **LEAN envelope** (marker + `files_changed` + `verification_result`).
**T2 / error-fix / risky:** full ceremony + full envelope. When unsure, ask the orchestrator's
brief — it names the tier.

### Leaf executor
You are a LEAF EXECUTOR. You MUST NOT call the **Task** tool. You may NOT call the **Agent**
tool either — all delegation flows through the orchestrator. You may NOT spawn sub-agents.
`disallowedTools: Task, Agent` enforces this structurally; the rule states the intent. If you
need work outside your surface, return `## NEXUS:NEEDS-DECISION` naming the persona that owns
it — never reach for it yourself.

### Discovery — grep is free (DEC-039)
Discovery is unblocked: grep/rg/find are free for all personas this cycle. Prefer a structural
SocratiCode query for concepts/maps; grep for known strings; lsp-py for type-exact refs.
Preference, not a gate.

### Skill invocation
When the brief carries `skills_required`, invoke each via `Skill <name>` BEFORE your first
non-Read tool call. Do not rely on auto-discovery. The return envelope's `skills_loaded`
field is checked deterministically — if you loaded it, it must be listed there.

### External tools — conduit
A capability not in your direct tool list may still be reachable: EXTERNAL services (GitHub,
Tableau, Docker, a database, a URL fetch, …) route through conduit's 4 meta-tools
(`mcp__conduit__toolport_status` → `toolport_search_tools` → `toolport_call_tool` →
`toolport_fetch_result`). **Never declare a capability unavailable without checking conduit
first.** Nexus-core tools stay DIRECT, never routed through conduit: the broker gate
(`mcp__nexus-broker__*`), SocratiCode `codebase_*`, and `log.py`. Full pattern + current
server snapshot: `Skill conduit`.

### Agent Notepad (T2 / error-fix only, DEC-039)
For **T2 / error-fix / multi-phase work**, bracket your work with the notepad:

1. `python3 .memory/log.py notepad list --topic <topic>` — FIRST action. The topic is in
   your brief (`notepad_topic`).
2. Do your work.
3. `python3 .memory/log.py notepad add --topic <topic> --agent <persona> --note "..." --kind <kind>`
   — LAST action before your completion marker.

Note rules:
- ≤500 chars.
- Insight, not status. "Completed" is forbidden. "The X pattern breaks under Y condition" is
  correct.
- Pick the right kind: `gotcha` / `nuance` / `reminder` / `fyi` / `next-agent-action`.

For T0/T1, the LEAN envelope OMITS `notepad_written` entirely (CONTRACT.md Rule 17) — do not
populate a skip stub. The next agent on the same topic depends on what you write for T2 —
treat it like leaving a sticky note for a colleague.

### Write-boundary deny-tail
Your agent file lists your OWN allow/deny/route rows. Regardless of persona, these paths are
NEVER writable by a leaf agent:
- `.memory/**` — the orchestrator owns this writeable surface.
- `.claude/**` — orchestration meta; orchestrator + user only.
- `~/`, `/etc/`, anywhere outside the repo — never.

A persona-file-declared overlay (e.g. scout's `.memory/scout-reports/**`, atlas's
`.memory/schema.sql` + `.memory/migrations/**`, the pipeline personas' `.memory/*.py`) is a
sanctioned exception to the `.memory/**` row — the agent file's overlay wins for exactly its
named paths.

Any attempted write outside your allowed set = stop and return `## NEXUS:BLOCKED` with
`attempted_path`. A forbidden path in the brief you must change = `## NEXUS:NEEDS-DECISION`
naming the file, never a silent edit.

### Typed return envelope (canonical, F1-08)
Alongside your `## NEXUS:<STATUS>` marker, emit ONE fenced ```json block per
`docs/agents/CONTRACT.md`'s "Typed return envelope (CANONICAL — F1-08 cutover)" subsection
(`completion_marker`, `files_changed`, `status`, `verification_result`). That JSON block is
now the authoritative routing signal — the marker heading stays mandatory but is the
human-readable convention derived from it; this skill does not restate the field list or
examples.

### Friction Signals
When the orchestrator itself blocks, confuses, or stalls you (a gate DENY, a
NEEDS-DECISION/REVISE you had to emit, a wrong-fit persona/skill, a roster mismatch, or
missing context), call `nexus_submit_feedback` (or `python3 .memory/log.py feedback add`).
No permission needed — the meta-layer harvests it to improve the system. This is also the
channel for reporting Single-Home drift.

## Worked example — the dispatch bracket

A dispatched implementer whose brief has `notepad_topic: TASK-114`,
`skills_required: [agent-protocol, <persona>-conventions]`:

```text
1. python3 .memory/log.py notepad list --topic TASK-114        # first action
2. Skill <persona>-conventions                                  # before first non-Read tool
3. codebase_search "the symbol named in the brief"              # SocratiCode before any grep
4. <Read → Edit within the allowed surface only>
5. <run the persona's gate commands; capture verbatim output>
6. python3 .memory/log.py notepad add --topic TASK-114 --agent <persona> \
     --note "<insight ≤500 chars — never 'completed'>" --kind gotcha   # last action
7. emit completion marker + envelope
```

The non-obvious deltas: step 1 before ANY other action, step 2 before the first non-Read
tool (not lazily), step 3's SocratiCode call unblocks the grep gate, and step 6 is the LAST
thing before the marker — a late notepad write is a skipped one.

## Decision path

| Condition | What to do |
|---|---|
| Task is T0 (docs/config) or T1 (trivial single-file) | Skip the notepad bracket, RCA, and Friction unless there's a real signal; return the LEAN envelope (marker + `files_changed` + `verification_result`). |
| Task is T2 / error-fix / multi-phase / risky | Full ceremony: notepad bracket (list → work → add), RCA where applicable, full envelope. |
| Tier is unstated or ambiguous | Ask the orchestrator's brief — it names the tier; never guess down to LEAN. |
| Work needed is outside your declared write surface | Return `## NEXUS:NEEDS-DECISION` naming the persona that owns it — never reach for it yourself, never silently edit. |
| A path you must touch is explicitly forbidden by your agent file's deny-tail | `## NEXUS:NEEDS-DECISION` naming the file — never a silent edit, even if the brief told you to touch it. |
| An attempted write falls outside your allowed set entirely (not just brief-forbidden) | Stop; return `## NEXUS:BLOCKED` with `attempted_path`. |
| The orchestrator itself blocked/confused/stalled you (gate DENY, forced REVISE, wrong-fit persona, missing context) | Call `nexus_submit_feedback` / `log.py feedback add` — no permission needed, this is also the Single-Home-drift reporting channel. |
| Two docs disagree on a hoisted block's content | The declared master wins; report the drift via Friction Signals — never silently follow either copy. |

Default when none of the rows match: treat the task as T2 (full ceremony) — under-ceremony is the costlier failure mode.

## References

- `references/constitution-persona-excerpt.md` — the PLEXUS-scoped excerpt: read when
  dispatched on this meta-repo and you need the "why" behind a CONTRACT rule, a
  verification tier (T0/T1/T2), or the test-first / root-cause / no-deferral /
  branch-discipline articles. Master: `docs/CONSTITUTION.md` (Plexus/Core rendering).
- `references/target-constitution-persona-excerpt.md` — the TARGET-scoped excerpt
  (adds the Target-only single-writer / schema-lock / idempotency / session-branch
  articles): read when dispatched on a product install, or when authoring/reviewing
  content that ships to one. Master: `nexus-package/docs/CONSTITUTION.md`.
- `references/output-envelope.md` — read when assembling your return JSON; it holds the
  verbatim envelope every field of which is mandatory. Master: `docs/agents/CONTRACT.md`.
- `docs/agents/CONTRACT.md` — read when you need the completion-marker vocabulary
  (non-normative), the `status ↔ completion_marker` mapping, or the routing state machine.
  This is the single physical home of the markers; this skill deliberately does not restate
  the marker table. Its "Typed return envelope (CANONICAL — F1-08 cutover)" subsection is the
  single home of the canonical JSON-block emission rule stated above (field list + DONE/BLOCKED
  examples).
- `references/s12-never-fabricate.md` — read when writing `verification_result`: never
  fabricate a green; paste the verbatim command output and exit code, or return BLOCKED.
