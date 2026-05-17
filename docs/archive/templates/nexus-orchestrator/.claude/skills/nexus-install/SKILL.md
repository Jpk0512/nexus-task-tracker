---
name: nexus-install
description: Configure the Nexus orchestrator for this project. Runs the complete project-specific setup interview, generates all config and agent files, initializes the memory DB, and validates the install. Invoke via `Skill nexus-install` on first use in a new project, or to reconfigure an existing install. Safe for existing projects — detects and preserves existing configuration.
---

# Skill: nexus-install

**Trigger:** Invoke via `Skill nexus-install` after `install.sh` has been run and template files are present.

**You are the installing agent.** Work through each phase in order. Do not skip phases. Use AskUserQuestion for all interview questions — do not assume answers. Write files only after the interview is complete.

---

## Phase 0: Announce + Context Load

Print this header before doing anything else:

```
🔧 Nexus Orchestrator Install
═══════════════════════════════
Phases: Safety → System Check → Handoff/Interview → Stack → Docker → Agents+Skills → Generate → Validate → What's Next
```

Read the notepad:

```bash
python3 .memory/log.py notepad list --topic nexus-install
```

---

## Phase 1: Safety Scan — Detect Existing Project

Run:

```bash
# Check existing state
ls nexus-config.json 2>/dev/null && echo "CONFIG_EXISTS" || echo "CONFIG_MISSING"
ls .claude/agents/ 2>/dev/null
python3 .memory/log.py session start 2>&1 | head -3
```

**Decision logic:**

- If `nexus-config.json` exists → **MERGE mode**: use AskUserQuestion with these options:
  - "Reconfigure from scratch" (will overwrite existing config and agent files)
  - "Add/update sections only" (preserve existing, only fill gaps)
  - "Just run validation" (skip to Phase 9)
- If `.claude/agents/` has files beyond `nexus-orchestrator.md`, `scout.md`, `lens.md`, `quill.md`, `quill-py.md` → note each extra file; do not overwrite without user confirmation
- If memory DB already initialized (session start succeeds without error) → "Memory DB already active — preserving existing sessions and tasks"
- If no config, no extra agents, no DB → **FRESH mode**: proceed normally

Report discovery:

```
Discovery:
  nexus-config.json: [EXISTS (MERGE mode) / NOT_FOUND (FRESH mode)]
  Domain agents already present: [list or "none"]
  Memory DB: [initialized / uninitialized]
  Mode: [FRESH / MERGE]
```

---

## Phase 2: System Verification

Check all required infrastructure:

```bash
# LM Studio
curl -sf --max-time 3 "http://127.0.0.1:1234/v1/models" > /dev/null 2>&1 && echo "LM_STUDIO=OK" || echo "LM_STUDIO=OFFLINE"

# sqlite-vec
python3 -c "import sqlite_vec; print('SQLITE_VEC=OK')" 2>/dev/null || echo "SQLITE_VEC=MISSING"

# Docker
docker info > /dev/null 2>&1 && echo "DOCKER=OK" || echo "DOCKER=NOT_RUNNING"
```

**Decision logic:**

- `LM_STUDIO=OFFLINE` → warn but continue: "LM Studio offline. Qwen routing and semantic memory will degrade gracefully. Start LM Studio and load: `qwen3.5-0.8b-intent-classification` + `nomic-embed-text-v1.5`. You can complete setup and start LM Studio later."
- `SQLITE_VEC=MISSING` → warn but continue: "sqlite-vec not installed. Run `pip install sqlite-vec` to enable M001 semantic memory migration. You can continue and install it later."
- `DOCKER=NOT_RUNNING` → **STOP**: use AskUserQuestion before proceeding:
  - "Docker is now running (re-check)" — re-run the Docker check and continue
  - "Skip Docker setup (this is a non-Docker project)" — note the skip and proceed to Phase 3

Report:

