---
name: nexus-loss-function
description: Design a HEAVY goal — a loss function + harness — for a long-running, autonomous, eval-driven Nexus optimization loop. Use when a goal is long-running/autonomous/eval-driven rather than a simple in-session iterate-until-done (the LIGHT Goal Object is the default for that). The orchestrator observes the environment, clarifies the target into a VERIFIABLE form, ingests or generates the spec, builds a blinded dev/holdout eval, generates and verifies the harness against Nexus verification gates, red-teams its own draft for reward-gaming, and emits goal.md ready to drive. Re-invoke in PATCH MODE when a running loop cheated and the loss function — not the agent — needs fixing. Do NOT use for a simple in-session task with a single deterministic gate (tests pass / rtk tsc / build_snapshot --check rc=0) — that's the default LIGHT Goal Object, no harness needed.
metadata: {tier: sonnet, token_budget: 5000, injectable: true}
---

# Nexus Loss Function (HEAVY goal model)

> **Adapted from [elvisun/loss-function-development](https://github.com/elvisun/loss-function-development)
> (`skills/lfd-design`), MIT License.** This is an ADAPTATION, not the original —
> LFD's design has been mapped onto Nexus primitives (verification gates, Lens,
> the lessons table, the feedback system). See `ATTRIBUTION.md` in this skill
> directory for the full upstream MIT notice. Original authorship is the
> upstream project's; the Nexus mapping is the adaptation.

## When this skill fires (HEAVY vs LIGHT)

Nexus has a **tiered goal model** (DEC-025). The DEFAULT is the **LIGHT Goal
Object** — `{success_criteria, acceptance_checks, non_goals, open_questions}` —
drafted from a vague intent, confirmed once, and used as the in-session
termination oracle for an iterate-until-done loop. Reach for THIS skill (the
**HEAVY** tier) only when the goal is:

- **long-running** — spans many cycles / outlives one context window, leaves
  cold-start state on disk between ticks;
- **autonomous** — the orchestrator drives it through fully-autonomous ticks
  with a separate critic (Lens) reviewing, not a human in every loop;
- **eval-driven** — success is a *metric on data the optimizer cannot see*, not
  just "the tests pass / the gate is green."

If the oracle is a single deterministic gate that goes green in-session
(tests pass, `rtk tsc`/`uv run pytest`/`build_snapshot --check` rc=0, no new
Lens findings), you do NOT need a loss function — use the LIGHT Goal Object and
a loop-until-done Workflow. A loss function is the second, harder thing: "build
this, make the gates green, **then** descend toward this bar on data you cannot
see."

You are designing an **optimization target**, not solving the task. The agent
(or Workflow) that receives `goal.md` is a competent, tireless, literal
optimizer: it will satisfy the target by the cheapest available path —
memorizing the eval, hardcoding answers, mining feedback channels into lookup
tables. Your job is to make **genuine capability the cheapest path left.**

Every `goal.md` you emit has FOUR parts, mapped to Nexus:

| LFD part | What it is | Nexus mapping |
|---|---|---|
| **Target** | the metric, blinded, mechanically scored at the right resolution | the held-out eval; acceptance measured on holdout only |
| **Constraints** | wall-clock / money / surface / methodology / capacity caps | the dispatch budget + surface allowlist + capacity caps |
| **Instruments** | ONE command per constraint — "a constraint without an instrument is a vibe" | the **verification gates** (`rtk tsc`/`rtk lint`, `uv run ruff check`/`uv run pytest`, `tools/build_snapshot.sh --check`) + `harness/score.sh`/`lint.sh`/`probe.sh`/`status.sh` |
| **Forced entropy** | per-cycle overfit reflection, stall rule, exploration quota, a log that survives compaction | the **REVISE stall-escalation** + the **lessons table** + the **feedback system** as the iteration log |

## The Nexus mappings (read these once — they are load-bearing)

The upstream skill assumes a bare-repo harness. In Nexus the same roles already
exist as first-class primitives — REUSE them, don't reinvent:

- **Instruments = the verification gates.** A constraint is only real if a
  single command proves it. Nexus already has those commands: `rtk tsc` /
  `rtk lint` for TS; `uv run ruff check` / `uv run pytest` (run from
  `nexus-broker/`) for Python; `tools/build_snapshot.sh --check` (rc=0) for the
  deployable. Wire each constraint to the gate that proves it; only invent a new
  `harness/*.sh` instrument for the *eval-specific* checks the standard gates
  don't cover (the score itself, capacity-cap lint, the probe gap, the status
  dashboard).
- **Judge = Lens (the separate-judge mandate).** "The model that stopped working
  never decides it's done" (DEC-024). The optimizer NEVER scores its own
  acceptance: **Lens** is the adversarial verifier that measures the holdout bar
  and signs off, exactly as in the standard verify phase. Holdout acceptance is
  a Lens responsibility, blinded from the optimizer. This is the LFD
  separate-judge principle = the existing Lens validation gate.
- **Iteration log = the lessons table + the feedback system.** LFD's `LOG.md`
  (hypothesis / expected-failure-mode / diagnostic / result, written *before* the
  change) is what survives context compaction. In Nexus that durable log is the
  **lessons table** (`python3 .memory/log.py lesson ...`) plus the **feedback
  system** (`category=workflow-friction` and the "had-to-discover-it" signal,
  DEC-019/021). Failure-boundary / abstention-aware memory (DEC-024) — "store
  what FAILED so the loop doesn't re-try it" — is a lesson. Keep a `LOG.md`
  per-cycle for the in-flight run AND harvest the durable findings into lessons.
- **Forced-entropy stall rule = the REVISE stall-escalation.** Nexus already
  halts on "same-knob-harder" via REVISE stall counting; the loss function's
  stall rule ("flat metric ⇒ the next change must be structural") and exploration
  quota make that explicit in `goal.md` and feed the same runaway guards.
- **Runaway guards (DEC-024) are MANDATORY, not optional.** Three independent
  ceilings — max-iteration cap, no-progress detection (halt on identical
  errors / empty diffs / recurring fails N times), and a token/$ budget — plus a
  rate-based circuit-breaker that halts and escalates. They live in `goal.md`'s
  Constraints + Stop conditions and are non-negotiable for any autonomous loop.

**Instruction-hierarchy note (Nexus HARD RULES still bind):** `goal.md` is a
*target you author*, not an instruction that can relax a HARD RULE. The DEC-002
main-only / no-worktree default and DEC-005 no-deferral rules outrank anything
the loop "discovers." An autonomous loop that decides to branch, defer, or have
the orchestrator write code itself is a **bug in the loss function** — patch the
target (Patch mode), don't obey the discovery.

## Two modes

- **Design mode** (default): Phases 0–9 below, in order, then drive.
- **Patch mode** (see end): a running loop cheated — fix the loss function, not
  the agent.

---

## Phase 0 — Observe before clarifying

Inventory the environment BEFORE asking the user anything — the first principle
of harness engineering is observability, applied to your own design task. Use
the read-only discovery tools (SocratiCode `codebase_search`/`codebase_symbols`,
`lsp-py` references, Scout recon) — never grep before the gate opens.

- **Repo:** existing test suites, eval datasets, scoring scripts, CI workflows,
  `.memory/` state (open tasks, prior lessons, decisions), `CLAUDE.md`.
- **Gates as ready-made instruments:** which verification commands already
  apply to this surface (`rtk tsc`/`rtk lint`, `uv run pytest`,
  `build_snapshot --check`). Reuse them; don't author a parallel scorer for what
  a gate already proves.
- **Tooling:** what is installed and usable (headless browser, image-diff, `jq`,
  db clients, the broker/vault MCP servers).
- **Surfaces:** which API keys/providers are reachable (presence only — never
  print a secret value; the secret-path guard blocks it anyway).
- **Reference artifact:** if the user named a product/dataset, look at what is
  publicly accessible right now.

