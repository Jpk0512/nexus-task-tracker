# Install-Aware Persona Availability

`SKILL.md` keeps only the one-line rule; this is the full remap table and the
verify-before-dispatch procedure.

**Stack-conditional personas exist ONLY when their stack condition is present at
install time** (see `nexus-package/docs/STACK-PROFILE.md` for the exact per-persona
condition — e.g. a Python data-pipeline persona ships only when a Python backend or an
ingestion layer is detected; an async-worker persona ships only when a worker layer is
detected). They are NOT registered agent files in an install whose stack profile doesn't
match. Dispatching an unregistered `agentType` hard-fails mid-workflow with no recovery
path.

**Before dispatching any persona, verify it is registered:**
- The canonical roster is listed in `docs/agents/TEAM.md`.
- The agent file must exist at `.claude/agents/<persona>.md`.
- A missing agent file = the persona is NOT installed = dispatch will hard-fail.

## Remap table when a stack-conditional persona is absent

| Work type that would need the absent persona | Map to instead |
|---|---|
| Data transforms / DB writes / embeddings (data-pipeline persona absent) | The server-side implementer persona (read-side) + the wiring persona |
| Async workers / external API clients (async-worker persona absent) | The server-side implementer persona (server actions) + the wiring persona (auth/client wiring) |
| Backend-language test authoring (that language's test-author persona absent) | The remaining installed test-author persona, scoped to what it can actually cover |

If the work genuinely requires logic in a stack the installed roster does not cover,
surface a `## NEXUS:NEEDS-DECISION` — do not silently remap logic that belongs in one
stack onto a persona built for another.

## Classification decision tree (full)

```
Task arrives →
├── Is it a bug fix / config / single obvious change touching ≤2 files (already read)?
│   YES → Simple Task Bypass. Handle inline. No ceremony.
│   NO  → continue
├── Does it span >5 files OR multi-domain OR ambiguous scope?
│   YES → Complex. Spawn Scout first. Then dispatch the parallel
│         implementation as a dynamic Workflow (Task fan-out under a
│         shared TaskList), one owned task per domain — NOT a raw
│         multi-Task fan-out without a verify stage. See nexus-protocol.
│         Each code-writing teammate gets an explicit Lens verify stage.
│   NO  → Standard. Single persona per routing table (if it splits into
│         ≥2 independent slices, escalate to a dynamic Workflow too).
│
└── Standard or Complex →
    1. Run planning gate (skill: nexus-protocol)
    2. Reflect (spawn Scout for 5-bullet reflection)
    3. Delegate per CONTRACT.md (skill: contract-schema)
    4. Review completion marker (skill: contract-schema)
    5. Run db_log_cmds
```
