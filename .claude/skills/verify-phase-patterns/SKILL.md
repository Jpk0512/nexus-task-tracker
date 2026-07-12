---
name: verify-phase-patterns
description: DEC-030 verify-phase decomposition recipes — how to structure the Lens/verification phase of a Workflow. Covers parallel lint/type/test branches, backgrounding the heaviest gate at the orchestrator level, per-agent stall budgets, repair-loop re-runs of only the failed leg, and the monolithic-vs-decomposed before/after contrast (LSN-008). Load before authoring a verify phase in any Workflow script, before setting up a release-gate phase, or when diagnosing a stuck single-agent verification that keeps timing out.
---

# Verify-Phase Patterns (DEC-030)

Reference for structuring the verification phase inside a Nexus Workflow.
Source of truth: **DEC-030 (Constitution Article XIII / 2026-06-25)** and the
observed LSN-008 failure (1hr+ single-agent thrash from a monolithic verify gate).

## The core rule (DEC-030)

A verify or release-gate phase MUST be decomposed into **several bounded parallel
agents**, each owning ONE check. NEVER one agent running the full gauntlet serially.

Three corollaries:
1. The **single heaviest gate** (e.g. `tools/build_snapshot.sh --check`, full
   `uv run pytest`) runs at the **orchestrator level via backgrounded Bash**, NOT
   inside a workflow agent — a multi-minute gate inside an agent causes thrash.
2. Each verify agent carries a **stall/time budget** — on expiry, kill the agent
   and escalate; do NOT retry indefinitely.
3. **Repair loops re-run ONLY the failed leg**, never the full gauntlet. No
   redundant gates: `build_snapshot --check` already runs pytest; do NOT also
   schedule a separate full-suite run.

## Why this matters — LSN-008

Observed failure mode: a single verify-phase agent was given the entire gate suite
(`ruff check`, `pytest`, `build_snapshot --check`). `build_snapshot --check` takes
multiple minutes. When the agent's time budget expired, the harness restarted it
from scratch, including the expensive gate. This looped for over an hour. Fix:
decompose → the heavy gate is backgrounded by the orchestrator; lightweight checks
run in bounded parallel agents.

## Decomposition recipe

### Before (anti-pattern — monolithic)

```js
// ONE agent, full serial gauntlet — DO NOT DO THIS
phase("verify", () => agent({
  persona: "lens",
  goal: "run ruff, pytest, build_snapshot --check, and check hook imports",
  // If this times out, the harness restarts from scratch — indefinite thrash.
}));
```

### After (correct — decomposed)

**3-move mechanic (KICK → FAN → JOIN):**

```js
// Move 1 — KICK: background the heavy gate at ORCHESTRATOR level (chat thread, not inside a phase/agent).
// Bash(run_in_background=true) — appends an unambiguous rc sentinel as the LAST line.
// ( tools/build_snapshot.sh --check; echo "SNAPSHOT_RC=$?" ) > .memory/verify-snapshot.out 2>&1
// Returns a shell id immediately. Do NOT block. The echo IS the pipe-safe rc capture (gotcha #5).

// Move 2 — FAN: launch fast gates in parallel while the heavy gate runs
phase("verify", () => parallel([
  agent({ persona: "lens-fast", goal: "run uv run ruff check — report findings only",
          stall_budget_seconds: 120 }),
  agent({ persona: "lens-fast", goal: "run uv run pytest nexus-broker/tests/ -q — report findings only",
          stall_budget_seconds: 180 }),
  agent({ persona: "lens-fast", goal: "import every .claude/hooks/*.py under python3 — report exit codes",
          stall_budget_seconds: 60 }),
  // lens-fast for deterministic gates; lens for semantic review when needed
]));

// Move 3 — JOIN: after fast legs return, harvest the backgrounded gate.
// grep -E '^SNAPSHOT_RC=' .memory/verify-snapshot.out   -> SNAPSHOT_RC=0 (pass)
// If SNAPSHOT_RC line is absent, the gate is still running — do NOT mark done on a partial file.
// Synthesize: phase is GREEN iff lint.rc==0 && tsc.rc==0 && SNAPSHOT_RC==0.
```

### Stall budget enforcement

Every verify agent gets a `stall_budget_seconds` in its brief. When the budget
expires:

1. Call `TaskStop` on the agent's `taskId` / `runId`.
2. Log the stall via `python3 .memory/log.py lesson add` with the agent's
   last-known output.
