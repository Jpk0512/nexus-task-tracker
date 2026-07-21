# Nexus on pi — Operating Manual

This is the **pi-native** recreation of the Nexus orchestrator system. The **governance** layer (the agent contract, completion markers, skills, memory DB) is *shared* with the Claude Code and Cursor installations — only the **dispatch mechanism** is pi-specific.

If you want the *why*, read `.pi/APPEND_SYSTEM.md` (the orchestrator overlay pi loads) and `docs/agents/CONTRACT.md` (the contract every delegation follows). This document is the reference.

## What lives where

| Layer | Location | Notes |
|---|---|---|
| Orchestrator identity (shared) | `AGENTS.md` | loaded by pi as context |
| Orchestrator pi-overlay | `.pi/APPEND_SYSTEM.md` | reconciles `Task`/`Workflow` references → the `subagent` tool |
| Dispatch tool | `.pi/extensions/subagent/` | official pi `subagent` example + 3 Nexus tweaks |
| Personas (13) | `.pi/agents/*.md` | pi-native frontmatter (`name`/`description`/`model`/`tools`) + identity body |
| Workflow prompts | `.pi/prompts/*.md` | `/implement`, `/scout-and-plan`, `/implement-and-review`, `/verify` |
| Contract (shared) | `docs/agents/CONTRACT.md` | required input/output schema + 19 rules |
| Skills (shared) | `.agents/skills/` | JIT-loaded via `Skill <name>` |
| Memory (shared) | `.memory/log.py` + `project.db` | notepad, decisions, tasks, sessions, facts |
| Routing map (shared) | `docs/agents/TEAM.md` | persona ownership + forbidden-dir matrix |

## Activation (one-time)

1. Start pi in this project. On the **project trust** prompt, approve it (this loads `.pi/` resources and executes the extension). Run **`/trust`** to persist the decision so you are not re-prompted.
2. Run **`/reload`** after any edit to `.pi/`.
3. Confirm it loaded — the startup header lists extensions, skills, and context files.

That's it. `AGENTS.md` + `.pi/APPEND_SYSTEM.md` set the Nexus identity; the `subagent` tool is your dispatch primitive; `.pi/agents/` holds the personas; skills are one `Skill <name>` away.

## The dispatch tool — `subagent`

Provided by `.pi/extensions/subagent/`. Spawns a separate `pi` process per persona with an **isolated context** (`--mode json -p --no-session -nc`), streaming tool calls and returning the final output. Each subprocess gets the persona's body as `--append-system-prompt`, its `tools` list via `--tools`, and its `model` via `--model`.

| Mode | Shape | When |
|---|---|---|
| **single** | `{agent, task, agentScope:"both"}` | one persona |
| **parallel** | `{tasks:[{agent, task}, …], agentScope:"both"}` | ≥2 independent personas (lens ∥ lens-fast; fan-out). Cap 8 tasks / 4 concurrent. |
| **chain** | `{chain:[{agent, task:"…{previous}…"}, …]}` | sequential; step N's output feeds N+1 via `{previous}` |

**Always pass `agentScope: "both"`** so `.pi/agents/` personas are discovered. Personas run with `-nc` (no `AGENTS.md`) for a clean identity — they do **not** inherit your orchestrator overlay, and they have no `subagent` tool in their toolset, so they **cannot recurse**.

## The loop — ORIENT → CLASSIFY → BRIEF → DELEGATE → VERIFY → CHECKPOINT

0. **Notepad first:** `python3 .memory/log.py notepad list --topic <topic>`.
1. **Classify out loud:** Trivial (≤2 files, no logic change) → do it inline (the one exception to "don't write code"). Simple/Standard/Complex → delegate.
2. **Planning gate** (Standard/Complex) — `Skill nexus-protocol`.
3. **Reflect** (Standard/Complex) — dispatch `scout` for a 5-bullet reflection first.
4. **Delegate** via `subagent` with a full `docs/agents/CONTRACT.md` brief. Walk `Skill parallel-first-check` first; build the brief with `Skill contract-schema`.
5. **Review** the returned completion marker — the JSON envelope's `status` is the routing signal.
6. **Verify** code-touching work: dispatch `lens-fast` + `lens` in ONE parallel `subagent` call. Early-fail on `lens-fast` → re-dispatch the implementer.
7. **Checkpoint** — run the returned `db_log_cmds`, then ONE focused commit on the session branch.

## Persona roster (pi)

