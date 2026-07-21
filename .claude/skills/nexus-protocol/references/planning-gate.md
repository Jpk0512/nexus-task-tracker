# Planning Gate — Full Detail

Full text of the 7-item planning gate, forced submission, the bootstrap sequence, and
MACRO_NODE hierarchical planning. `SKILL.md` §4 keeps only the checklist itself.

## The 7-item planning gate

Before implementation begins on any Standard or Complex feature, all 7 items must pass:

```
[ ] 1. Spec file exists at docs/features/FEAT-XXX.md
[ ] 2. GWT acceptance criteria written and accepted by user
[ ] 3. No [NEEDS CLARIFICATION] markers remain in spec
[ ] 4. Constitution check: all articles verified against spec
[ ] 5. SocratiCode semantic search run for all affected areas
[ ] 6. DB schema locked in spec (required if feature touches persistent storage)
[ ] 7. Test stubs written by the test-author persona and confirmed failing
```

**Run the machine validator** (catches items 1–4 and 6–7 automatically):
```bash
python3 .memory/log.py planning-gate check --feat FEAT-XXX
```

Item 5 (SocratiCode search) requires manual confirmation — run a `codebase_search` before
checking it off.

## Forced submission (rejects on incomplete plans)

For Standard and Complex features, the seven-item check above is paired with a structured
`submit` step. Submitting a plan that's missing any required field is rejected at the CLI
layer — no implementer is dispatched.

```bash
python3 .memory/log.py planning-gate submit --feat FEAT-XXX --json '{
  "feat": "FEAT-XXX",
  "scope_summary": "...",
  "files_touched_estimate": <int>,
  "acceptance_criteria": ["Given X, when Y, then Z", "..."],
  "constitution_articles_verified": ["I", "III", "V"],
  "risks": ["..."],
  "rollback_plan": "git revert <sha>  |  feature-flag off  |  ..."
}'
```

Return: `{"gate": "ACCEPTED", ...}` (logged as a `context_log` row with
`action_type=planning-gate-submit`) OR `{"gate": "REJECTED", "missing_fields": [...],
"type_errors": [...]}` (no DB write — fix and resubmit).

Simple class skips submit. Standard/Complex MUST submit before the first implementer
dispatch.

## Bootstrap — authoring the gate's own prerequisites

Planning-gate items 1 and 7 (the spec file and failing test stubs) are the gate's own
prerequisites — but their authors are code-writing personas: the schema/spec-owning
persona writes the spec, the test-author persona writes the stubs. This is a
chicken-and-egg: a Standard/Complex dispatch to a code-writing persona requires an
ACCEPTED planning-gate row first, yet the spec and stubs that satisfy the gate do not
exist yet.

The escape: `broker-gate.py` computes `is_feature_code = task_tier in {"standard",
"complex"} and _is_code_writing(persona, intent)`. A **SIMPLE-tier** dispatch is never
feature-code regardless of persona, so it skips the planning-gate requirement entirely.

Bootstrap sequence (3 steps):

1. Author the spec (`docs/features/FEAT-XXX.md`) and failing test stubs at **SIMPLE
   tier** — the planning-gate does not apply, so the spec-author and test-author personas
   can be dispatched without a prior ACCEPTED row.
2. Run `planning-gate submit` → verify the return is `{"gate": "ACCEPTED", ...}`.
3. Dispatch implementers at Standard/Complex tier — the accepted planning-gate row now
   exists and the gate passes.

## MACRO_NODE — hierarchical planning for multi-phase features

When a feature naturally splits into phases (e.g. schema design → migration → ingestion →
exposure), use the **MACRO_NODE pattern**:

1. **Macro plan** (one `planning-gate submit` call against the whole feature)
   ```json
   {
     "feat": "FEAT-XXX",
     "scope_summary": "...",
     "macro_phases": [
       {"id": "A", "title": "...", "owner": "<persona>", "exits_when": "schema doc approved"},
       {"id": "B", "title": "...", "owner": "<persona>", "exits_when": "migration green"},
       {"id": "C", "title": "...", "owner": "<persona>", "exits_when": "ingestion lands"}
     ],
     ...
   }
   ```
2. **Per-phase brief** is a fresh `Task` call using only the artifacts that phase needs.
   The brief's `context_files` includes the prior phase's handoff doc.
3. **Inter-phase handoff** is a 10–20 line doc at
   `.memory/handoffs/FEAT-XXX/phase-<id>.md`:
   - What landed
   - What was rejected and why
   - What the next phase depends on (file paths + symbol names)
   - Open questions the next phase must resolve
4. **Nexus owns the macro state** — never delegate phase sequencing to a sub-agent. The
   orchestrator decides when phase N is "done enough" to start N+1.

**Anti-pattern:** A single brief like "implement FEAT-XXX end-to-end." That's a MACRO not
handed to MACRO_NODE, and it almost always blows up at the third surprise.

A worked MACRO_NODE example: `examples/planning-gate-and-macro-node.md`.