```
System Check:
  LM Studio: [OK / Offline — needs qwen3.5-0.8b-intent-classification + nomic-embed-text-v1.5]
  sqlite-vec: [Installed / Missing — run: pip install sqlite-vec]
  Docker: [Running / Not running]
```

---

## Phase 3: Handoff Check + Project Interview

```bash
# Check for handoff directory
ls docs/handoff/ 2>/dev/null && echo "HANDOFF_EXISTS" || echo "NO_HANDOFF"
```

**If `docs/handoff/` exists:**

```bash
# List and read all handoff files
ls docs/handoff/
# Agent reads each file found
```

Extract from handoff docs:
- Project description and purpose
- Key domain concepts and terminology
- Tech stack details (if mentioned)
- Decisions already made (any ADRs or architecture notes)

Print: "Handoff docs found and read. Proceeding with context from handoffs."

Skip the grill-me interview. Proceed to Phase 4 with extracted context — use it to pre-fill answers where possible and confirm with the user.

**If no `docs/handoff/` directory:**

Print: "No handoff docs found — running project discovery interview."

Invoke `Skill grill-me-with-docs`.

The grill-me skill will interview the user relentlessly about their project's domain model, core concepts, and key decisions. It creates or updates `CONTEXT.md` and may produce ADRs. After it completes, extract:
- Project name and description (1 sentence)
- Core domain concepts
- What the system does and does not do
- Key constraints or non-negotiables

Store as working context for all remaining phases.

---

## Phase 4: Tech Stack Configuration

Use AskUserQuestion for each set below. Where grill-me or handoff docs already provided answers, pre-fill and ask the user to confirm or correct.

**Question Set 1 — Project Identity** (ask together):
- Project name (short kebab-case, e.g., `my-api`, `data-platform`)
- Project description (1 sentence — use the one from grill-me/handoff if already collected)
- Primary language(s): TypeScript, Python, Go, Ruby, multiple

**Question Set 2 — Stack** (ask together):
- Frontend: Next.js App Router / React+Vite / Vue+Nuxt / SvelteKit / None (server-only)
- Backend: FastAPI / Express+Hono / Django / Go / Rails / None
- Database: PostgreSQL / MySQL / MongoDB / DuckDB / SQLite / Redis / None
- Queue/background: Redis+Dramatiq / Celery / BullMQ / None

**Question Set 3 — Verification commands** (auto-suggest based on stack, ask user to confirm):
- Type-check: `rtk tsc` (TypeScript), `uv run mypy src/` (Python), or custom
- Lint: `rtk lint` (TS/ESLint), `uv run ruff check` (Python), or custom
- Test frontend: `rtk vitest run` (Next.js/React), or custom
- Test backend: `uv run pytest` (Python), or custom

**Auto-suggestion logic:**
- TypeScript selected → suggest `rtk tsc` + `rtk lint` + `rtk vitest run`
- Python selected → suggest `uv run mypy` + `uv run ruff check` + `uv run pytest`
- Go selected → suggest `go build ./...` + `golangci-lint run` + `go test ./...`

Store all answers as `stack_config`.

---

## Phase 5: Docker Setup (MANDATORY)

Docker is required. If the user chose "Skip Docker setup" in Phase 2, note that and skip to Phase 6 — but explicitly inform the user: "Skipping Docker config. You will need to create docker-compose.yml manually before deploying."

If Docker is running, proceed:

```bash
# Find running containers and their ports
docker ps --format "{{.Names}}\t{{.Ports}}" 2>/dev/null | head -20

# Scan for first available port starting at 5178
for port in 5178 5179 5180 5181 5182 5183 5184 5185; do
  nc -z localhost $port 2>/dev/null && echo "$port: IN USE" || echo "$port: AVAILABLE"
done
```

**Port assignment:**
- Pick the first AVAILABLE port from the scan above (starting at :5178)
- Report: "Assigning port `:XXXX` for your web app (next available sequential slot)"
- Store as `dev_port`

