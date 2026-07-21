# Ownership Boundaries and Isolation Discipline

Full detail behind the pre-dispatch ownership check and the worktree-vs-session-branch
decision. `SKILL.md` keeps only the routing table and a one-line pointer here.

## Decomposition boundary — pre-dispatch ownership check

Before briefing any teammate in a dynamic Workflow, intersect the teammate's assigned
file-globs against the forbidden-directory map below.

**Rule:** no teammate may be briefed on files that fall outside its write boundary. If a
brief would cross an ownership line, **split the brief along that line** — one teammate
per ownership domain — rather than leaving it to the teammate to self-restrict.

**How to apply:**
1. List every file or glob the teammate will touch.
2. Check each against the persona's "Cannot touch" row in the forbidden-directory table.
3. If ANY file crosses the boundary, split into two or more briefs — one per domain — and
   assign the correct persona to each.

**Ownership shortcuts for common splits:**
- Schema or migrations → split out to the schema/data-modeling persona
- Server-side API routes → split out to the server-side implementer persona
- Frontend UI / components → split out to the UI implementer persona
- Ingestion transforms/writers → split out to the data-pipeline persona
- Ingestion workers/clients → split out to the async-worker persona
- Auth wrappers / env-var plumbing / Docker / MCP → split out to the wiring persona
- Test files only → split out to the appropriate test-author persona

A brief that spans ownership lines is a dispatch contract violation — Lens will flag it
and the task will require a REVISE cycle.

## Forbidden directories (per persona)

The exact persona names and their write boundaries live in each persona's own agent file
(`.claude/agents/<persona>.md`) and `docs/agents/TEAM.md` — this table is the
routing-time cross-check, not the canonical source. General shape, consistent across
every persona roster this package can render:

| Persona role | Cannot touch |
|---|---|
| Read-only investigator (Scout) | Anything (write tools disallowed) |
| Frontend UI implementer | Backend/ingestion dirs, `docker-compose*.yml`, `.memory/`, backend API routes |
| Server-side/backend implementer | Frontend component dirs, `docker-compose*.yml`, `.memory/`, ingestion dirs |
| Data-pipeline implementer | Frontend app dirs, schema/model dirs, `docker-compose*.yml`, `.memory/` |
| Async-worker implementer | Frontend app dirs, schema/model dirs, `docker-compose*.yml`, `.memory/` |
| Wiring/integration persona | Business logic inside app or ingestion dirs (auth/integration glue only); schema/model dirs, `.memory/` |
| Schema/data-modeling persona | Anything via Bash (design only); app/ingestion business logic |
| Verification personas (Lens / fast-lane Lens) | Anything (reports only, write tools disallowed) |
| Visual-design persona | App source code, ingestion, `docker-compose*.yml`; writes ONLY to its own design-report surface |
| Test-author personas | Non-test files (only test files modifiable); `.memory/` |
| Orchestrator | Anything via Edit/Write (write tools disallowed); orchestrate via delegation only |

## Isolation discipline — worktree vs session branch

**Worktree isolation is the DEFAULT for parallel multi-part implementation** — run 2-3
independent phases at once in registered worktrees, not as an opt-in exception. When the
orchestrator briefs teammates for a Workflow:

- **≥2 independent code-writing legs in parallel** (each editing disjoint files): the
  orchestrator registers a worktree per leg BEFORE spawning, and each brief carries
  `isolation_mode: worktree` + `worktree_path: <absolute-path>` (the registration the
  orchestrator already made).
- **A SINGLE indivisible workflow**: stays directly on the session branch,
  `isolation_mode: main`, no worktree.
- **Sequential legs inside one Workflow** (a write-dependency chain) or **read-only legs**
  (Scout/Lens): stay on the session branch by default — isolation buys nothing when
  there's no concurrent write.
- **Self-modifying lanes** (`.claude/hooks/**`, `.claude/settings.json`,
  `.claude/agents/**`) get `worktree_required: true` regardless of leg count (the
  original hazard class this rule protects against).

Fail-closed, unchanged: an unregistered worktree path is hard-DENIED by
`worktree-guard.sh` on `git worktree add` — registration is the orchestrator's
responsibility, never implicit. Every registered worktree branches off the session branch
(never hardcoded `main`) and the merge-back+remove is a MANDATORY final phase — no orphan
may ever survive. Full ladder + registration mechanics: `Skill nexus-dispatch-catalog`.
