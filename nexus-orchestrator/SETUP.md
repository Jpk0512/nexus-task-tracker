# Nexus Orchestrator — Setup Guide

## What you get

- **Nexus** orchestrator agent — Claude Code's default agent for this project; classifies work, plans, delegates, reviews
- **Scout** — read-only investigator dispatched before any Standard/Complex task
- **Lens** — mandatory QA verifier dispatched after every implementer NEXUS:DONE
- **Quill / Quill-PY** — test engineer personas (failing stubs before implementation, PASS after)
- **DOMAIN-AGENT-TEMPLATE.md** — starter template for your own domain implementer personas
- **Memory system** — SQLite-backed (.memory/project.db): sessions, tasks, decisions, lessons, facts, procedures
- **17 enforcement hooks** — SocratiCode-first gate, Lens gate, stall counter, analysis-paralysis guard, no-direct-push-to-main, and more
- **Skill library** — nexus-protocol, team-routing, contract-schema, verification-protocols, tdd-patterns, and others

---

## Quick Start (5 minutes)

1. Copy this template directory into your project root (or clone it alongside your project)
2. Copy `nexus-config.example.json` to `nexus-config.json`
3. Edit `nexus-config.json` with your stack + personas (see Step 1 below)
4. Run `./install.sh` to wire everything into your project
5. Open Claude Code in your project — it auto-loads as Nexus orchestrator

---

## Step-by-step Setup

### Step 1: Configure your project

Edit `nexus-config.json`:

**`project`** block:
- `name` — your project identifier (used in memory DB, log entries)
- `description` — one-sentence description
- `dev_port` — local dev server port (default 3000)

**`tech_stack`** block:
- `languages` — list of languages (e.g., `["TypeScript", "Python"]`)
- `frontend` / `backend` / `database` — human-readable stack description
- `testing.frontend` / `testing.backend` — test runner(s) per layer
- `verification` — commands for type-check, lint, test per language. These replace `[TYPE_CHECK]`, `[LINT]`, `[PYTHON_LINT]` in agent files:
  - `type_check` — e.g., `rtk tsc` (TypeScript), `mypy src/` (Python), `go vet ./...` (Go)
  - `lint` — e.g., `rtk lint` (ESLint), `uv run ruff check` (Python), `golangci-lint run` (Go)
  - `test_frontend` — e.g., `rtk vitest run`, `jest --passWithNoTests`
  - `test_backend` — e.g., `uv run pytest`, `go test ./...`

**`personas`** block — one entry per domain specialist agent:
- `name` — agent file name (e.g., `"forge"` → `.claude/agents/forge.md`)
- `role` / `description` — what this agent does
- `owns` — list of directories this persona is responsible for
- `do_not_touch` — directories this persona must never write to
- `stack` — list of technologies this persona uses
- `verification` — commands to run before NEXUS:DONE
- `model` — `"sonnet"` for most, `"haiku"` for cheap investigation
- `effort` — `"high"` for implementation, `"xhigh"` for complex escalations

**`paths`** block:
- `docs` — where you keep markdown documentation (default `docs/`)
- `agents_doc` — where TEAM.md and CONTRACT.md live (default `docs/agents/`)
- `source_roots` — directories that trigger the Lens gate after implementer changes
- `tests` — root test directory

**`hooks`** block:
- `gated_source_paths` — comma-separated prefixes for SocratiCode gate (e.g., `"app/,ingestion/,src/"`)
- `context_reset_at` — message count before context-reset warning fires (default 10)

---

### Step 2: Create your domain persona agent files

Use `DOMAIN-AGENT-TEMPLATE.md` as your starting point for each domain persona in `nexus-config.json`.

For each persona entry:

1. Copy `.claude/agents/DOMAIN-AGENT-TEMPLATE.md` to `.claude/agents/<name>.md`
2. Replace all `[PLACEHOLDER]` tokens with your project values:
   - `[PERSONA_NAME]` → persona `name` from config (e.g., `forge`)
   - `[ROLE_SHORT_TITLE]` → short role label (e.g., `TypeScript Implementer`)
   - `[ROLE_DESCRIPTION]` → persona `description` from config
   - `[DOMAIN_DIRECTORY]` → first entry in persona `owns` array
   - `[OTHER_DOMAIN]` / `[OTHER_PERSONA]` → do_not_touch directories + owning persona names
   - `[TECHNOLOGY_N]` / `[VERSION]` → entries from persona `stack`
   - `[TYPE_CHECK_CMD]` / `[LINT_CMD]` → entries from persona `verification`
   - `[SKILL_NAME]` → skills relevant to this persona's domain

**Example substitution for a TypeScript/Next.js persona:**

| Placeholder | Value |
|---|---|
| `[PERSONA_NAME]` | `forge` |
| `[ROLE_SHORT_TITLE]` | `TypeScript Implementer` |
| `[DOMAIN_DIRECTORY]` | `app/` |
| `[OTHER_DOMAIN]` | `ingestion/` |
| `[TECHNOLOGY_1]` | `Next.js 15` |
| `[TYPE_CHECK_CMD]` | `rtk tsc` |
| `[LINT_CMD]` | `rtk lint` |
| `[SKILL_NAME]` | `forge-ui-conventions` |

---

### Step 3: Run the installer

```bash
./install.sh
```

The installer:
1. Validates `nexus-config.json` (checks required fields, valid JSON)
2. Copies `.claude/agents/` into your project's `.claude/agents/`
3. Merges `settings.json` hooks into your project's `.claude/settings.json`
4. Sets `NEXUS_PROJECT_NAME` from `project.name`
5. Writes `.claude/hooks/.env` with `GATED_SOURCE_PATHS` and `CONTEXT_RESET_AT` from config
6. Runs `python3 generate-project.py` to produce:
   - `docs/agents/TEAM.md` — persona routing table
   - `docs/agents/CONTRACT.md` — sub-agent I/O contract (if not present)
   - `CLAUDE.md` — project directives with your stack
   - `docs/ARCHITECTURE.md` — stub architecture doc (if not present)

---

### Step 4: Verify the install

```bash
# Check that Nexus loads correctly
claude --agent nexus-orchestrator --print "Confirm session start. Run: python3 .memory/log.py session start"

# Verify hooks are wired
cat .claude/settings.json | python3 -c "import json,sys; s=json.load(sys.stdin); print('Hooks:', list(s['hooks'].keys()))"

# Verify memory DB is initialized
python3 .memory/log.py session start
python3 .memory/log.py context dump
```

If `context dump` returns a JSON object with `open_tasks`, `last_session`, and `recent_decisions` keys, the memory system is working.

---

### Step 5: Configure domain skills (optional)

The orchestrator uses skills (`.claude/skills/`) for just-in-time context loading. The template includes the core orchestration skills. Add domain skills for your stack:

- **TypeScript/Next.js**: `forge-ui-conventions`, `rsc-boundary-rules`, `tremor-patterns`
- **Python/FastAPI**: `pipeline-data-conventions`, `pytest-idioms`, `polars-test-fixtures`
- **Database**: `duckdb-read-patterns`, `atlas-schema-patterns`
- **AI/ML**: `ai-sdk-patterns`, `embedding-patterns`

Copy skills from the source project's `.claude/skills/` or write new ones following the skill file format.

---

## Creating domain-specific personas

### The escalation ("pro") pattern

For any domain persona, you can create a `<name>-pro.md` variant that uses a more powerful model for complex/rework tasks:

```yaml
---
name: forge-pro
description: "Same as forge but for COMPLEX tasks or when Lens rework loop is active."
model: opus
effort: xhigh
---
```

The body is identical to the base persona. Nexus dispatches the `-pro` variant when `difficulty=complex` or `stall_count > 0`.

### Adding a persona later

1. Add the persona entry to `nexus-config.json`
2. Run `python3 generate-project.py --add-persona <name>` to generate the agent file stub and update TEAM.md
3. Fill in the `[PLACEHOLDER]` tokens in the generated `.claude/agents/<name>.md`