**Ask about services** (AskUserQuestion, multiSelect — pick all that apply):
- Web app (Next.js / React / FastAPI / etc.)
- PostgreSQL database
- Redis
- Worker / background processor
- Custom API service
- Just the web app (single service)

**Generate `docker-compose.yml`:**

Write a `docker-compose.yml` at the project root using the selections above. Use this template, filling in from `stack_config` and `dev_port`:

```yaml
version: "3.9"

services:
  web:
    build: .
    ports:
      - "<dev_port>:3000"
    volumes:
      - ".:/app"
    environment:
      - NODE_ENV=development
    labels:
      - "nexus.project=<project_name>"
      - "nexus.service=web"
    restart: unless-stopped

  # Uncomment and fill in as needed:

  # postgres:
  #   image: postgres:16
  #   environment:
  #     POSTGRES_DB: <project_name>
  #     POSTGRES_USER: app
  #     POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
  #   ports:
  #     - "5432:5432"
  #   volumes:
  #     - postgres_data:/var/lib/postgresql/data
  #   labels:
  #     - "nexus.project=<project_name>"
  #     - "nexus.service=postgres"

  # redis:
  #   image: redis:7-alpine
  #   ports:
  #     - "6379:6379"
  #   labels:
  #     - "nexus.project=<project_name>"
  #     - "nexus.service=redis"

  # worker:
  #   build: .
  #   command: python -m dramatiq worker
  #   volumes:
  #     - ".:/app"
  #   depends_on:
  #     - redis
  #   labels:
  #     - "nexus.project=<project_name>"
  #     - "nexus.service=worker"

# volumes:
#   postgres_data:
```

Uncomment only the blocks for services the user selected.

Also write `.env.template`:

```bash
# Copy to .env and fill in values
# Do NOT commit .env to git

# App
NODE_ENV=development
PORT=<dev_port>

# Database (if using PostgreSQL)
# POSTGRES_PASSWORD=change_me
# DATABASE_URL=postgresql://app:change_me@localhost:5432/<project_name>

# Redis (if using Redis)
# REDIS_URL=redis://localhost:6379

# AI / LLM
# AI_API_BASE_URL=
# AI_API_KEY=
```

After generating:
```
Docker compose ready.
To start: docker compose up -d
Port assigned: :<dev_port>
```

---

## Phase 6: Agent + Skills Catalog

Present a curated recommended list based on `stack_config` collected in Phase 4.

**Core agents (already present in template — no action needed):**
- `scout` — Read-only investigator
- `lens` — QA validator (mandatory)
- `quill` — Test engineer
- `quill-py` — Python test engineer

**Stack-based domain agent recommendations:**

Check `stack_config` and recommend:
- TypeScript / Next.js → `forge` (Frontend/TypeScript implementer)
- Python backend → `pipeline` (Python/data implementer)
- DuckDB or Malloy schemas → `atlas` (Schema/Malloy specialist)
- Multiple services / Docker / Azure / external API wiring → `hermes` (Infrastructure + wiring specialist)
- Design system / UI tokens / Tailwind → `palette` (Design specialist)

**IMPORTANT — Qwen routing depends on the `description:` field in each agent's frontmatter.** The `description:` is what `router_core.py` reads at runtime to decide which agent to dispatch. It must be specific and accurate. You will be asked to define it carefully in Phase 7.

**Curated skills catalog — recommend based on stack:**

Always include (core):
- `nexus-protocol` — orchestration rules and planning gate
- `contract-schema` — delegation brief format
- `team-routing` — persona dispatch rules
- `verification-protocols` — deterministic verification gate
- `project-context` — session start / state surface
- `log-work` — memory logging patterns
- `grill-me-with-docs` — domain discovery interview

