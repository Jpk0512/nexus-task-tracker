# Worked example — launch, watch, checkpoint, and resume a Workflow

**Scenario:** a fan-out-and-synthesize dispatch across 3 independent slices, expected to
run ~15 minutes — long enough to need the stall watchdog.

**1. LAUNCH:**
```js
phase("impl");
const outs = (await parallel([
  () => agent("slice A brief …", { label: "impl-A", phase: "impl", agentType: "builder-persona" }),
  () => agent("slice B brief …", { label: "impl-B", phase: "impl", agentType: "builder-persona" }),
  () => agent("slice C brief …", { label: "impl-C", phase: "impl", agentType: "builder-persona" }),
])).filter(Boolean);
```
This returns a `runId` + a background-task output file path immediately.

**2. STALL WATCHDOG (start right after launch):**
```bash
# run_in_background=true — poll every ~45s, exit on complete or stall
until grep -q '"result_count":3' .memory/workflow-<runId>.journal.jsonl 2>/dev/null; do
  # check all THREE quiet signals before declaring stall: transcript mtime,
  # gate-output mtime, no live gate process — see references/operating-the-primitive.md
  sleep 45
done
```

**3. CHECK ETA (mid-run):** `tail` the output file — shows "phase: impl, 2/3 legs done,
last log: 'slice B: applying fix to module X'". Phase position (`impl`, not yet `verify`)
is the cheapest ETA proxy.

**4. CHECKPOINT (natural boundary):** the `phase("verify")` transition after all 3 slices
land is the commit-per-phase checkpoint — one commit, revertable, and the resume unit if
the run is interrupted here.

**5. RESUME (if interrupted mid-`verify`):**
```js
// Same scriptPath, same runId — the impl phase (all 3 agent() calls) replays from CACHE
// (the leading agent() prefix is unchanged), only the verify phase actually re-executes.
Workflow({ scriptPath: "scripts/fan-out-x.js", resumeFromRunId: "<runId>" });
```

**6. KILL (if a leg wedges):** if the stall watchdog confirms all three quiet signals for
slice B past its `stall_budget_seconds`, call `TaskStop` on that leg's taskId, log the
stall via the lessons system, and either resume with a smaller scope for that leg or
escalate to the user with the last-known output.

**Non-obvious delta:** the watchdog and the ETA check both read the SAME journal file —
there is no separate "status" RPC. Everything after launch is disk-reads until the next
`agent()` call actually returns.
