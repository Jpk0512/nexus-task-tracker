# Delegation Protocol and Sub-Agent Review — Full Detail

`SKILL.md` §5-6 keep only the brief-field list and the marker-routing table. This is the
full delegation and review protocol.

## Delegation Protocol

Every sub-agent brief must include (per CONTRACT.md schema):

- `agent_persona` — exact canonical/split slug from `docs/agents/TEAM.md`. Retired base
  names (unsplit persona families) are never dispatched directly. Escalation is a
  dispatch-time `model`/`effort` override on the SAME persona — a separate escalated-tier
  agent file does not exist; do not invent one.
- `goal` — one sentence
- `context_files` — minimum set of files to read (≤5; no "read everything")
- `acceptance_criteria` — GWT format, copied from spec
- `verification_required` — which checks must pass (project's lint/type/test commands)
- `do_not_touch` — files agent must not modify
- `db_log_cmds` — commands to run on completion (if any)
- `constraints` — must NOT do X, must use Y not Z

**Never** brief an agent with "figure out what needs doing." The scope is fully defined
before delegation.

**Fresh spawn per task — never reuse a subagent.** Every distinct task = a new `Task` tool
invocation with full brief. NEVER use `SendMessage` to a prior subagent instance to route
a new task — that reuses the old context window and breaks isolation guarantees. Two
distinct tasks = two `Task` calls = two fresh contexts. `SendMessage` is reserved
exclusively for explicit user follow-up to a still-running agent on the same task; it is
never an orchestrator routing primitive.

For multi-stage work, write a 10-20 line **handoff** to `.memory/` between stages — what
was decided, what was rejected, what remains. The next persona's brief (still a fresh
`Task` call) includes the handoff as a `context_file`.

### Per-task effort bumping (`ultrathink` keyword)

Default reasoning level is set by each persona's `effort:` frontmatter. For genuinely hard
one-off spawns, bump the effort by including the literal word `ultrathink` somewhere in
the Task prompt body. Claude Code recognizes it and raises that single spawn's thinking
budget to the model's max.

**Bump (include `ultrathink`) when:**
- Task is Complex class AND a Scout reflection flagged non-trivial risks
- An architectural decision is embedded (schema choice, library swap, API contract design)
- Re-spawn after a prior failed iteration on the same task — encode the failure pattern +
  bump
- Cross-cutting refactor where one wrong call cascades across many files

**Do NOT bump for:**
- Standard CRUD, single-file edits, doc updates, isolated bug fixes with a clear repro
- Test authoring (the test-author persona has a tight, well-specified contract)
- Verification (deterministic-first checks are bounded; semantic checks shouldn't need a
  bump unless the output is genuinely ambiguous)
- Routine orchestrator routing turns (classification, status checks, log commands)

Mechanically, just drop the word in the brief. Example: `"goal: 'ultrathink — propose the
indexing strategy for this query pattern. Three candidates with tradeoffs.'"`.

**Full-session override:** set `CLAUDE_CODE_EFFORT_LEVEL=xhigh` in the environment when
starting Claude Code. This wins over frontmatter and `ultrathink` keyword — use it
sparingly (debugging a stuck session, validating a difficult feature end-to-end).

The user is the ultimate authority on bumps. If they say "use ultrathink for this",
include it regardless of the heuristic above.

---

## Sub-Agent Review Protocol

When an agent returns work, route on the **completion marker** (H2 heading at top of
agent output):

| Marker | Action |
|---|---|
| `## NEXUS:DONE` | Verify `verification_result` is verbatim passing → run `db_log_cmds` → mark task done. |
| `## NEXUS:BLOCKED` | Read `blockers`. If a different persona can unblock, re-route. Otherwise escalate to user. |
| `## NEXUS:NEEDS-DECISION` | Use `AskUserQuestion` with the options the agent surfaced in `decisions_needed`. On user response, log via `decision add` and re-spawn with the chosen path. |
| `## NEXUS:CHECKPOINT` | Write checkpoint summary to `.memory/` (via context snapshot) → pause and resume next session. |
| `## NEXUS:REVISE` (from Lens) | **Revision loop**: re-spawn implementer with the failing issues YAML as `context_files`. Cap at 3 iterations. Stall detection: if `current_issue_count >= previous_issue_count`, escalate ("revision loop stalled at iteration N — issue count not decreasing"). |
| `## NEXUS:DEFER-REQUEST` | Agent found an out-of-scope error and requests deferral. Default action is **FIX**, not FILE. Options: (a) approve deferral — log the tracked task, then continue; (b) instruct an inline fix; (c) escalate to user. Never leave a surfaced error with no resolution path. |

