---
name: verification
description: "Deterministic-first, two-phase Lens verification (lint → type-check
  → tests → semantic) plus DEC-030 verify-phase decomposition (KICK/FAN/JOIN) for
  structuring the verify stage of a Workflow. Use when running or authoring a Lens/
  lens-fast validation, when structuring the verify or release-gate phase of any
  Workflow script, or when diagnosing a stuck single-agent verification that keeps
  timing out. Do NOT use for the `--sync`/`--check` build_snapshot ladder or its 6
  gotchas — `deployable-engineering` owns that. Do NOT use for stub-authoring TDD
  rules — `tdd-core` owns those."
metadata: {tier: sonnet, token_budget: 1600, injectable: true}
---

# Verification

## When this fires
Any Lens/lens-fast validation dispatch, or authoring/repairing a Workflow's
verify phase. One agent running the full gate gauntlet serially → decompose
per this skill. A verify phase that keeps restarting from scratch on
timeout → almost always a monolithic gate that should be backgrounded (LSN-008).

## Rules

- **Redesign depth cap ENDED (DEC-072, 2026-07-14).** DEC-039's
  light-Lens-only cap was scoped to the redesign and ended with it. What
  STAYS (velocity-proven, DEC-068): deterministic-first ordering, the tier
  table below, T1 row-optional, RCA advisory (DEC-028). The DEC-029
  structural floor (>=1 `agent_validated='lens'` row before a code DONE)
  binds unchanged. T2 audits are targeted-full again — depth is no longer
  capped to the LIGHT lane. Docs/config (T0) still needs no Lens row.
- **Classify the tier FIRST, every dispatch (DEC-068 tier table).**

  | Tier | Condition | Lens row |
  |---|---|---|
  | T0 | docs/config only, no code path | none required |
  | T1 | exactly one file, non-gated prefix, no subprocess/eval/exec/os.system/socket/requests/urllib/http/curl probe hit, deterministic gates GREEN | LIGHT lane row is OPTIONAL (owner-approved TRADE, DEC-068) — a green deterministic gate alone may satisfy the leg; a light row may still be written when useful |
  | T2 | multi-file OR gated prefix OR probe hit OR any ambiguity | targeted full — a real verdict row is required, keyed to `files_changed` |

  (Historical: during the redesign, DEC-039 capped T2 audit depth to the
  LIGHT lane; that cap ended with DEC-072, 2026-07-14 — T2 is targeted-full.)
- **T1 missing-row is advisory, not a hard deny (DEC-068).** `lens-gate.sh`
  treats a T1 leg with no Lens row as a WARN (proceed), not a block — the row
  is optional on a green deterministic single-file change, never mandatory.
- **T1-exempt audit trail (DEC-093, recorded 2026-07-17).** When a T1 leg's
  Lens row is legitimately skipped, the ORCHESTRATOR SHOULD write the
  distinguishing `validation_log` row itself: `agent_validated='orchestrator'`,
  `verdict='PASS (T1-exempt: green deterministic)'`, citing the deterministic
  gate evidence — so a later audit can tell "intentionally exempted" from
  "forgotten" without requiring a full Lens dispatch. SHOULD, not MUST — and
  Lens remains the sole writer of `agent_validated='lens'` rows; this is a
  distinct row type, never a Lens substitute.
- **Deterministic must fully pass before semantic begins.** If any
  `deterministic.<key>.exit_code != 0`, verdict is FAIL — return immediately
  with the failing output as the issue. No semantic review on a failing build.
- **Lint-detection is a decision, not a guess** — run exactly one branch: (1)
  `package.json` has a `"lint"` script → run it; (2) no script but an eslint
  config exists at project root → `npx eslint . --max-warnings=0`; (3) neither
  → emit `lint.status: "not_configured"` explicitly (never silently skipped;
  N/A does NOT degrade the verdict to FAIL). A non-zero exit on branch 1/2 is
  ALWAYS FAIL — N/A requires confirming zero lint tooling, not a failed run.
- **A verify/release-gate phase MUST decompose into bounded parallel legs
  (DEC-030)** — never one agent running the full gauntlet serially. The
  single heaviest gate (e.g. a multi-minute build/snapshot check or a full
  test suite) runs at the **orchestrator level via backgrounded Bash**, never
  inside a workflow agent. Each verify leg gets a stall budget the ORCHESTRATOR
  enforces by watching liveness (transcript mtime / journal progress — there is
  no per-agent timeout opt in the script API); on expiry, `TaskStop` + escalate
  — do not retry the same config
  ("same-knob-harder"). Repair loops re-run **only the failed leg**, never
  the full gauntlet.
- **Evidence is verbatim** — file:line, test name, or literal command output.
  "I checked X" is not evidence; paraphrase of command output is a FAIL.
