---
name: parallel-first-check
description: "Parallel-first pre-dispatch checklist — before any single Agent/Task dispatch, walk the Article XIII.d three-rung threshold ladder: one indivisible task -> ONE Agent; >=2 independent parallel subtasks -> a dynamic Workflow (TeamCreate + Agent-tool teammates) instead of sequential single dispatches; multi-phase / fan-out-then-verify / beyond-one-context -> a dynamic Workflow with the plan moved into code. Enforces Constitution Article XIII / XIII.b / XIII.d. Advisory nudge on homogeneous fan-out: prefer diverse personas over identical clones (not a hard numeric cap). Use this skill at every parallel dispatch decision point."
---

# Parallel-First Check

Mechanical checklist run before EVERY single Agent/Task dispatch, to catch the
"two consecutive single dispatches that should have been one Workflow"
anti-pattern (Article XIII / XIII.d). For the primitive cheat-sheet, the 6
techniques, and the goal model, load **`Skill nexus-dispatch-catalog`** first —
this skill is only the pre-dispatch gate check, not the catalog.

## The three-rung threshold ladder (Article XIII.d)

- **(a) Single INDIVISIBLE task** → ONE `Agent`, or (preferred, DEC-017) a
  single-teammate Workflow for the Lens-review/monitorability it adds.
- **(b) `>=2` INDEPENDENT subtasks** → a dynamic Workflow is REQUIRED (DEC-029),
  not merely preferred. No shared file scope, no read-after-write dependency.
- **(c) MULTI-PHASE / fan-out-then-verify / beyond one context** → a dynamic
  Workflow with the plan moved into code (script holds the loop/branching;
  the conversation sees only the final answer).

## Ownership-intersection check (pre-brief, unique to this gate)

Before dispatch, for each teammate list its file-globs and check each against
the persona forbidden-directory map (`Skill team-routing`). A glob crossing an
ownership boundary must be split along that line before dispatch —
schema/migrations → atlas; server-side API/actions/AI-wiring → forge-wire;
frontend UI → forge-ui; ingestion transforms/writers → pipeline-data;
ingestion workers/clients → pipeline-async; auth/env/Docker/MCP → hermes; test
files → quill-ts or quill-py. A cross-boundary brief is a contract violation
Lens will flag as REVISE.

## Install-aware roster check (unique to this gate)

Confirm the persona is registered at `.claude/agents/<persona>.md` before
dispatch. Python-stack personas (`pipeline-data`, `pipeline-async`, their
`-pro` siblings, `quill-py`) are absent in TS-only installs — dispatching an
absent persona hard-fails mid-workflow. Remap per `Skill team-routing`;
surface `## NEXUS:NEEDS-DECISION` if genuine Python logic is required but no
Python persona is installed.

## R4 inline-overlap rule (unique to this gate)

Once a Workflow's agents are dispatched, the orchestrator MUST NOT sit idle.
Any inline work independent of the in-flight agents' outputs — reads, greps,
planning, drafting the next brief, fast Bash checks — runs CONCURRENTLY while
agents execute. Blocking on one in-flight agent when independent inline work
exists is a serialization violation equivalent to a wrong-serial single
dispatch. Genuinely independent work is parallelized unconditionally; token
economy only restrains work that truly depends on another agent's output.

## Cross-references (do NOT rediscover, DEC-021)

- **`Skill nexus-dispatch-catalog`** — the shape→primitive cheat-sheet, the 6
  techniques in depth, fan-out width, the goal model. Load this FIRST.
- **`Skill nexus-orchestration`** — HOW to run the chosen primitive (launch,
  checkpoint, resume, stop, Monitor, Cron).
- **`Skill nexus-loss-function`**, **`Skill team-routing`** — as named above.

Full detail: Constitution **Article XIII.d**, `Skill plexus-protocol` §8.
