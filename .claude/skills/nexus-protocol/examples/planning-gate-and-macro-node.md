# Worked example — planning gate + MACRO_NODE

**Scenario:** a feature naturally splits into phases: schema design → migration →
ingestion → search exposure.

**Macro plan (one submit against the whole feature):**
```bash
python3 .memory/log.py planning-gate submit --feat FEAT-041 --json '{
  "feat": "FEAT-041",
  "scope_summary": "Add full-text search over ingested documents",
  "files_touched_estimate": 12,
  "acceptance_criteria": ["Given a search query, when submitted, then ranked results return in <200ms"],
  "constitution_articles_verified": ["I", "V"],
  "risks": ["schema migration touches a table with existing data"],
  "rollback_plan": "git revert <sha>",
  "macro_phases": [
    {"id": "A", "title": "Schema design for the search index", "owner": "schema-persona", "exits_when": "schema doc approved"},
    {"id": "B", "title": "Migration + index build", "owner": "data-persona", "exits_when": "migration green"},
    {"id": "C", "title": "Search exposure via API route", "owner": "server-persona", "exits_when": "endpoint returns ranked results"}
  ]
}'
```
Returns `{"gate": "ACCEPTED", ...}`.

**Per-phase brief (Phase A, a fresh Task call):**
```
agent_persona: <schema-persona>
goal: "Design the search-index schema for FEAT-041 Phase A."
context_files: ["docs/features/FEAT-041.md"]
notepad_topic: FEAT-041
```

**Inter-phase handoff (written by Phase A, read by Phase B's brief):**
`.memory/handoffs/FEAT-041/phase-A.md` — 10-20 lines: the chosen schema, what alternatives
were rejected (and why), the exact table/column names Phase B's migration must reference,
and any open question ("should the index be rebuilt incrementally or on a full
re-ingest?" — resolved in Phase A, not deferred).

**Phase B's brief includes the handoff:**
```
agent_persona: <data-persona>
goal: "Implement the migration for the schema locked in Phase A."
context_files: ["docs/features/FEAT-041.md", ".memory/handoffs/FEAT-041/phase-A.md"]
notepad_topic: FEAT-041
```

**Anti-pattern avoided:** a single brief "implement FEAT-041 end-to-end" would have
blown up at the third surprise (e.g. discovering mid-implementation that the migration
needs a backfill strategy the spec never addressed) — MACRO_NODE surfaces that decision
at the Phase A/B boundary instead, where it's cheap to resolve.
