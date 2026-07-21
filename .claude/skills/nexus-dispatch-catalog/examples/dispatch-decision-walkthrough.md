# Worked example — shape a real dispatch decision

**Input:** a brief lands: "these N files each need the same structural fix applied,
verified independently." A scout report names the files and their per-file gaps.

**Action (climb the ladder):**
1. Task shape check: ≥2 independent subtasks? Yes — each file is editable independently
   (disjoint file scope, no read-after-write dependency between them). Ladder rung (b)
   fires: a dynamic Workflow is REQUIRED for a from-scratch fan-out, not merely preferred.
2. BUT: if this specific dispatch already arrived as a single same-persona brief (the
   orchestrator chose to keep same-persona, same-write-scope edits as one leaf dispatch
   rather than N parallel Agent spawns), the primitive decision was made one level up, not
   re-litigated here. Cheat-sheet row match: this is still "PARALLEL / independent slices"
   in shape, but because every file sits under one persona's write scope with no cross-file
   conflict, a single sequential leaf editing N disjoint files is cheaper than N
   Workflow-spawned Agents for mechanical edits — the token-tax note ("a Workflow on trivial
   work is a token tax") applies per-file here.
3. Technique used within the dispatch: none of the 6 (no adversarial-verify sub-loop, no
   tournament) — straight sequential edit-per-file, verified once at the end via a single
   verification command, not per file.

**Output:** N files edited in one leaf-agent pass; a single verification command run once
at the end (not per file) — matching the "heavy verification runs ONCE" discipline,
composed with this skill's primitive-selection ladder.

---

## Worked example — a goal-shaped request

**Input:** "make the test suite green."

**Action (goal model):**
1. **ELICIT** — the goal is present but the oracle is implicit. No clarifying question
   needed here (the test suite exit code is an unambiguous oracle).
2. **CLARIFY** — the Goal Object:
   ```yaml
   goal:
     success_criteria: ["the project test suite exits 0"]
     acceptance_checks: ["<the project's test command> exits 0"]
     non_goals: ["do not refactor passing tests", "do not change test framework"]
     open_questions: []
   ```
3. **CONFIRM** — surface the Goal Object to the user once: "I'll iterate fix→verify until
   the suite is green, capped at 20 iterations. Confirm?"
4. **DRIVE** — a loop-until-done Workflow (Pattern 1, `Skill loop-until-done-patterns`):
   scan failing tests → fix the failing shard → Lens re-verify → repeat until oracle
   satisfied or the cap/no-progress guard fires.

**Output:** either the suite is green (Lens PASS row covering every changed file) or the
loop escalates with a named reason (max-iter hit / no-progress / circuit-breaker) — never a
silent giving-up.