Always:
1. Check `verification_result`: verbatim passing output, not just "I ran it"
2. Check `acceptance_met`: every entry must be `true` with evidence
3. Run all `db_log_cmds` (task updates, decision logs)
4. **Do not mark task done** until verification passes AND acceptance is met

Two failures on the same task by the same agent → escalate to user before retrying.

**Re-delegation = fresh `Task` call.** When re-routing after `## NEXUS:REVISE`, `##
NEXUS:BLOCKED`, or `## NEXUS:NEEDS-DECISION`, always spawn a NEW `Task` invocation with an
updated brief — never `SendMessage` to the prior subagent. Each re-spawn pays the cost of
a fresh context window deliberately; that cost is the point.

### Revision loop (detail)

```
iteration = 0
prev_count = ∞
while iteration < 3:
  spawn implementer with brief.context_files += [lens_revision_report.md]
  output = implementer_response
  if output.completion_marker == "## NEXUS:DONE":
      → spawn Lens to re-validate
      if Lens returns DONE: break (success)
      if Lens returns REVISE again:
          current_count = len(lens.issues)
          if current_count >= prev_count:
              escalate("revision loop stalled at iteration {iteration}")
              break
          prev_count = current_count
          iteration += 1
          continue
  else:
      → handle marker per table above
      break
if iteration == 3:
    escalate("revision loop hit cap at 3 iterations")
```

A worked revision-loop walkthrough: `examples/revision-loop-walkthrough.md`.

### Reflection step (between planning gate and delegation)

For Standard and Complex tasks ONLY (Simple bypass skips reflection):

1. After planning gate passes, BEFORE delegating to the implementer, spawn `scout` with
   this brief:
   ```
   goal: "Read the brief + spec + relevant code. Write a 5-bullet reflection: (1) hidden
   assumptions in the spec, (2) likely failure modes for this approach, (3) files that
   should be read before coding starts, (4) what the test stubs (if any) miss, (5) one
   alternative approach worth considering. ≤200 words. No code changes."
   context_files: [<spec_path>, <relevant_files_from_classification>]
   acceptance_criteria: ["5-bullet reflection produced", "≤200 words", "no edits made"]
   verification_required: ["read-only — no commands"]
   ```
2. Log the returned reflection as a `context_log` row with `--action-type research`.
3. If the reflection identifies a blocker, escalate to the user BEFORE proceeding with
   implementation.
4. Otherwise, include the reflection file path (e.g. `.memory/reflections/<task_id>.md`)
   as a `context_files` entry in the implementer's brief.

Cost: one Scout call (cheap, high-volume model). Pays back by catching a meaningful share
of premature "done" patterns.

### Scout report file-dump (output isolation)

To keep the orchestrator's context window clean, Scout dumps full findings to a file and
returns only a summary. Pattern:

- **Brief instruction:** include `session_id` (from `python3 .memory/log.py session
  current --id-only`) and a kebab-case `task_slug` (≤40 chars) in every Scout brief.
- **Scout writes:** `.memory/scout-reports/<session-id>/<task-slug>.md` containing the
  complete findings JSON + narrative.
- **Scout returns:** `report_path`, ≤200-word `summary`, `top_3_files` (path + one-line
  each), `recommended_persona_next`, completion marker.
- **The orchestrator reads:** the summary first. Only `Read` the dump file (with
  `offset`/`limit` if large) when the summary is insufficient to make a routing call.
- **Path is gitignored** (`.memory/scout-reports/` in `.gitignore`). Reports are
  session-scoped, not durable artifacts.

Apply the same pattern to **Lens** when its `revision_report` exceeds ~500 words — dump
full report to `.memory/lens-reports/<session-id>/<task-slug>.md`, return summary + issue
count + top-3 critical findings.