TypeScript projects:
- `forge-ui-conventions` — Next.js / Tailwind / Tremor patterns
- `rsc-boundary-rules` — Server Component / client boundary rules
- `ai-sdk-patterns` — Vercel AI SDK 4 patterns
- `server-action-contract` — server action validation patterns

TypeScript UI:
- `tremor-patterns` — Tremor 4 component patterns
- `tailwind-design-tokens` — design token conventions

TypeScript testing:
- `vitest-rtl-idioms` — Vitest + React Testing Library patterns
- `tdd-patterns` — TDD stub-first workflow

Python projects:
- `pipeline-data-conventions` — Polars, DuckDB writes, type hints, ruff
- `pipeline-async-conventions` — async patterns for Python data pipelines

Python data:
- `polars-duckdb-mapping` — Polars ↔ DuckDB type and API mapping
- `embedding-patterns` — embedding generation and storage patterns

Python testing:
- `pytest-idioms` — parametrize, fixture scoping, conftest, xfail patterns
- `polars-test-fixtures` — Polars DataFrame fixture patterns

DuckDB:
- `duckdb-read-patterns` — read-side query patterns
- `atlas-schema-patterns` — Malloy / DuckDB schema conventions
- `duckdb-test-shims` — in-memory DuckDB for tests

Workers:
- `dramatiq-patterns` — Dramatiq + Redis worker patterns

Tableau integration:
- `tableau-client-patterns` — Tableau REST API / VizQL / Metadata API patterns
- `tableau` — general Tableau integration patterns

**Present using AskUserQuestion (multiSelect):**

Show the recommended agents and skills pre-selected based on stack. Let user deselect or add others via "Other" option.

Instructions to user: "These agents and skills are recommended for your stack. Deselect any you don't need. The `description:` field for each domain agent will be configured in the next phase — it drives automatic Qwen routing, so we'll be precise about it."

Store selections as `approved_agents` and `approved_skills`.

---

## Phase 7: Generate Project Files

### 7a. Generate domain agent `.md` files

For each domain agent in `approved_agents` (forge, pipeline, atlas, hermes, palette — NOT scout/lens/quill/quill-py which are already present):

Use AskUserQuestion to collect per-persona details (batch all questions for one persona together):

1. **What directories does this agent OWN?** (e.g., `app/`, `ingestion/`) — these are the paths it may write to
2. **What directories must it NOT touch?** (e.g., `ingestion/`, `app/`) — enforced by its system prompt
3. **One-line description for Qwen routing** — this is the MOST CRITICAL field. It is what `router_core.py` reads to route user requests to this agent. Must be specific and accurate.
   - Format: `"[Role] — owns [dirs]. Handles: [brief work types]."`
   - Example: `"TypeScript implementer for Next.js app/ tree — owns all source under app/. Handles: UI, API routes, server actions, Vitest tests."`
4. **Stack details for this persona** (specific versions and libraries, e.g., "Next.js 15 App Router, TypeScript 5.4, Tailwind CSS 4, Tremor 4, Vitest 2")
5. **Verification commands** (type-check and lint — confirm from stack_config or override per-persona)

Generate `.claude/agents/<persona>.md` using this template:

```markdown
---
name: <persona>
description: "<user-provided one-liner — CRITICAL for Qwen routing>"
model: sonnet
effort: high
disallowedTools: []
---

<!-- ROUTER NOTE: The description: field above is read at runtime by router_core.py
     to build the Qwen routing prompt. Keep it accurate and specific.
     Format: "[Role] — owns [dirs]. Handles: [brief work types]."
     Example: "TypeScript implementer for app/ tree — owns all source under app/" -->

# <Persona Name> — <Role Title>

## Role

<user-provided description>. Spawned by Nexus orchestrator per routing rules — NOT for direct user invocation.

## Leaf executor

You are a leaf executor. You may NOT call the Task tool. You may NOT spawn sub-agents. If you need design clarification, return `## NEXUS:NEEDS-DECISION`. If you need cross-domain help, return `## NEXUS:NEEDS-DECISION` requesting a pairing.