- **Cardinal rules:** never lower the bar to reach PASS. Even one FAIL →
  verdict FAIL. Lens cannot write code (`disallowedTools: Edit, Write,
  NotebookEdit`). Don't re-run the same command 3x hunting different output.
- **Spec/impl disagreements are logged as conflicts, never silently resolved**
  toward the implementation — the orchestrator decides which side updates.
- **Realist check:** theoretical worst-cases are not blockers unless data
  loss, security exposure, or a contract violation — everything else is an
  Open Question, not a FAIL.
- **NEXUS:REVISE re-runs ONLY the failed leg (DEC-030 enforced, DEC-068
  restated, DEC-095 sharpened).** A repair loop after a partial FAIL never
  re-runs the full gauntlet — it re-dispatches the fixer against the failing
  leg alone and re-runs only that leg's check(s). This is the same rule as
  the KICK/FAN/JOIN repair-loop note below; DEC-068 makes it a first-class
  Rule so it is never read as decomposition-only advice. DEC-095 sharpens it
  for tests specifically: the re-run is the failed pytest command ONLY —
  never a broader sweep, and never a re-run of a command that already passed
  in an earlier round of the same leg.
- **Advisory-only results NEVER escalate a verdict (DEC-068).** A finding that
  is explicitly advisory/shadow-only (e.g. the C2 `skills_loaded` check below,
  a root-cause-gate nudge, a read-injection-scanner flag) may add a note to
  the verdict summary but MUST NOT flip a PASS to FAIL/PARTIAL or otherwise
  change the verdict — only a deterministic gate failure or a real semantic
  FAIL finding may do that. Conflating "advisory" with "blocking" is itself a
  bar-lowering-in-reverse bug (over-blocking on a nudge is as wrong as
  under-blocking on a real FAIL).
- **Lens verify template gains a test-quality criterion (DEC-068).** When
  reviewing generated-artifact tests, flag implementation-coupled assertions —
  a test that asserts on private/internal implementation details (mock call
  counts, internal method names, private state) rather than observable
  behavior/properties is a finding, even if it currently passes. See
  `Skill tdd-core`'s "assert properties, not prose" rule for the authoring
  side of this same criterion.
- **One test execution per leg per round (DEC-095, owner-approved
  2026-07-17).** The IMPLEMENTER runs the targeted pytest for its own
  `files_changed` and pastes VERBATIM output (exit code + summary line) into
  its return. `lens-fast` VERIFIES that evidence — rc present, summary line
  present, plausible, keyed to `files_changed` — and runs only NON-pytest
  deterministic checks (py_compile/syntax, lint, hook-syntax, drift checks);
  it does NOT re-run the pytest the implementer already ran. `lens` is
  SEMANTIC-ONLY on the test dimension: it re-runs tests ONLY on a stated
  suspicion, and that suspicion MUST be named explicitly in the verdict — a
  routine re-run is scope creep, not diligence. A `NEXUS:REVISE` round
  re-runs ONLY the failed command (the existing rule below is strengthened,
  not replaced, by this: never re-run a command that already passed earlier
  in the SAME leg).
