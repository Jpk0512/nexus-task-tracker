<!-- DERIVED: master=docs/ORCHESTRATOR-GATES.md -->

# Gate Matrix (Lens / verification digest)

Digest of the Lens-relevant gate — `docs/ORCHESTRATOR-GATES.md` §6 is the full
authority; this file restates only what a Lens/lens-fast dispatch needs to
self-check. If this digest and the master ever disagree, the master wins —
update this file, don't trust it over the source.

## `lens-gate.sh` — 3-tier Lens-before-DONE (CONTRACT Rule 17, DEC-029)

**Trigger.** `SubagentStop`, only when the return carries `## NEXUS:DONE`.

**Tiers:**
- **T0** (docs/config only, no code path) — no Lens row required, gate
  silent-passes.
- **T1** (trivial: exactly one file, non-gated prefix, no
  subprocess/eval/exec/os.system/socket/requests/urllib/http/curl
  content-probe hit) — LIGHT lane, but a REAL verdict row with
  `agent_validated='lens'` is still required, verdict PASS.
- **T2** (multi-file OR any gated prefix OR content-probe hit OR
  classification ambiguity) — FULL deep audit; hard-deny behavior applies.
  **DEFAULT-DENY on ambiguity → T2.**

**Blocks (hard deny, exit 2).** A gated code-writing persona emitting
`## NEXUS:DONE` whose `files_changed` touches a gated source prefix
(`nexus-broker/`, `nexus-package/`, `prism/`, `.claude/hooks/`, `.memory/`,
`bin/`) without a matching `validation_log` row
(`agent_validated='lens'`, `verdict='PASS'`, written in the last hour for
the same task hash) is blocked. T1 single-file non-gated work still needs
the light-lane row. Fails closed (blocks) if the validation DB is
unreadable. Read-only personas (scout, lens) and docs/design-only personas
are excluded.

**How to satisfy.** Route the artifact to Lens (a distinct verifier, never
the author). Lens writes:
```
python3 .memory/log.py validation add --agent lens --target <agent> \
  --task-hash <hash> --verdict PASS|PARTIAL|FAIL --summary "..."
```
Deterministic checks (lint → type-check → tests) must be green *before* the
semantic verdict is consulted — no semantic review on a failing build.

**R1-T08 — N-distinct-lens-row requirement (additive, never replaces the
floor above).** The floor (`_has_lens_validation`) is unchanged: >=1
in-window PASS row at ANY tier for `(target_agent, task_hash)` still gates
every T1/T2 dispatch. On top of it, a **T2** dispatch additionally requires
a PASS row whose `lens_type` column equals `T2` — a stale or lower-tier PASS
row inside the same window no longer silently satisfies a full-audit
requirement. `lens_type`/`risk_tier` are nullable additive columns on
`validation_log`; NULL never matches a required tier.

**How to satisfy (v2).** For a T2 dispatch, the `validation add` call must
pass `--lens-type T2 --risk-tier T2` (not just `--verdict PASS`):
```
python3 .memory/log.py validation add --agent lens --target <agent> \
  --task-hash <hash> --verdict PASS --lens-type T2 --risk-tier T2 --summary "..."
```

**Third enforcement point — Stop-event backstop
(`lens-tier-backstop.sh`, advisory-only).** Re-derives the v2 invariant
session-wide (not per-dispatch): for every in-window `risk_tier='T2'` PASS
row, confirms a matching `lens_type='T2'` PASS row exists for the same
`(target_agent, task_or_brief_hash)`. This is a safety net for a dispatch
whose SubagentStop gate check never fired — Stop cannot block (no
implementer left to re-dispatch to), so a gap surfaces as a WARN for the
orchestrator/user to investigate, never a silent pass. Do not silently
swallow this WARN — treat it as a signal to investigate before trusting
that session's DONE markers.

**Bypass token.** None. Test override: `_HOOK_DB_PATH`.

## C2 — `skills_loaded` reconciliation check (ADVISORY / SHADOW only, R2)

New deterministic check added to lens-fast's gate matrix: skills-loaded
coverage — comparing the return envelope's self-reported skills-loaded field
against the event-sourced `skill_load_events` table (columns: `id`,
`dispatch_id`, `skill_id`, `ts`, `byte_len`, `recorded_at`; `dispatch_id` is
an advisory, not enforced, FK to `dispatch_telemetry`) to confirm every
skill the dispatch's brief called for actually has a matching load-event
row. This replaces trusting a model's bare self-report of "I loaded skill X"
with a real event row.

**This check is ADVISORY / SHADOW ONLY in R2.** A missing load-event row for
a skill the brief called for is reported as a finding only — the dispatch's
result is unaffected either way. Enforcement (turning this comparison into a
hard gate) is explicitly out of scope for R2 and is reserved for
**R3-T07/T08**. Do not wire any enforcement path, hook edit, or new table
for this check in R2 — it is a reporting-only addition to the deterministic
gate matrix.

## Cross-reference

- Full authority for every hook/gate in the corpus (not just Lens):
  `docs/ORCHESTRATOR-GATES.md`.
- Verdict shape this gate matrix feeds into: `../references/lens-verdict-schema.json`.
- Phase-decomposition mechanics (KICK/FAN/JOIN) that run these gates in
  bounded parallel legs: `../SKILL.md`.
