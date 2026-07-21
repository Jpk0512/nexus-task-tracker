# The 19 Universal Rules (full text)

`SKILL.md` lists a condensed 9-rule summary. This is the full text of every rule agents
must follow, from `docs/agents/CONTRACT.md`.

1. **Read before edit.** Always read a file before editing it. Re-read after any other
   tool changes the file.
2. **Searching.** grep/rg/find are free (discovery is unblocked); SocratiCode for
   concepts/maps, lsp-py for type-exact refs — advisory. If SocratiCode is unindexed,
   INDEX it — never grep as a substitute. Use `codebase_symbol(name=…)` /
   `codebase_symbols(query=…)`.
3. **Verify before done — substantive evidence required.** Run every
   `verification_required` command and capture the verbatim output in
   `verification_result`. Claims without output → rejected.

   **Two legal exits when a verify command does not return a clean pass:**

   | Situation | Legal action |
   |---|---|
   | Command ran, reported a real failure (type error, lint error, test fail) | **FIX** the code, re-run until genuinely green |
   | Command absent / not installed / `command not found` / rc=127 | **emit `## NEXUS:BLOCKED`** — verification could not be performed |

   **Green is something a tool prints, never something you assert. No run, no green —
   BLOCK.** Fabricating a third path is a hard violation. Evidence ladder: see
   `references/completion-marker-state-machine.md` §Fallback Output Ladder.

   **Banned phrases** in `verification_result` without a real rc=0 capture: `todo`,
   `tbd`, `n/a`, `none`, `pending`, `<any angle-bracket token>`, `...`, `-`, `deferred`,
   `structure verified`, `Ready for <tool>`, `verified complete`, any checkmark not
   backed by captured command output.
4. **No silent failures.** If a tool call fails, report it in `blockers`, not in `notes`.
5. **Commit on the session branch — commit-only, never push.** All work lands on the
   session branch (the branch active at session start, detected at runtime via `git
   branch --show-current` — may be `main` or any other branch; never hardcode it). One
   focused commit per task IS the checkpoint. Do NOT create a new feature branch and do
   NOT use `git worktree` unless the orchestrator explicitly registered one for this leg.
   A sub-agent COMMITS on the session branch but does NOT push it — only the orchestrator
   or the user pushes.
6. **Return `db_log_cmds`.** The orchestrator runs these to update the memory DB. Agent
   does not run them — orchestrator does.
7. **No invented features.** If the spec is ambiguous, return `## NEXUS:NEEDS-DECISION`
   with `decisions_needed` populated. Do not design around an ambiguity.
8. **Leaf executor — no recursion.** Sub-agents may NOT spawn their own sub-agents (no
   Task tool usage). All delegation flows through the orchestrator. Personas that need
   help must return `## NEXUS:NEEDS-DECISION` requesting a pairing.
9. **Respect `do_not_touch`.** Files in that list must not be modified, even if the agent
   thinks they should be. If a needed change is in a forbidden file, return `##
   NEXUS:NEEDS-DECISION` requesting permission.
10. **Deploy-step disclosure — LOCAL actions pre-authorized, REMOTE requires handoff.**
    Every implementation response that touches application/backend/design or
    `docker-compose*.yml` MUST end with a `## Deploy step` block naming the restart
    command (none / HMR / restart / rebuild).

    **PRE-AUTHORIZED LOCAL actions** (agent MAY run these directly as part of
    verification, no human handoff needed): `docker compose up --build <svc>` /
    `docker compose restart <svc>` / `docker compose down && docker compose up -d <svc>`
    / local test re-runs.

    **BLOCKED — REMOTE/PRODUCTION actions that require human handoff:** `git push` to
    any remote; publishing to a package registry; production database migrations; any
    action touching a live/hosted environment.

    When in doubt: if it touches a remote host, registry, or production DB → `##
    NEXUS:NEEDS-DECISION` with the action described.
11. **Root cause in every fix response.** When the task is "fix a bug" or "investigate an
    error", the response MUST include a `## Root Cause Analysis` block:
    ```
    ## Root Cause Analysis
    Symptom: <one line describing what the user observed>
    Why 1: <immediate cause>
    Why N (root): <architectural / contract / design defect that allowed this bug class>
    Pattern fix: <how the codebase/process is changing so this class can't recur>
    ```
    The chain is as long as the cause actually needs — one hop for a genuinely shallow
    cause, many for a deep one; no minimum count, no padding. A response that resolves
    the symptom but cannot articulate the root cause is INCOMPLETE.
12. **No deferral of discovered issues.** Errors, anomalies, or contract violations
    discovered while doing assigned work MUST be fixed in the same delivery. Filing as a
    follow-up task is FORBIDDEN unless the user explicitly authorized the defer (via
    AskUserQuestion or the `## NEXUS:DEFER-REQUEST` flow). The default is FIX, not FILE.
13. **Visual and end-to-end verification.** Verification must cross the real boundary —
    tests that mock the boundary they are validating do NOT satisfy this rule: UI changes
    need before+after screenshots; API/route changes need a real-boundary invocation
    result (curl or equivalent) in the response; container/Dockerfile changes need a
    docker build + container start + smoke test result.
14. **Deploy-step block with action + verification.** Every implementation response
    touching application/backend/design or `docker-compose*.yml` MUST end with:
    ```
    ## Deploy step
    Restart action: <none | HMR | restart <svc> | build+up <svc>>
    Verification: <command that confirms the new code is running>
    ```
    The block targets the current session-branch HEAD — there is no branch/checkout
    line. A response without this block is INCOMPLETE.
15. **Architectural-pattern review when crossing service boundaries.** If the fix changes
    a cross-service mechanism (process exec, IPC, queue, RPC, etc.), the response MUST
    cite which alternative patterns were considered and why the chosen one fits the
    deployment topology.
16. **Lens validates before any coding-agent NEXUS:DONE is accepted.** Code-writing
    persona responses claiming `## NEXUS:DONE` on source-code work are CONDITIONAL until
    Lens has validated. The orchestrator MUST dispatch Lens before logging task-done or
    merging the change. Lens can downgrade to `## NEXUS:REVISE`; the orchestrator
    re-dispatches with the failure context. Skipping Lens is a CONTRACT VIOLATION.
17. **Notepad read-write loop.** Every dispatched agent MUST: (1) as their FIRST action,
    run the notepad `list --topic <topic>` command; (2) as their LAST action before
    returning any completion marker, run the notepad `add` command with a concise
    insight (≤500 chars); (3) the notepad is for INSIGHTS, not tasks — "I completed step
    3" is FORBIDDEN; (4) the `notepad_written` output field MUST be populated — either
    with the insight written or `{skipped: "no useful context to add"}`.
18. **Skill bindings — JIT load-order rule.** Skills bind through three mechanisms: (1)
    frontmatter `skills:` — the foundational layer that arrives with the agent by
    composition; (2) description-driven discovery; (3) brief-carried `skills_required`
    (Rule 19). Load order: `skills_required` first, in the order given; then
    description-matched skills; never mid-task after the first non-Read tool call.
19. **Brief-driven skill loading.** When `skills_required` is non-empty in the brief, you
    MUST call `Skill <name>` for each entry BEFORE your first non-Read tool call. Order
    matters: skills are applied in the order listed. Auto-discovery is not relied upon;
    explicit invocation is the contract, mechanically enforced by
    `skills-required-guard.sh`.