- **Evidence-verify is not evidence-trust (DEC-095).** The never-fabricate/
  verbatim-evidence rule below is UNCHANGED — the implementer's pasted output
  must still be literal command output, never paraphrase. What changes is who
  RE-RUNS: `lens-fast` MAY spot-re-run AT MOST ONE fast test file when the
  pasted evidence smells wrong (rc/summary mismatch, missing summary line, a
  runtime implausibly fast for the file's size) — the sole exception to
  "no pytest re-run," and it must be named as an exception, not exercised
  routinely.
- **Broad `-k` sweeps retired (DEC-095).** Targeted test files keyed to
  `files_changed` ONLY. A sweep beyond `files_changed` (a directory-wide `-k`
  pattern, a full-suite run "just to be safe") requires a stated reason in
  the verdict — e.g. "the fix touches a shared fixture, sweeping its
  consumers."
- **testmon lane is the RECOMMENDED per-leg targeted-test mechanism
  (F1-05/DEC-069, adopted DEC-095)** where a testmon `.testmondata` file
  exists for the leg's tree. Exact invocation (see
  `nexus-foundation/tools/verify_testmon.sh` and `run_testmon_fast_gate` in
  `tools/build_snapshot.sh`): `-m "not slow"
  --testmon-forceselect`, xdist OFF (parallel workers break testmon's
  coverage-map aggregation — see build_snapshot.sh's F1-05 comments).
  Fallback when no datafile exists: files_changed-targeted pytest (the
  existing per-item bar, unchanged). **Contention caveat:** a `.testmondata`
  file is SHARED STATE — parallel legs (worktree waves, concurrent
  dispatches) MUST NOT point at the same datafile; use a per-worktree
  datafile path, or fall back to files_changed-targeted pytest for the
  duration of a parallel wave. The full `tools/build_snapshot.sh --check`
  release gate is UNCHANGED by this lane — it never sets `TESTMON_DATAFILE` /
  never passes `--testmon-forceselect`, and stays the single once-only
  release gate (never `--fast` as a substitute for it).
- **Unchanged by DEC-095:** the DEC-029 Lens-row floor (>=1
  `agent_validated='lens'` row before a code DONE), the DEC-068 tier table
  above, and the "Evidence is verbatim" rule above — "I checked X" or a
  paraphrase is still a FAIL.
- **Known-failures manifest (DEC-095/TASK-092).** Before re-running a red
  suite (e.g. the R4e-live hook suite), consult
  `nexus-foundation/plans/artifacts/known-failures.json` FIRST — failures
  listed there are PRE-EXISTING (TASK-092's true-failing-list
  re-derivation), never attributed to the current leg, never re-derived from
  scratch. The manifest is deleted when TASK-092 lands.

## Why decomposition matters — LSN-008 (incident grounding, DEC-030)

Observed failure: a single verify-phase agent ran the entire gate suite
(lint, tests, a multi-minute build/snapshot check) serially. When its time
budget expired mid-gauntlet, the harness restarted it from scratch —
including the expensive gate already run. This looped **over an hour**. Fix:
background the heavy gate at the orchestrator level; run lightweight checks
in bounded parallel agents. Never let a multi-minute gate live inside a
single agent's serial checklist.

## Worked example — KICK / FAN / JOIN decomposition

```
# KICK — background the heavy gate at ORCHESTRATOR level (chat thread, not
# inside a phase/agent). The echo IS the pipe-safe rc capture (last line):
# ( tools/build_snapshot.sh --check; echo "SNAPSHOT_RC=$?" ) > .memory/verify-snapshot.out 2>&1
# Bash(run_in_background=true) returns a shell id immediately — don't block.

# FAN — launch fast gates in parallel while the heavy gate runs.
# Real script API: phase(title) is a STATEMENT; agent(promptString, opts) —
# opts.label + opts.phase give each leg its OWN live row in the side panel.
phase('Verify')
await parallel([
  () => agent('lens-fast brief: run lint — report findings only',
              { agentType: 'lens-fast', label: 'lens-fast:lint', phase: 'Verify' }),
  () => agent('lens-fast brief: run the targeted test file only',
              { agentType: 'lens-fast', label: 'lens-fast:tests', phase: 'Verify' }),
])

# JOIN — after fast legs return, harvest the backgrounded gate:
# grep -E '^SNAPSHOT_RC=' .memory/verify-snapshot.out  -> SNAPSHOT_RC=0 (pass)
# Absent line = still running — never mark done on a partial file.
# Phase is GREEN iff every fast leg passed AND SNAPSHOT_RC==0.
```

Repair loop re-runs ONLY the failed leg (e.g. if only the test leg failed,
dispatch the fixer + re-run only that test command — do not re-run lint if
it was already green and the fixer never touched lint-scoped files).

## C2 — `skills_loaded` check (ADVISORY / SHADOW ONLY in R2)

lens-fast's deterministic gate matrix gains one new check: skills-loaded
coverage, verified by comparing the dispatch's return-envelope self-report
against real event rows in `skill_load_events` (columns: `id`,
`dispatch_id`, `skill_id`, `ts`, `byte_len`, `recorded_at`) rather than
trusting the model's bare claim that it loaded a skill.

This check is **ADVISORY / SHADOW ONLY in R2** — a missing load-event row
surfaces as a `semantic`/finding-style note only; the dispatch's result is
unaffected either way. Enforcement is explicitly out of scope here and is
reserved for **R3-T07/T08**. Do not wire an enforcement path against this
check in R2.

## Decision path

| Leg shape (observable condition) | Route |
|---|---|
| T0 — docs/config only, no code path | No Lens row required. |
| T1 — exactly one file, non-gated prefix, no subprocess/eval/exec/os.system/socket/requests/urllib/http/curl probe hit, deterministic gates GREEN | Lens row is OPTIONAL (DEC-068 TRADE) — a green deterministic gate alone may satisfy the leg; if written, it's the LIGHT lane (deterministic gates + a brief 2-3 paragraph semantic pass). |
| T2 — multi-file OR gated prefix OR probe hit OR any ambiguity | Targeted full — a real verdict row keyed to `files_changed` is required (DEC-068; the DEC-039 redesign-era LIGHT-lane depth cap ended with DEC-072). |
| T1 leg's Lens row is legitimately skipped (green deterministic gate) | Orchestrator (not Lens) writes a `validation_log` row: `agent_validated='orchestrator'`, `verdict='PASS (T1-exempt: green deterministic)'` — distinguishes exemption from omission for later audit. |
| Any `deterministic.<key>.exit_code != 0` | Verdict is FAIL immediately — no semantic review begins on a failing build. |
| `package.json` has a `"lint"` script | Run it (lint branch 1). |
| No lint script, but an eslint config exists at project root | `npx eslint . --max-warnings=0` (lint branch 2). |
| Neither a lint script nor an eslint config | `lint.status: "not_configured"` explicitly — never silently skipped, and N/A does NOT degrade the verdict to FAIL. |
| A verify/release-gate phase would run the full gate gauntlet in one serial agent | Decompose into bounded parallel legs (DEC-030); background the single heaviest gate at the orchestrator level via Bash, never inside a workflow agent. |
| A repair loop / `NEXUS:REVISE` is re-running after a partial FAIL | Re-run ONLY the failed leg — never the full gauntlet (DEC-030, restated DEC-068); for a test leg, re-run the failed pytest command only, never a broader sweep, never a command that already passed this leg (DEC-095). |
| A leg needs its targeted test run | IMPLEMENTER runs it ONCE and pastes verbatim output; `lens-fast` VERIFIES that evidence (rc + summary line present, plausible, keyed to `files_changed`) rather than re-running it; `lens` re-runs a test only on a STATED suspicion named in the verdict (DEC-095). |
| A testmon `.testmondata` file exists for the leg's tree | Use the testmon lane as the RECOMMENDED targeted-run mechanism: `-m "not slow" --testmon-forceselect`, xdist off; fall back to files_changed-targeted pytest if no datafile exists, or if the leg runs in a parallel wave that would share a datafile with another leg (F1-05/DEC-069, adopted DEC-095). |
| `lens-fast`'s pasted-evidence check smells wrong (rc/summary mismatch, missing summary line, implausible runtime) | MAY spot-re-run AT MOST ONE fast test file — the sole exception to the no-pytest-rerun rule; name the smell explicitly rather than exercising it routinely (DEC-095). |
| About to re-run a red suite (e.g. the R4e-live hook suite) | Consult `nexus-foundation/plans/artifacts/known-failures.json` FIRST — listed failures are PRE-EXISTING (TASK-092), never attributed to the current leg, never re-derived (DEC-095). |
| A sweep beyond `files_changed` seems warranted | State the reason explicitly in the verdict — an unstated broad `-k` sweep is retired (DEC-095). |
| A finding is explicitly advisory/shadow-only (C2 check, root-cause-gate nudge, read-injection-scanner flag) | Note it in the verdict summary; NEVER escalate the verdict itself (DEC-068) — only a deterministic gate failure or a real semantic FAIL may change PASS to FAIL/PARTIAL. |
| `skill_load_events` shows a missing load-event row for a self-reported skill (C2 check) | Surface as an advisory/shadow finding-note only in R2 — does NOT affect the dispatch's result; do not wire enforcement (reserved for R3-T07/T08). |
| Reviewing a generated-artifact test | Flag implementation-coupled assertions (mock call counts, private state, internal method names) as a finding even if currently passing — DEC-068 test-quality criterion; see `Skill tdd-core`'s authoring-side "assert properties, not prose" rule. |

Default when none of the rows match: classify the leg as T2 — the conservative default.

## References
- `references/gate-matrix.md` — read when checking exact lens-gate.sh
  block/allow conditions, the N-distinct-lens-row (R1-T08) rule, or the C2
  skills-loaded check's shipped columns (DERIVED digest of
  `docs/ORCHESTRATOR-GATES.md` — that file is the master on any conflict).
- `references/lens-verdict-schema.json` — read when constructing or
  validating a Lens verdict's JSON shape.
- `nexus-foundation/tools/verify_testmon.sh` / `run_testmon_fast_gate` in
  `tools/build_snapshot.sh` — canonical for the testmon lane's exact flags
  (`-m "not slow" --testmon-forceselect`, xdist off) — DEC-095 points here
  rather than restating the invocation.
- `nexus-foundation/plans/artifacts/known-failures.json` — the tracked
  known-failures manifest (DEC-095/TASK-092); consult before re-running a
  red suite so a pre-existing failure is never mistaken for a regression
  introduced by the current leg.
- `Skill review-panel` — the sharded re-derivation panel for output-verify
  legs whose risk score triggers one (DEC-060/061); template + phase-spec
  at `docs/agents/templates/review-workflow.md` /
  `review-phase-spec.json`. Dispatch-wiring is R4 scope (TASK-017) — the
  panel is not yet orchestrator-routed.
- `Skill deployable-engineering` — CANONICAL for the `--sync`/`--check`
  build_snapshot ladder and its gotchas; this skill covers phase
  decomposition only, not which flag to run when.
- `Skill tdd-core` — stub-authoring rules and the xfail(strict) prohibition.