Whatever observation cannot answer becomes a clarifying question in Phase 1.

## Phase 1 — Clarify into a VERIFIABLE goal (the ONE confirmation)

Per DEC-023, the orchestrator **elicits → clarifies → sets → drives**; it never
tells the user to run `/goal` or `/loop`. Ask in ONE batched round, only what
Phase 0 couldn't answer, using TYPED-AMBIGUITY framing (DEC-024: missing-goal /
missing-premises / ambiguous-terminology):

1. **Outcome** — what artifact or behavior, and what does "good" look like? Is
   there a reference artifact to score against?
2. **Eval source and size** — where do ground-truth cases come from, and how many
   are obtainable? (Phase 3 can build the eval if the answer is "nowhere yet.")
3. **Budgets** — wall-clock budget, dollar/credit ceiling, which paid surfaces
   exist. An 80% solution in 2 hours beats a 100% one in 30 days — get the
   user's real tolerance.
4. **Surface** — directories, APIs, providers, models, concurrency the loop may
   touch. Everything unlisted is denied. (HARD RULES still bind on top: no
   branches/worktrees by default, no deferral.)
5. **Acceptance** — the holdout score bar, plus a diminishing-returns stop ("if
   marginal gain ≈ 0 for N cycles, stop and report").

Get **ONE confirmation** of the clarified, verifiable goal BEFORE driving. A
separate critic (Lens) reviews during the autonomous ticks — the human is not in
every loop, so the up-front confirmation is the human gate.

## Phase 2 — Spec: the inner loop (gates green BEFORE descent)

The spec is the starting point, not the finish line.

- If a spec exists, read it. If not, generate one — reverse-engineer the
  reference artifact (public surfaces only) into a system design plus concrete
  test cases, written to `spec.md`. For Nexus FEATURE work this is the
  `docs/features/FEAT-XXX.md` spec + GWT.
- The spec's test suite is the **inner loop**: short horizon, fast feedback, one
  objective — make the gates green. The eval is the **outer loop**: long horizon,
  sparse feedback.
- `goal.md` MUST gate the outer loop behind the inner one: **Stage 0** = build to
  spec, all verification gates green (`uv run pytest` / `rtk tsc` /
  `build_snapshot --check` rc=0), BEFORE any descent on the eval. Never let the
  loop optimize a half-built system against slow, sparse feedback.

## Phase 3 — Build the blinded eval (dev / holdout split)

If the user can't hand over enough cases, build them — real expected outputs for
many problems already sit in public artifacts:

- Collect real expected outputs from the reference artifact at scale. Public
  artifacts only; respect robots.txt, rate limits, ToS.
- Dedup. Check diversity — no single entity, date range, or template may
  dominate, or the eval teaches a shortcut.
- Reject any case that overlaps seed or fixture data.
- Collection must be **independent of the future optimizer**: do it now, in this
  design session, and land the answers OUTSIDE the optimizer's surface.
- **Split (the anti-reward-gaming core, DEC-025):**
  - `eval/dev/` — scored freely; misses reported but **capped**; answers live
    only inside the scorer.
  - `eval/holdout/` — scored rarely, aggregate-only; **acceptance is measured
    here exclusively** (by Lens); answers outside the repo if at all possible.
- **Visibility rule, stated explicitly in `goal.md`:** eval INPUTS may be visible
  (probe generation needs them); eval ANSWERS are never readable — dev answers
  live only inside the scorer, holdout answers outside the repo.
- If the total is under ~200 cases, **warn explicitly**: a small eval is
  enumerable and the optimizer WILL memorize it. Widen before proceeding.

## Phase 4 — Design the loss function

**Target.**
- Mechanically computable by a script, at the right resolution for the claim. An
  LLM judge that "compares two screenshots" approves 12px spacing errors; a
  pixel-diff does not. Match the instrument to the precision the user wants.
- Penalize BOTH failure directions. Recall without precision invites
  return-everything; precision without recall invites return-one-thing. A
  one-sided metric from the user gets fixed — and tell them why.
- **Leak audit** on every feedback channel: bits revealed per scoring call ×
  expected cycles — can the optimizer reconstruct the eval before the run ends?
  If yes, cut feedback resolution (cap the miss list, return aggregates) or grow
  the set.

**Constraints (with the mandatory runaway guards).**
- Wall-clock budget, stated in `goal.md`. Agents have no sense of time and will
  grind 10 hours for 2%.
- Dollar / credit ceilings per paid surface.
- Surface allowlist from Phases 0–1; HARD RULES on top (main-only, no deferral).
- Methodology rules (LLM-in-the-data-plane allowed? deterministic only?).
- **Capacity caps** on every artifact that could become a lookup table — keyword
  lists, regex sets, seed data, special-case branches. Name the artifact and the
  cap ("keyword list ≤ 20 entries").
- **Runaway guards (DEC-024):** max-iteration cap; no-progress detection (halt on
  identical errors / empty diffs / recurring fails N times); token/$ budget;
  rate-based circuit-breaker that halts and escalates to the user.

**Enumerate the cheats.** Read `references/cheat-museum.md`, then list ≥10 ways a
lazy optimizer could max THIS metric without solving THIS task. For each, write
the fence: a constraint in `goal.md` AND a way to detect violation. A constraint
without an instrument is a vibe — the optimizer violates it cheerfully because it
can't tell it's violating it.

**Enforcement design rule.** Any constraint that references eval content (e.g.
"no literal in the codebase may match an eval item") can only be checked by the
harness — the agent can't check it without reading the eval. Put the check in
`harness/lint.sh`, run it inside `score.sh`, and on violation **VOID the score and
report nothing else**. Naming the offending literal turns your lint into a
membership oracle the optimizer can mine string-by-string (cheat-museum #12).
Your enforcement instrument is itself a feedback channel — leak-audit it like any
other.

## Phase 5 — Generate the harness (reuse the gates)

Write these now, tailored to the task. No placeholders. Reuse anything Phase 0
found — especially the standard verification gates.

- `harness/score.sh` — the task-specific scorer (pixel-diff for a UI clone with
  deterministic rendering: frozen time, animations off, pinned fonts, fixed
  viewport; recall@k + precision for retrieval; structured JSON diff for API
  behavior). Runs `lint.sh` first — any violation voids the score (`VOID:
  constraint violation` and nothing more). Scores `eval/dev` by default;
  `--holdout` returns ONE aggregate number, rate-limited, appended to an audit
  log that only Lens/the human reads.
- `harness/lint.sh` — checks capacity caps and eval-literal overlap. Called only
  by `score.sh`; detailed findings go to a file OUTSIDE the optimizer's read
  surface, for Lens/the human.
- `harness/probe.sh` — perturbed variants of dev INPUTS (paraphrases, date
  shifts, entity swaps); reports the dev-vs-probe score gap. The gap is the
  memorization gauge.
- `harness/status.sh` — per-step timestamps + total wall-clock elapsed; spend so
  far AND projected burn before the next paid batch, per surface; score history
  per cycle; the optimizer's own token consumption where session logs allow.
  Gain-per-token is the gradient of the optimization — the loop must be
  self-aware.
- **Standard gates as instruments:** wire `uv run pytest` / `rtk tsc` /
  `tools/build_snapshot.sh --check` as the Stage-0 inner-loop instruments
  directly — don't reimplement what they already prove.
- `eval/dev/` and `eval/holdout/` — from Phase 3.
- `LOG.md` — instantiate `references/log-template.md`: one entry per cycle with
  `hypothesis / expected failure mode / diagnostic / result`, written BEFORE the
  change. Harvest durable findings into the **lessons table** + **feedback
  system** so they survive past this run.

## Phase 6 — Verify the harness yourself (don't delegate to the user)

1. Run `score.sh` on dev — it must produce a number.
2. Calibrate: score one known-good and one known-bad output. The scorer must
   separate them decisively. A broken scorer optimizes noise. (This is a Lens
   calibration check — the judge must demonstrably distinguish good from bad.)
3. Run `probe.sh` and `status.sh` once each.
4. Blinding check: from the optimizer's working directory, try to read the
   holdout answers. If you can, the agent can.
5. Trip the lint deliberately — plant an eval literal, confirm the score voids
   WITHOUT naming it, then remove the plant.

## Phase 7 — Red-team your own draft

Before emitting, simulate the laziest possible agent against your draft `goal.md`:
what is the five-minute win? Common ones: seed data mirroring the eval, mining
per-item miss feedback into a keyword lookup table, gaming a judge, editing the
scorer or `goal.md` itself, declaring victory on the dev set. Patch the draft and
simulate again. Emit only when **three consecutive simulations find nothing
cheaper than doing the real work.** This red-team-your-own-draft pass is the
design-time analogue of the Lens adversarial-verification mandate.

## Phase 8 — Emit goal.md

Fill `references/goal-template.md`. Every placeholder gets a task-specific value;
no section dropped. Invariants the emitted `goal.md` MUST keep regardless of task:
the Stage-0 gates-green gate, VOID semantics, **holdout-only acceptance measured
by Lens**, the read-only set including `goal.md` itself, the per-cycle checkpoint
commit (one commit per cycle on the session branch — DEC-002, NO new branch /
worktree), the entropy rules (stall rule + exploration quota), and the stop
conditions (bar hit on holdout · any budget/runaway-guard tripped · marginal gain
≈ 0 for N cycles). Record the goal + oracle as durable `.memory/` state so the
loop is cold-start-resumable (DEC-022 durable-loop principle).

## Phase 9 — Drive it (orchestrator-invocable primitives only)

Everything else was verified in Phase 6. The orchestrator DRIVES the loop using
only agent-invocable primitives — never a user slash-command (DEC-023):

- **iterate-until-goal** → a loop-until-done **Workflow** (the Workflow tool),
  each cycle running the harness, with Lens as the in-loop critic and the runaway
  guards as the kill condition.
- **poll external state** (CI, deploy, crawler queue) → **Monitor**.
- **cross-session / recurring** → **CronCreate** (session, ≤7-day) or
  **RemoteTrigger** (durable).

Two things only the human can do, surfaced before launch:
1. Use a disposable API key with a provider-side spend limit.
2. **Babysit cycle 1** — watch what the loop touches and confirm it uses the
   instruments. Then let it run.

## Patch mode — when the loop cheats anyway

A cheat mid-run is a bug in the TARGET, not the agent. When re-invoked against a
running/paused loop (the user reports a cheat, or `LOG.md` / a probe gap / a
lesson shows one):

1. Read `LOG.md`, the score history, the lessons table, and the diff since the
   last honest checkpoint commit.
2. Identify the open path: which feedback channel leaked, which artifact had
   spare capacity, which constraint lacked an instrument.
3. Patch the **loss function**, NOT the agent's code: widen the eval, cap the
   artifact, cut feedback resolution, add the missing lint/gate.
4. Append the exhibit to `references/cheat-museum.md` (what it looked like → the
   fence that closed it) AND record it as a lesson + feedback signal so the next
   loop never reopens that path.
5. Re-verify the harness (Phase 6), revert eval-shaped artifacts the cheat
   produced, and resume the loop from the last honest checkpoint commit.

## Worked example — designing a HEAVY goal end-to-end

**Input.** The user asks: "`vault_query` recall@5 on ambiguous queries is weak
— get it above 85% and let it run unattended overnight if it needs to." This
trips all three HEAVY triggers at once: long-running (spans past one session),
autonomous (no human per-cycle), eval-driven (scored against held-out
query→note pairs, not a single in-session gate) — so this skill fires, not the
LIGHT Goal Object.

**Action (Phases 0–9, condensed to the non-obvious calls):**
1. **Phase 0 observe:** find the existing `nexus-vault` MCP tool, 40 real
   query→expected-note pairs already logged in `.memory/vault-query-log/`, and
   the ready-made instrument `uv run pytest nexus-broker/tests/test_vault_query.py`.
2. **Phase 1 clarify (one batched round):** "Eval: build from the 40 logged
   pairs plus synthetic paraphrases? Budget: ≤$15 Anthropic spend, ≤8h
   wall-clock, disposable key? Surface: `nexus-vault` MCP + `harness/` +
   `.memory/vault-eval/**` only? Acceptance: recall@5 ≥0.85 on holdout, stop if
   flat for 3 cycles?" — user confirms all four in one reply.
3. **Phase 3 build the eval:** 240 pairs collected from the log, deduped,
   diversity-checked (no single note dominates); split `eval/dev/` (190,
   capped miss list) / `eval/holdout/` (50, Lens-only, rate-limited to one
   `--holdout` run per 4 cycles).
4. **Phase 4 design + cheat enumeration:** Target = recall@5 **and** precision@5
   (a recall-only metric invites "return everything"); capacity cap
   `keyword-boost list ≤ 15 entries` on the one artifact that could turn into a
   lookup table. Cheat museum flags "seed dev-set note titles into the boost
   list" — fenced by `harness/lint.sh` checking boost-list entries against
   `eval/dev` titles, VOID on overlap, no offending title printed (or the lint
   becomes a membership oracle — cheat-museum #12).
5. **Phase 6 self-verify:** `harness/score.sh --dev` returns a real number
   (baseline **recall@5=0.61**); calibration run scores a known-good config
   (0.61) against a known-bad one (0.22) — the scorer separates them; a
   deliberate holdout-answer read from the optimizer's working directory fails
   (outside the surface) — good; a planted eval-literal in the boost list
   VOIDs the score without naming the literal.
6. **Phase 9 drive:** an iterate-until-goal **Workflow**, `pipeline-data` as the
   implementer each cycle, `lens` as the in-loop critic scoring `--holdout`
   every 4th cycle, the three runaway guards (max 40 cycles / no-progress halt
   on 3 identical scores / the $15+8h caps) as the kill condition.

**Output — the emitted `goal.md` (Phase 8), abbreviated:**
```markdown
## Target (outer loop)
recall@5 AND precision@5 on ambiguous queries · Bar: recall@5 ≥0.85 on holdout.
Score with `harness/score.sh --dev` (freely) / `--holdout` (Lens only, ≤1 per 4
cycles). VOID means a constraint was violated — find and remove it; the harness
will not say which.

## Constraints
- Wall-clock ≤8h · spend ≤$15 (disposable key) · surface: nexus-vault MCP +
  harness/ + .memory/vault-eval/** only.
- Capacity cap: keyword-boost list ≤15 entries.
- Runaway guards: max 40 cycles · no-progress halt (3 identical scores) ·
  $15/8h budget · rate-based circuit-breaker.
```
and one real cycle's terminal output, harvested by `pipeline-data` +
`lens`:
```
$ harness/score.sh --dev
recall@5=0.68 precision@5=0.71 (cycle 6, prior 0.64 — gain, continue)
$ harness/probe.sh
dev=0.68 probe=0.66 (gap 0.02 — generalizing, not memorizing)
```
Cycle 6 checkpoints as one commit (`cycle 6: recall@5=0.68`) on the session
branch — no new branch, per DEC-002 — and the loop continues until Lens signs
off a `--holdout` run ≥0.85 or a runaway guard trips.

---

## References

- `references/cheat-museum.md` — catalog of real reward-gaming cheats + their
  fences. Read before designing any target.
- `references/goal-template.md` — the `goal.md` skeleton; fill every placeholder.
- `references/log-template.md` — the per-cycle iteration-log skeleton (harvest
  into the lessons table + feedback system).
- `ATTRIBUTION.md` — upstream MIT notice (elvisun/loss-function-development).