3. Escalate to the user with context: which gate timed out, what its last output
   was, and the recommended manual command.

Do NOT restart the same agent with the same configuration ("same-knob-harder"). If
you restart, change the approach (smaller scope, different command flags, or
escalate to user).

## Repair loop — failed-leg-only re-run

When one verify leg fails:

```
failed_leg = "pytest"        # the one that failed

# CORRECT: re-run only the failed leg
# Substitute the implementer persona that owns the failing code:
#   "hermes"        — wiring / auth / hook fixes
#   "pipeline-data" — Python data-transform / test fixes
#   "forge-wire"    — TypeScript server / API fixes
#   "quill-py"      — Python test-authoring fixes
agent({ persona: "pipeline-data", goal: "fix the pytest failures listed in <finding_path>",
        context_files: [finding_path] })
agent({ persona: "lens-fast", goal: "re-run uv run pytest nexus-broker/tests/ -q only",
        stall_budget_seconds: 180 })

# WRONG: re-run the full gauntlet
# parallel([ruff-agent, pytest-agent, hook-import-agent]) — wastes budget on passing legs
```

Re-run ONLY the leg that failed, and ONLY against the specific files the fixer
touched. If `ruff` was green before the fix, do not re-run `ruff` unless the
fixer touched Python files.

## Verify agent size guide

| Gate | Persona | Typical stall budget | Notes |
|---|---|---|---|
| `uv run ruff check` | `lens-fast` | 90s | Fast; use `--select` to scope to touched files |
| `uv run pytest <specific file>` | `lens-fast` | 180s | Targeted; never the full suite in-agent |
| `rtk tsc` | `lens-fast` | 120s | Type-check only |
| `rtk lint` | `lens-fast` | 90s | |
| `docker compose config` | `lens-fast` | 30s | Syntax only, no containers |
| Semantic / RCA / visual | `lens` | 300s | One concern per agent |
| `build_snapshot --check` | **orchestrator Bash** | N/A — backgrounded | NEVER inside a workflow agent |
| Full pytest suite | **orchestrator Bash** | N/A — backgrounded | Only if truly required; avoid redundancy |

## No-redundancy rule

`tools/build_snapshot.sh --check` internally runs `uv run pytest`. If you have
already dispatched a targeted pytest agent, do NOT also run `build_snapshot --check`
for the same test scope — that is a redundant gate. Run `build_snapshot --check`
at the orchestrator level only when you need to validate the full snapshot
consistency (snapshot copy correctness, version stamping, install-surface tests).

## Workflow skeleton (complete verify phase)

```js
// In a standard [scout → impl → verify] workflow:

phase("verify", async () => {
  // Heavy gate: backgrounded at orchestrator level
  // (The orchestrator runs: Bash("tools/build_snapshot.sh --check", background=true))

  // Fast gates: bounded parallel agents
  const [ruffResult, pytestResult, hookResult] = await parallel([
    agent({ persona: "lens-fast",
            goal: "run uv run ruff check nexus-broker/src/ — return exit code + stdout",
            stall_budget_seconds: 90 }),
    agent({ persona: "lens-fast",
            goal: "run uv run pytest nexus-broker/tests/ -q — return exit code + stdout",
            stall_budget_seconds: 180 }),
    agent({ persona: "lens-fast",
            goal: "python3 -c 'import broker.server; import broker.state' — return exit code",
            stall_budget_seconds: 45 }),
  ]);

  // Semantic review (separate agent, separate concern)
  const semanticResult = await agent({ persona: "lens",
    goal: "semantic review of files_changed against acceptance_criteria — security + ops + new-hire passes",
    context_files: impl_files_changed,
    stall_budget_seconds: 300,
  });

  // Synthesize: if any agent reports non-zero exit, route to repair loop
  // re-run ONLY the failing leg after the fixer addresses it.
});
```

## R6 — build_snapshot wrapping: decompose, never repeat (R6)

When a Workflow's verify phase wraps `build_snapshot`, apply these rules:

**R6a — Decompose into parallel legs when repair is possible:**
```
broker-tests leg:   cd nexus-broker && uv run pytest tests/ -q
hook-tests leg:     python3 -c 'import importlib; ...' (or targeted hook test file)
package-tests leg:  cd nexus-broker && uv run pytest tests/test_install_surface.py -q
```
Run legs in parallel (separate bounded agents or backgrounded Bash). When ONE leg
fails, the repair re-runs ONLY that leg — never the full `--check` again.