## Owns

<list each owned directory with a brief note on what it contains>

## Do Not Touch

<list each forbidden directory with which persona owns it>
- `.memory/**` — Nexus owns this writeable surface
- `.claude/**` — orchestration meta; Nexus + user only

## Stack (canonical)

<list technologies and versions from user input>

## SocratiCode-first (programmatically enforced)

Discovery starts with SocratiCode (`codebase_search`, `codebase_symbol`, `codebase_graph_query`). The PreToolUse hook blocks grep/rg/find until at least one SocratiCode call has fired in your session.

## Verification (required before completion)

Run BOTH and capture verbatim output in `verification_result`:

```bash
<type-check-command>    # type-check
<lint-command>          # lint
```

If either fails, fix and re-run before returning `## NEXUS:DONE`. If you cannot fix, return `## NEXUS:BLOCKED` with the verbatim error.

## Standards

- Read before edit. Re-read after any other tool changes a file. Don't batch >3 edits to the same file without an interleaved Read.
- No comments unless the WHY is non-obvious.
- No error handling for impossible paths. Validate at boundaries only.
- No backwards-compat shims for removed code.
- Respect `do_not_touch` paths in the brief — if a needed change is forbidden, return `## NEXUS:NEEDS-DECISION`.

## Skill triggers (JIT — load when condition matches)

| Skill | Trigger |
|---|---|
<list each approved skill relevant to this persona with its trigger condition>

## Agent Notepad (mandatory)

Read first, write last. Every dispatch:

1. `python3 .memory/log.py notepad list --topic <topic>` — first action. The topic is in your brief.
2. Do your work.
3. `python3 .memory/log.py notepad add --topic <topic> --agent <persona> --note "..." --kind <kind>` — last action.

Note rules:
- ≤500 chars.
- Insight, not status. "Completed" is forbidden.
- Pick the right kind: gotcha / nuance / reminder / fyi / next-agent-action.

## Completion markers (required as H2)

End every response with exactly one of:

- `## NEXUS:DONE` — code shipped + verification passing
- `## NEXUS:BLOCKED` — cannot ship; blockers listed
- `## NEXUS:NEEDS-DECISION` — design choice or pairing needed
- `## NEXUS:CHECKPOINT` — partial progress, safe resume point
- `## NEXUS:REVISE` — only when responding to a Lens revision request

## Output schema