---

## Environment variables

The hooks read these variables from `.claude/hooks/.env` (written by `install.sh`) and the `env` block in `settings.json`:

| Variable | Default | Description |
|---|---|---|
| `NEXUS_PROJECT_NAME` | `[PROJECT_NAME]` | Project identifier used in memory logs and agent descriptions |
| `MEMORY_TOOL_PATH` | `.memory/files` | Path to session scratchpad files |
| `DB_PATH` | `.memory/project.db` | Path to SQLite memory DB |
| `GATED_SOURCE_PATHS` | `app/,src/,lib/` | Comma-separated prefixes for SocratiCode gate and Lens gate |
| `CONTEXT_RESET_AT` | `10` | Messages before context reset warning fires |
| `REPO_ROOT` | current directory | Absolute path to repo root (used by hooks with absolute paths) |

---

## What gets generated by generate-project.py

Running `generate-project.py` (called automatically by `install.sh`) produces:

| File | Description |
|---|---|
| `docs/agents/TEAM.md` | Persona routing table — which persona owns which domain |
| `docs/agents/CONTRACT.md` | Sub-agent I/O contract (not overwritten if exists) |
| `docs/agents/TEST_CONTRACT.md` | Quill's mandate (not overwritten if exists) |
| `CLAUDE.md` | Project directives with your stack from `nexus-config.json` |
| `docs/ARCHITECTURE.md` | Stub architecture doc (not overwritten if exists) |
| `.claude/hooks/.env` | Hook configuration: `GATED_SOURCE_PATHS`, `CONTEXT_RESET_AT`, `DB_PATH` |
| `docs/agents/SKILL_MAP.md` | Skill routing map (which skills each persona loads JIT) |

Files marked "not overwritten if exists" are safe to customize — re-running `install.sh` or `generate-project.py` will not clobber your edits.

---

## Adding new personas later

```bash
python3 generate-project.py --add-persona <name> \
  --role "TypeScript implementer" \
  --owns "src/frontend/" \
  --stack "React 19, TypeScript 5, Vite" \
  --verification "npx tsc --noEmit, npx eslint src/"
```

This:
1. Appends the persona to `nexus-config.json`
2. Creates `.claude/agents/<name>.md` from `DOMAIN-AGENT-TEMPLATE.md` with placeholders filled
3. Updates `docs/agents/TEAM.md` routing table
4. Adds the persona alias to `settings.json` hook resolver

---

## Troubleshooting

### "socraticode-gate.sh: command not found"
The hooks directory isn't in your project yet. Run `./install.sh` to copy the hooks.

### "python3 .memory/log.py: No module named ..."
The memory system requires Python 3.10+ and the packages in `.memory/requirements.txt`. Run:
```bash
pip install -r .memory/requirements.txt
# or if using uv:
uv pip install -r .memory/requirements.txt
```

### "NEXUS:BLOCKED — cannot write: disallowedTools"
Nexus has `disallowedTools: Write, Edit, NotebookEdit` by design. Nexus orchestrates; implementer personas write code. If you want Nexus to do something directly, it must be delegated to the appropriate persona.

### Agent not auto-loading as nexus-orchestrator
Verify `"agent": "nexus-orchestrator"` is in `.claude/settings.json` and `.claude/agents/nexus-orchestrator.md` exists.

### SocratiCode gate blocking grep before SocratiCode has fired
This is by design. Run any `codebase_search` first to set the flag, then grep is permitted. If SocratiCode isn't indexed yet, run `python3 -m socraticode index .` (or the equivalent for your SocratiCode installation).

### Memory DB not found
Run `python3 .memory/log.py session start` — this initializes the DB if it doesn't exist. If `.memory/` doesn't exist, `install.sh` creates it.

### Hook producing wrong output
Check `.claude/hooks/.env` — missing or wrong `GATED_SOURCE_PATHS` is the most common cause. Re-run `./install.sh` to regenerate it from `nexus-config.json`.
