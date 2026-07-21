# `.pi/` — Nexus orchestrator on pi

The pi-native recreation of the Nexus orchestrator system. The shared governance layer (contract, completion markers, skills, memory DB) is reused from the Claude Code / Cursor installations; only the dispatch mechanism is pi-specific.

See **`docs/NEXUS-PI.md`** for the full operating manual.

## What's here

```
.pi/
├── APPEND_SYSTEM.md          # orchestrator overlay pi loads (reconciles Task tool → subagent tool)
├── extensions/
│   └── subagent/             # the dispatch tool (official pi example + 3 Nexus tweaks)
│       ├── index.ts
│       └── agents.ts
├── agents/                   # 13 pi-native personas
│   ├── scout.md  planner.md
│   ├── forge-ui.md  forge-wire.md
│   ├── pipeline-data.md  pipeline-async.md
│   ├── atlas.md  hermes.md  palette.md
│   ├── lens.md  lens-fast.md
│   └── quill-ts.md  quill-py.md
└── prompts/                  # workflow templates
    ├── implement.md  scout-and-plan.md
    ├── implement-and-review.md  verify.md
```

## Activate (one-time)

1. Start pi in this project. On the **project trust** prompt, **approve** it (loads `.pi/` resources + runs the extension).
2. Run **`/trust`** to persist the decision (no more prompts).
3. Run **`/reload`** after editing anything in `.pi/`.

The startup header confirms what loaded: the `subagent` extension, the skills, and the context files (`AGENTS.md`, `.pi/APPEND_SYSTEM.md`).

You are now Nexus in pi. Try: `/implement <goal>` or just describe a task and the orchestrator will classify → delegate → verify.

## The dispatch tool

The `subagent` tool (model-invoked) spawns isolated `pi` processes per persona. Three modes: **single**, **parallel**, **chain**. Always pass `agentScope: "both"`. Personas run with a clean identity (`-nc`, no `AGENTS.md`) and **cannot recurse** (no `subagent` in their toolset).

## Editing personas

`.pi/agents/<persona>.md` frontmatter:

| field | meaning |
|---|---|
| `name` | dispatch slug |
| `description` | one-line role |
| `model` | pi model pattern: `haiku` / `sonnet` / `opus` (or full `provider/id`) |
| `tools` | comma-separated pi tools: `read, write, edit, bash, grep, find, ls` |

The body is the persona's system prompt. Keep it tight — heavy detail belongs in skills (`.agents/skills/`), loaded JIT via `Skill <name>`.

## Extension diff from upstream

`.pi/extensions/subagent/` is the official pi `subagent` example with three changes (documented so upgrades are easy to re-apply):

1. **Default `agentScope` → `"both"`** (was `"user"`) so `.pi/agents/` personas are discovered without passing the flag every call.
2. **Subprocesses run with `-nc`** (no `AGENTS.md`) so personas get a clean identity — they don't inherit the orchestrator overlay.
3. **Description/schema copy** updated to match.

Everything else is byte-identical to upstream, so you can diff against a future pi release cleanly.