```json
{
  "status": "complete | partial | blocked | needs-decision",
  "completion_marker": "## NEXUS:DONE",
  "files_changed": ["<owned-dir>/..."],
  "verification_result": "<type-check-cmd>: <verbatim>\n<lint-cmd>: <verbatim>",
  "acceptance_met": [{"criterion": "...", "met": true, "evidence": "..."}],
  "blockers": [],
  "decisions_needed": [],
  "db_log_cmds": ["python3 .memory/log.py task update --id TASK-XXX --status done"],
  "notes": "..."
}
```
```

### 7b. Generate `nexus-config.json`

Write `nexus-config.json` at the project root, filled from all collected data:

```json
{
  "project_name": "<project_name>",
  "project_description": "<project_description>",
  "version": "1.0.0",
  "dev_port": <dev_port>,
  "primary_languages": ["<lang1>"],
  "stack": {
    "frontend": "<framework or null>",
    "backend": "<framework or null>",
    "database": ["<db1>"],
    "queue": "<queue or null>",
    "testing": {
      "frontend": ["<test-framework>"],
      "backend": ["<test-framework>"]
    },
    "verification": {
      "type_check": "<type-check-cmd>",
      "lint": "<lint-cmd>",
      "test_frontend": "<test-frontend-cmd>",
      "test_backend": "<test-backend-cmd>"
    }
  },
  "personas": [
    {
      "name": "<persona-name>",
      "role": "<Role Title>",
      "description": "<the Qwen routing description — CRITICAL>",
      "owns": ["<dir/>"],
      "do_not_touch": ["<other-dir/>"],
      "stack": ["<tech1>", "<tech2>"],
      "verification": ["<type-check-cmd>", "<lint-cmd>"],
      "model": "sonnet",
      "effort": "high"
    }
  ],
  "core_agents": ["nexus", "scout", "lens", "quill", "quill-py"],
  "approved_skills": ["<skill1>", "<skill2>"],
  "model_cascade": {
    "scout": "claude-haiku-4-5",
    "implementers": "claude-sonnet-4-6",
    "nexus": "claude-opus-4-7"
  },
  "lm_studio": {
    "base_url": "http://127.0.0.1:1234/v1",
    "routing_model": "qwen3.5-0.8b-intent-classification",
    "embedding_model": "nomic-embed-text-v1.5"
  },
  "memory": {
    "db_path": ".memory/project.db",
    "log_script": "python3 .memory/log.py"
  },
  "hooks": {
    "gated_source_paths": "<comma-separated owned dirs for all personas>",
    "context_reset_at": 10
  },
  "paths": {
    "docs": "docs/",
    "agents_doc": "docs/agents/",
    "source_roots": ["<owned dirs>"],
    "tests": "tests/"
  }
}
```

### 7c. Generate `CLAUDE.md`

**If `CLAUDE.md` does not exist** → write it fresh.
**If `CLAUDE.md` exists** → use AskUserQuestion: "A CLAUDE.md already exists. Overwrite it, or merge manually?" before writing.

Write `CLAUDE.md` at the project root:

```markdown
# <Project Name> — Agent Directives

## Nexus Protocol

The Nexus orchestrator agent is auto-loaded as the main session via `agent: nexus-orchestrator` in `.claude/settings.json`. Deep protocol detail lives in the **`nexus-protocol`** skill — load on demand via `Skill nexus-protocol`.

You orchestrate; you do not write code (enforced by `disallowedTools: Write, Edit, NotebookEdit`).

## Source of Truth Precedence

When artifacts disagree, resolve in this order (highest authority first):

1. `.memory/project.db` — live decisions, tasks, sessions; canonical runtime state
2. `docs/CONSTITUTION.md` — governance articles; supersedes all docs and agent contracts
3. `docs/` — `DECISIONS.md`, `TASKS.md`, `ARCHITECTURE.md`, `features/*`
4. Nested `CLAUDE.md` files (this file and any subtree directives)

## Stack

- **Frontend**: <frontend framework or "none">
- **Backend**: <backend framework or "none">
- **Database**: <database(s)>
- **Queue**: <queue or "none">
- **Testing**: <test frameworks>
- **Languages**: <primary languages>
- **Dev port**: :<dev_port>

## LM Studio Models (required for Qwen routing + semantic memory)

- Routing model: `qwen3.5-0.8b-intent-classification`
- Embedding model: `nomic-embed-text-v1.5`
- Base URL: `http://127.0.0.1:1234/v1`

Start LM Studio, load both models, then routing is automatic.

## Persona Routing

See `docs/agents/TEAM.md` for full definitions. Quick routing:

<!-- IMPORTANT: The description: field in each .claude/agents/<persona>.md is read
     at runtime by router_core.py for Qwen-based routing. If routing feels wrong,
     edit the description: field in the relevant agent file. -->

| Work type | Lead persona |
|---|---|
<for each generated persona: what kind of work → persona name>

## Memory Logging

```bash
python3 .memory/log.py session start                                          # at session start
python3 .memory/log.py task update --id TASK-XXX --status in_progress|done    # on transitions
python3 .memory/log.py decision add --title "..." --context "..." --decision "..." --rationale "..." --alternatives "..." --consequences "..."
python3 .memory/log.py session end --summary "..." --next_step "..."          # at session end
```

`rationale`, `alternatives`, and `consequences` are not optional — empty rows are a contract violation.