**R6b — Use `--sync` for iteration, `--check` at the gate (ONCE):**
- Iterating (wave-to-wave propagate): `tools/build_snapshot.sh --sync` (~20-30s, skips heavy pytest).
- Release gate: `tools/build_snapshot.sh --check` — runs once, at the end. A repair
  re-runs ONLY the failed inner leg (e.g., `uv run pytest tests/<file>.py -q`), NOT
  `--check` again. The second `--check` is waste; the first one is already the gate.

**R6c — Anti-pattern:**
> Running `build_snapshot --check` three times in one verify phase (once per repair
> attempt) costs ~27 minutes. The fix: `--sync` during iteration, targeted test for
> the repair leg, `--check` only at the FINAL gate.

## N-distinct-lens-row requirement for gated / risk-tiered work (R1-T08, lens-gate v2)

`lens-gate.sh` enforces this at `SubagentStop` (per-dispatch, can block) and a
Stop-event backstop (`lens-tier-backstop.sh`) audits it session-wide
(advisory-only — Stop cannot block). A verify phase you author MUST NOT rely
on a single generic Lens call to satisfy a T2 (gated/risky) requirement — the
Lens dispatch has to write a validation row keyed at the SPECIFIC required
tier, not just any tier.

**The invariant, unchanged (structural floor — NEVER weakened):** every
code-touching `NEXUS:DONE` needs >=1 `validation_log` row with
`agent_validated='lens'` and `verdict='PASS'` (Constitution Art. XIII.d).
This still holds for T0 (no row required) and T1 (light row, any tier) work
exactly as before.

**The strengthening, additive on top (T2 / gated / risk-tiered dispatches
only):** the PASS row must ALSO carry `lens_type` matching the tier the
dispatch actually required (`risk_tier`) — a stale or lower-tier row (e.g. a
leftover T0/T1 PASS inside the validation window) no longer silently
satisfies a T2 requirement. When your verify phase's classifier determines a
leg is T2, the Lens dispatch for that leg MUST call:

```
python3 .memory/log.py validation add --agent lens --target <implementer> \
  --task-hash <hash> --verdict PASS --lens-type T2 --risk-tier T2 \
  --summary "..."
```

If your Workflow fans a single task out across MULTIPLE risk tiers (e.g. one
leg needs only a light T1 check, another leg touches a gated prefix and
needs the full T2 audit), each leg's Lens dispatch writes its OWN row at its
own tier — the gate then requires a DISTINCT row per required tier, not one
row reused across tiers. Do not have one Lens call claim coverage for
multiple tiers it didn't actually audit at that depth.

**Do not silently swallow the Stop-event backstop's WARN.** If
`lens-tier-backstop.sh` surfaces a gap at session end (a T2 row was claimed
but the matching `lens_type='T2'` row never landed), treat it as a signal to
investigate before trusting that session's DONE markers — it means a
SubagentStop dispatch's gate check was bypassed or never fired.

See `docs/ORCHESTRATOR-GATES.md` §6 for the full gate contract and exact
deny messages (`LENS/NO-VALIDATION` for the v1 floor, `LENS/TIER-MISMATCH`
for the v2 distinct-tier gap).

## Per-leg Lens invocation for worktree legs (RDEC-018 Option 3)

Worktree isolation is now the DEFAULT for ≥2 parallel code-writing Workflow legs
(DEC-008). When multiple legs run in parallel worktrees, invoke a SEPARATE Lens agent
PER WORKTREE, with `files_changed` scoped to that worktree's outputs only — never one
generic Lens call over the union of all legs' diffs. This prevents one leg's failing
Lens from blocking another leg's already-passing verdict (the independent-verification
principle already governing the fan-out-and-synthesize technique). The final
merge-back-and-remove phase runs ONLY after ALL parallel Lens verdicts are PASS; a
single REVISE holds back only its own worktree's merge, not the others.

## Cross-references

- **DEC-030 source text:** `docs/CONSTITUTION.md` (Article XIII, 2026-06-25 amendment)
- **Lesson:** LSN-008 (1hr+ thrash from monolithic verify inside single agent)
- **Skill nexus-orchestration** §1 — Verify-phase structure and mandatory Lens stage
- **Skill nexus-dispatch-catalog** — ANTI-PATTERN note on monolithic verification barrier
- **Skill verification-protocols** — Lens output schema + evidence rules
- **Skill deployable-engineering** — Fast-Verify Recipe + VERIFICATION-TIER mapping (R2map)