| Persona | model | tools | owns | cannot touch |
|---|---|---|---|---|
| `scout` | haiku | read,bash,grep,find,ls | read-only recon | any write (no write/edit tool) |
| `planner` | opus | read,write,edit,bash,grep,find,ls | `docs/plans/**`, `.memory/plans/**` | source, hooks, `.claude/**` |
| `forge-ui` | sonnet | read,write,edit,bash,grep,find,ls | `app/apps/dashboard/src/**` | `app/apps/api/src/**`, ingestion, models, compose |
| `forge-wire` | sonnet | read,write,edit,bash,grep,find,ls | `app/apps/api/src/**` | `app/apps/dashboard/src/**`, ingestion, models, compose |
| `pipeline-data` | sonnet | read,write,edit,bash,grep,find,ls | ingestion transforms/writers, embeddings | dashboard, async workers |
| `pipeline-async` | sonnet | read,write,edit,bash,grep,find,ls | ingestion workers/clients, async | dashboard, sync write pipelines |
| `atlas` | opus | read,write,edit,grep,find,ls | `models/**`, schema/DDL | bash (design only), dashboard, business logic |
| `hermes` | sonnet | read,write,edit,bash,grep,find,ls | auth, integration, MCP, docker wiring, hooks/docs infra | business logic in dashboard/ingestion |
| `palette` | sonnet | read,write,edit,bash,grep,find,ls | `design/**`, `docs/design/**` | implementation code, ingestion, models |
| `lens` | sonnet | read,bash,grep,find,ls | semantic verify (sole verdict-row writer) | any write (no write/edit tool) |
| `lens-fast` | haiku | read,bash,grep,find,ls | deterministic gates pass/fail | any write; never a verdict row |
| `quill-ts` | sonnet | read,write,edit,bash,grep,find,ls | `app/apps/dashboard/**` tests | non-test source |
| `quill-py` | sonnet | read,write,edit,bash,grep,find,ls | `tests/**` | non-test source |

**Mandatory pairings:** `forge-ui` ⇄ `palette` (any visual work); `forge-ui` + `forge-wire` (full-stack); `pipeline-data` + `pipeline-async` (ingestion); `lens-fast` ∥ `lens` (post-implementation, one parallel call).

**Escalation** (`-pro`): no separate agent files — re-dispatch the SAME persona with `model: opus` and high thinking. Escalate at most once per task; then escalate to the user.

## The contract — brief in, envelope out

Every delegation is a `docs/agents/CONTRACT.md` brief. **Required input fields:** `agent_persona`, `goal`, `context_files`, `acceptance_criteria`, `verification_required`, `do_not_touch`, `notepad_topic`, `skills_required` (code-writing personas). Recommended: `constraints`, `db_log_cmds`, `db_context`, `parallel_group_id`, `verification_tier`.

**Required output** — a fenced JSON envelope: `status` (`DONE`|`BLOCKED`|`NEEDS-DECISION`|`CHECKPOINT`|`REVISE`|`DEFER-REQUEST`), `completion_marker` (the matching `## NEXUS:<STATUS>`), `files_changed`, `verification_result` (verbatim), `acceptance_met[]`, `db_log_cmds`, `notes`. Fix tasks add `root_cause_analysis`; deliveries touching `app/`/`ingestion/`/`design/`/compose add `deploy_step`.

`status` is the routing signal — you route on it, not on the prose.

## Completion markers → your action

| status | your action |
|---|---|
| `DONE` | verify verbatim passing output + every `acceptance_met=true` → run `db_log_cmds` → commit |
| `BLOCKED` | re-route to another persona OR escalate to user |
| `NEEDS-DECISION` | **ask the user in your reply** (no AskUserQuestion tool) → `decision add` → re-dispatch fresh |
| `CHECKPOINT` | write checkpoint to `.memory/`, pause |
| `REVISE` | re-dispatch implementer with the actionable issue list (`file:line` + what + fix); cap 3 iterations |
| `DEFER-REQUEST` | approve (log task) / inline fix / escalate — default **FIX not FILE** |

## Memory ritual

`.memory/project.db` via `python3 .memory/log.py` is the only durable store.
- **You (orchestrator):** `context dump` at session start; `session start`; `session end --summary --next_step` at session end.
- **Every persona:** `notepad list --topic <topic>` first; `notepad add --topic <topic> --agent <persona> --note "…" --kind <kind>` last. The brief states `notepad_topic`. Notes are insights (≤500 chars), not status — "Completed" is forbidden.
- Never dump raw memory into a reply — summarize.

## Parallel / chain recipes

**Verify (most common parallel):**
```
subagent({ tasks: [
  { agent: "lens-fast", task: "<brief with context_files + verification_required>" },
  { agent: "lens",      task: "<same brief>" },
], agentScope: "both" })
```

**Scout → implement (chain):**
```
subagent({ chain: [
  { agent: "scout",      task: "Map code relevant to: <goal>" },
  { agent: "forge-wire", task: "Implement per this brief: … . Prior scout findings:\n{previous}" },
], agentScope: "both" })
```
(Prefer two separate `single` calls when you need to read scout's output before briefing — chain is for tight handoffs.)

## Differences from Claude Code Nexus (what's gone, and the replacement)

| Claude Code | pi replacement |
|---|---|
| `Task(subagent_type=…)` | the `subagent` tool (single mode) |
| `Workflow` / `TeamCreate` / fan-out | `subagent` parallel mode |
| linear `Task` chains | `subagent` chain mode |
| `.claude/hooks/*` mechanical gates | **self-enforce** (no hooks in pi) |
| SocratiCode / PRISM / conduit | `grep`/`find`/`read` + brief-provided tools |
| `AskUserQuestion` | ask the user in your reply |
| `Monitor` / `CronCreate` (outlive session) | not available — would need a pi extension |
| persona recursion guard (hook) | enforced by toolset — personas have no `subagent` tool |

## Discipline you must self-enforce (no hooks)

- **Leaf executors only** — personas cannot dispatch (no `subagent` in their toolset).
- **Verify before DONE** — verbatim command output; no run, no green → BLOCK.
- **Respect `do_not_touch`** — via the brief.
- **Lens before accepting any code-touching DONE.**
- **Read before edit; re-read after another tool changes the file.**