## Codebase Search

SocratiCode first; grep/rg/find/ack/ag are blocked by `.claude/hooks/socraticode-gate.sh` until a SocratiCode discovery tool has fired in the session.

## Worktree Protocol

Feature work on `feat/<slug>` via `EnterWorktree`. Merge locally + push before marking task done. Never commit directly to `main` for feature tasks.

## Code Rules

- No comments unless the WHY is non-obvious
- No error handling for impossible paths
- No backwards-compat shims for removed code

## Verification

<type-check-cmd>   # type-check (must pass before NEXUS:DONE)
<lint-cmd>         # lint (must pass before NEXUS:DONE)

## Feature Specs

Index in `docs/TASKS.md`. Active specs under `docs/features/FEAT-*.md`.

## RTK

Always prefix shell commands with `rtk` (token-optimized proxy).
```

### 7d. Verify approved skills are present

For each skill in `approved_skills`, check if it exists in `.claude/skills/`:

```bash
for skill in <approved_skills_list>; do
  ls .claude/skills/${skill}/SKILL.md 2>/dev/null && echo "OK: ${skill}" || echo "MISSING: ${skill}"
done
```

List any that are MISSING — these must be added manually or sourced from the nexus-orchestrator-template.

---

## Phase 8: Memory Initialization

```bash
# Initialize DB if not already done
python3 .memory/log.py init 2>/dev/null || echo "DB may already exist — skipping init"

# Apply M001 if sqlite-vec is available
python3 -c "import sqlite_vec" 2>/dev/null && {
  echo "sqlite-vec available — applying M001"
  python3 .memory/migrations/apply_M001.py && echo "M001 applied"
} || echo "M001 skipped (sqlite-vec not installed — run: pip install sqlite-vec)"

# Start first session
python3 .memory/log.py session start
```

If session start fails with a schema error:
```bash
python3 .memory/migrations/migrate.py
python3 .memory/log.py session start
```

---

## Phase 9: Validation

Run the validation script:

```bash
bash scripts/validate-install.sh
```

If the script does not exist, run these manual checks:

```bash
# Core structure
ls nexus-config.json && echo "OK: nexus-config.json" || echo "FAIL: nexus-config.json missing"
ls CLAUDE.md && echo "OK: CLAUDE.md" || echo "FAIL: CLAUDE.md missing"
ls .claude/agents/nexus-orchestrator.md && echo "OK: nexus-orchestrator.md" || echo "FAIL"
ls .claude/agents/scout.md && echo "OK: scout.md" || echo "FAIL"
ls .claude/agents/lens.md && echo "OK: lens.md" || echo "FAIL"
ls .claude/agents/quill.md && echo "OK: quill.md" || echo "FAIL"
ls .claude/skills/nexus-protocol/SKILL.md && echo "OK: nexus-protocol" || echo "FAIL"
ls .claude/skills/contract-schema/SKILL.md && echo "OK: contract-schema" || echo "FAIL"
ls .claude/skills/team-routing/SKILL.md && echo "OK: team-routing" || echo "FAIL"
ls .claude/skills/verification-protocols/SKILL.md && echo "OK: verification-protocols" || echo "FAIL"
ls .claude/skills/log-work/SKILL.md && echo "OK: log-work" || echo "FAIL"
ls .claude/skills/project-context/SKILL.md && echo "OK: project-context" || echo "FAIL"
ls docs/CONSTITUTION.md && echo "OK: CONSTITUTION.md" || echo "FAIL"
ls .memory/log.py && echo "OK: log.py" || echo "FAIL"
python3 .memory/log.py session current 2>/dev/null && echo "OK: memory DB live" || echo "FAIL: memory DB not initialized"

