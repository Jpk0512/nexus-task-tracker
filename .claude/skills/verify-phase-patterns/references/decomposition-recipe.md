# Decomposition Recipe (before/after)

The KICK/FAN/JOIN mechanic that implements the core rule, with a monolithic-vs-decomposed
contrast. Read this before authoring or repairing a verify phase.

## Before (anti-pattern — monolithic)

```js
// ONE agent, full serial gauntlet — DO NOT DO THIS
phase("verify");
await agent(
  "run lint, the full test suite, and the release-gate consistency check",
  { label: "verify", phase: "verify", agentType: "lens" });
// If this times out, the harness restarts from scratch — indefinite thrash.
```

## After (correct — decomposed)

**3-move mechanic (KICK → FAN → JOIN). The KICK move is the HARD RULE — this is not one
option among several, it is how the hard rule is executed:**

```js
// Move 1 — KICK (HARD RULE): background the heavy gate at ORCHESTRATOR level
// (chat-thread Bash, NEVER inside a phase/agent).
// Bash(run_in_background=true) — appends an unambiguous rc sentinel as the LAST line.
// ( <the project's heaviest release-gate command>; echo "GATE_RC=$?" ) > .memory/verify-gate.out 2>&1
// Returns a shell id immediately. Do NOT block. The echo IS the pipe-safe rc capture.

// Move 2 — FAN: launch fast gates in parallel while the heavy gate runs
phase("verify");
const [lint, typecheck, tests] = await parallel([
  () => agent("run the project's lint command — report findings only",
    { label: "verify-lint", phase: "verify", agentType: "lens-fast", stall_budget_seconds: 90 }),
  () => agent("run the project's type-check command — report findings only",
    { label: "verify-typecheck", phase: "verify", agentType: "lens-fast", stall_budget_seconds: 120 }),
  () => agent("run the targeted test file(s) for the changed scope — report findings only",
    { label: "verify-tests", phase: "verify", agentType: "lens-fast", stall_budget_seconds: 180 }),
  // lens-fast for deterministic gates; lens for semantic review when needed
]);

// Move 3 — JOIN: after fast legs return, harvest the backgrounded gate.
// grep -E '^GATE_RC=' .memory/verify-gate.out   -> GATE_RC=0 (pass)
// If GATE_RC line is absent, the gate is still running — do NOT mark done on a partial file.
// Synthesize: phase is GREEN iff lint.rc==0 && typecheck.rc==0 && tests.rc==0 && GATE_RC==0.
```

### Stall budget enforcement

Every verify agent gets a `stall_budget_seconds` in its brief. When the budget
expires:

1. Call `TaskStop` on the agent's `taskId` / `runId`.
2. Log the stall via the lessons system with the agent's last-known output.
3. Escalate to the user with context: which gate timed out, what its last output
   was, and the recommended manual command.

Do NOT restart the same agent with the same configuration ("same-knob-harder"). If
you restart, change the approach (smaller scope, different command flags, or
escalate to user).

## Repair loop — failed-leg-only re-run

When one verify leg fails:

```js
const failed_leg = "tests";        // the one that failed

// CORRECT: re-run only the failed leg
// Substitute the implementer persona that owns the failing code
// (e.g. the backend implementer for a server-side test, the frontend
// implementer for a UI test, the wiring persona for a hook/auth fix).
phase("repair");
const fix = await agent("fix the test failures listed in <finding_path>",
  { label: "repair-tests", phase: "repair", agentType: "owning-persona",
    context_files: [finding_path] });

phase("verify");
await agent("re-run ONLY the previously-failing test(s)",
  { label: "verify-tests-recheck", phase: "verify", agentType: "lens-fast",
    context_files: fix.files_changed, stall_budget_seconds: 180 });

// WRONG: re-run the full gauntlet
// parallel([() => lintAgent(), () => typecheckAgent(), () => testAgent()]) — wastes budget on passing legs
```

Re-run ONLY the leg that failed, and ONLY against the specific files the fixer
touched. If lint was green before the fix, do not re-run lint unless the fixer
touched files under the linted scope.

## Verify agent size guide

| Gate | Persona | Typical stall budget | Notes |
|---|---|---|---|
| Lint command | `lens-fast` | 90s | Fast; scope to touched files where the tool supports it |
| Targeted test file(s) | `lens-fast` | 180s | Targeted; never the full suite in-agent |
| Type-check command | `lens-fast` | 120s | Type-check only |
| Docker Compose config check | `lens-fast` | 30s | Syntax only, no containers |
| Semantic / RCA / visual | `lens` | 300s | One concern per agent |
| Full release-gate / full test suite | **orchestrator Bash** | N/A — backgrounded | NEVER inside a workflow agent |

## No-redundancy rule

If the project's release gate already runs the full test suite internally, and you have
already dispatched a targeted test agent for the same scope, do NOT also run the full
release gate for that scope — that is a redundant gate. Run the full release gate at the
orchestrator level only when you need to validate the whole project (not a targeted
change), matching the `verification_tier: targeted` vs `release` distinction in
`docs/agents/CONTRACT.md`.

## Full verify-phase skeleton

```js
// In a standard [scout → impl → verify] workflow:

// Heavy gate: backgrounded at orchestrator level, BEFORE entering the verify phase
// (The orchestrator runs: Bash("<full release-gate command>", background=true))

phase("verify");

// Fast gates: bounded parallel agents
const [lintResult, testResult, typecheckResult] = await parallel([
  () => agent("run the project's lint command over the changed scope — return exit code + stdout",
    { label: "verify-lint", phase: "verify", agentType: "lens-fast", stall_budget_seconds: 90 }),
  () => agent("run the targeted tests for the changed scope — return exit code + stdout",
    { label: "verify-tests", phase: "verify", agentType: "lens-fast", stall_budget_seconds: 180 }),
  () => agent("run the project's type-check command — return exit code",
    { label: "verify-typecheck", phase: "verify", agentType: "lens-fast", stall_budget_seconds: 45 }),
]);

// Semantic review (separate agent, separate concern)
const semanticResult = await agent(
  "semantic review of files_changed against acceptance_criteria — security + ops + new-hire passes",
  { label: "verify-semantic", phase: "verify", agentType: "lens",
    context_files: impl_files_changed, stall_budget_seconds: 300 });

// Synthesize: if any agent reports non-zero exit, route to repair loop
// re-run ONLY the failing leg after the fixer addresses it.
```
