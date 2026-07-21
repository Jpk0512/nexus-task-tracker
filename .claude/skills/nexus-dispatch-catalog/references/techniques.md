# The 6 Dispatch Techniques

Full when / phase-shape / budget / stop / sketch detail for each of the 6 dispatch
techniques. `SKILL.md` keeps only the task-shape→primitive decision table; read this file
when you need a technique's actual shape, budget, and stop condition.

Phase-shape notation: `scout → impl xN → lens-fast || lens` = a scout phase, then N
parallel impl teammates, then fast+deep Lens verify branches. Every code-writing `agent()`
MUST be followed by a **separate Lens verify** keyed to that teammate's `files_changed` —
workflow-internal teammates BYPASS the live SubagentStop gates, so the script re-instates
the bar. The producer NEVER self-certifies (the **separate-judge** principle).

1. **Classify-and-act** — classifier decides the KIND, routes to different behavior.
   Trigger: branch-on-TYPE, not on scale. Shape: `classify → route → (impl per class) →
   lens`. Budget: 1 cheap/fast classifier + 1 routed teammate per class ACTUALLY hit (don't
   pre-spawn unhit classes). Stop: known class emitted + routed teammate DONE + Lens passes;
   unknown class → escalate, do not guess.
2. **Fan-out-and-synthesize** — independent slices in parallel → a synthesize barrier merges
   structured outputs. Trigger: ≥2 independent subtasks (no shared file scope, no
   read-after-write dep). Shape: `scout → impl xN → synthesize barrier → lens-fast || lens`.
   Budget: one teammate per independent slice; diverse personas over identical clones. Stop:
   all owned tracked items verified DONE at the barrier + a **no-deferral completeness
   check** — nothing surfaced-and-unresolved.
3. **Adversarial-verify (the Lens mandate)** — a SEPARATE teammate attacks each producer's
   output vs a rubric. **NEVER self-review.** ALWAYS, after any code-writing teammate. Shape:
   `impl → lens (different viewpoint) → [REVISE loop ≤3]`. Budget: 1 producer + 1 separate
   critic; fast lens (lint/type/test) ∥ deep lens (semantic). Stop: Lens GREEN on
   `files_changed`; on RED → route the failure to the right persona, re-verify; **cap 3
   REVISE** then escalate. (Escalation is by MODEL OVERRIDE — see `SKILL.md`.)
4. **Generate-and-filter** — generate many candidates → dedupe + keep best after a rubric
   filter. Trigger: breadth THEN quality. Shape: `generate xN → dedupe → filter (rubric) →
   impl winner → lens`. Budget: as many generators as independent angles warrant; one
   deterministic filter node; cheap models generate, stronger model filters. Stop: filter
   yields ≥1 candidate above threshold; zero survivors → loosen scope or escalate, never
   ship sub-threshold.
5. **Tournament** — N teammates attempt the SAME task DIFFERENTLY; judges compare pairwise to
   one winner. Trigger: ONE hard problem worth N attempts (thorny algorithm, design choice,
   bug with several plausible root causes). Shape: `solve xN (different approaches) → judge
   pairwise (bracket) → impl winner → lens`. Budget: N solvers each a DIFFERENT approach (not
   clones); judges compare pairwise; halt early on statistical convergence. Stop: one winner
   after the bracket (or adaptive-stability); then implement + Lens the winner ONLY.
6. **Loop-until-done** — unknown-size work with a crisp oracle ("fix until no failing
   tests", "scan until no new findings", "migrate until zero callsites left"). Shape: `loop[
   scan → fix xN → re-verify ] until oracle | cap`, with a **MANDATORY max-iter cap**. Full
   recipes: `Skill loop-until-done-patterns`.

## Per-technique sketch

```js
// 1. Classify-and-act
const kind = await agent("classify: {bug|feature|refactor} — return one token",
  { agentType: "scout", model: "haiku", label: "classify", phase: "classify" });
const route = { bug: "fixer-persona", feature: "builder-persona", refactor: "refactor-persona" };
const out = await agent(fullBrief(kind), { agentType: route[kind.class], label: "route:" + kind.class, phase: "route" });
await agent(verify(out.files_changed), { agentType: "lens", label: "verify", phase: "verify" }); // mandatory

// 2. Fan-out-and-synthesize
const slices = ["ui", "api", "schema"]; // independent → parallel
const results = (await parallel(slices.map(s =>
  () => agent(briefFor(s), { agentType: personaFor(s), label: "slice:" + s, phase: "impl" })))).filter(Boolean);
const changed = results.flatMap(r => r.files_changed);
await agent(verify(changed), { agentType: "lens", label: "verify", phase: "verify" });

// 3. Adversarial-verify
let pass = false;
for (let i = 0; i < 3 && !pass; i++) {
  const out = await agent(brief, { agentType: "builder-persona", label: "fix-" + i, phase: "fix" });
  const v = await agent(verify(out.files_changed), { agentType: "lens", label: "verify-" + i, phase: "verify" });
  pass = v.verdict === "GREEN";
  if (!pass) brief = reviseFrom(v.findings); // change the approach, never "same-knob-harder"
}
if (!pass) return { escalate: "3 REVISE cap hit" };

// 4. Generate-and-filter
const cands = await Promise.all(angles.map(i => agent(generate(i), { agentType: "scout", model: "haiku", label: "gen-" + i, phase: "generate" })));
const kept = dedupe(cands).filter(c => score(c) >= BAR);
if (!kept.length) return { escalate: "no candidate cleared the bar" };
const winner = await agent(impl(kept[0]), { agentType: "builder-persona", label: "impl", phase: "impl" });
await agent(verify(winner.files_changed), { agentType: "lens", label: "verify", phase: "verify" });

// 5. Tournament
let bracket = await Promise.all(approaches.map(a => agent(solve(a), { agentType: "builder-persona", label: "solve:" + a, phase: "solve" })));
while (bracket.length > 1) {
  bracket = await reducePairwise(bracket, (x, y) =>
    agent(judge(x, y), { agentType: "lens", label: "judge", phase: "judge" }));
}
await agent(verify(bracket[0].files_changed), { agentType: "lens", label: "verify", phase: "verify" });

// 6. Loop-until-done — full recipe: Skill loop-until-done-patterns
```