# Check generated domain agent files
python3 -c "
import json
c = json.load(open('nexus-config.json'))
for p in c.get('personas', []):
    import os
    path = f\".claude/agents/{p['name']}.md\"
    status = 'OK' if os.path.exists(path) else 'FAIL'
    print(f\"{status}: {path}\")
"
```

Report every FAIL. WARNs are acceptable and can be resolved post-install. Do not proceed to Phase 10 while there are FAILs — fix them first.

---

## Phase 10: What's Next

Print this summary:

```
Nexus Install Complete
═══════════════════════════════
Project: <project_name>
Port: :<dev_port>
Mode: <FRESH / MERGE>

Generated agents:
<for each approved domain agent: - <persona>.md>

Approved skills:
<list approved_skills in columns>

System status:
  LM Studio: [OK / Needs start + load models: qwen3.5-0.8b-intent-classification + nomic-embed-text-v1.5]
  sqlite-vec: [OK / Needs: pip install sqlite-vec]
  Docker: [Running / Skipped]

Next steps:
1. If LM Studio offline:
   - Start LM Studio
   - Load model: qwen3.5-0.8b-intent-classification  (Qwen routing)
   - Load model: nomic-embed-text-v1.5               (semantic memory)
   - The description: field in each .claude/agents/<persona>.md is what Qwen
     reads to route requests — if routing feels off, edit those fields.

2. If sqlite-vec missing:
   pip install sqlite-vec
   python3 .memory/migrations/apply_M001.py

3. Start Docker:
   docker compose up -d

4. Open Claude Code:
   claude

5. First command in Claude Code:
   python3 .memory/log.py session start

6. Create your first feature spec:
   Skill nexus-protocol   (then follow §4 Planning Gate)

Routing note: router_core.py reads the description: frontmatter field from each
agent file at runtime. To adjust routing behavior, edit the description: field
in .claude/agents/<persona>.md — no code changes needed.
```

---

## Last action (mandatory)

```bash
python3 .memory/log.py notepad add \
  --topic nexus-install \
  --agent nexus \
  --note "Install complete. Project: <project_name>, port: <dev_port>, mode: <FRESH/MERGE>, agents: <list>, LM Studio: <OK/offline>, sqlite-vec: <OK/missing>" \
  --kind next-agent-action
```

---

## Appendix A: Troubleshooting

### "generate-project.py not found"

The template's `scripts/` directory must be present. If you copied only `.claude/`, re-copy the full template:

```bash
cp -r nexus-orchestrator-template/scripts ./scripts
```

### "memory DB not initialized"

```bash
python3 .memory/migrations/migrate.py
python3 .memory/log.py session start
```

### "SocratiCode gate blocking grep"

Normal behavior. Fire any `codebase_search` query first — then grep is unblocked for the session.

### "persona agent file not generated"

Verify `nexus-config.json` → `personas` array is valid JSON, then re-run the generator:

```bash
python3 scripts/generate-project.py --config nexus-config.json --force
```

### "Qwen routing sending tasks to wrong agent"

The `description:` frontmatter field in `.claude/agents/<persona>.md` is what `router_core.py` reads at runtime. If routing is wrong, the description is too vague or overlapping with another agent's description. Edit it to be more specific:

```
# Too vague (causes mis-routing):
description: "Engineer who writes code"

# Correct (specific ownership and work types):
description: "TypeScript implementer for Next.js app/ tree — owns all source under app/. Handles: UI components, API routes, server actions, Vitest tests."
```

### "CLAUDE.md has hardcoded project names from a previous project"

Search and replace:

```bash
grep -r "old-project-name" CLAUDE.md .claude/agents/ docs/agents/
```

Replace any found references with your project name.

### "docker-compose.yml port conflicts"

Re-run the port scan and update the port in both `docker-compose.yml` and `nexus-config.json → dev_port`:

```bash
for port in 5178 5179 5180 5181 5182 5183 5184 5185; do
  nc -z localhost $port 2>/dev/null && echo "$port: IN USE" || echo "$port: AVAILABLE"
done
```
