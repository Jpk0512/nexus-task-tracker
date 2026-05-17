# Nexus Orchestrator Template

A portable, project-agnostic Claude Code orchestrator system. Drop into any project to get:

- **Nexus orchestrator agent** — classifies tasks, delegates to specialists, validates output
- **Scout, Lens, Quill** — investigation, QA, and test-engineering agents included
- **23 enforcement hooks** — SocratiCode-first, Lens gate, stall counter, root-cause gate
- **SQLite memory system** — sessions, tasks, decisions, lessons, facts across sessions
- **7 orchestration skills** — nexus-protocol, contract-schema, team-routing, verification-protocols, log-work, project-context, nexus-install
- **`nexus-install` skill** — configure for your project interactively via Claude Code

## Quick Start

```bash
# 1. Copy template content to your project root
cp -r nexus-orchestrator-template/. /path/to/your/project/

# 2. Create your project config
cp nexus-config.example.json nexus-config.json
# Edit nexus-config.json with your stack details

# 3. Run the installer
cd /path/to/your/project
bash install.sh

# 4. Open Claude Code — Nexus loads automatically
claude
```

## Or: Let the agent configure it

After copying the template and running `install.sh`, open Claude Code and run:
```
Skill nexus-install
```
The agent will interview you about your stack and generate all project-specific files.

## What's Included

| Component | Files | Purpose |
|---|---|---|
| Core agents | `.claude/agents/` | nexus-orchestrator, scout, lens, quill, quill-py |
| Domain agent template | `.claude/DOMAIN-AGENT-TEMPLATE.md` | Starting point for project-specific personas |
| Enforcement hooks | `.claude/hooks/` | 23 hooks enforcing constitutional rules |
| Memory system | `.memory/` | SQLite DB, log.py CLI, migrations |
| Orchestration skills | `.claude/skills/` | 7 skills including nexus-install |
| Protocol docs | `docs/agents/` | CONTRACT.md, CONSTITUTION.md, TEST_CONTRACT.md |
| Settings | `.claude/settings.json` | Hook wiring, permissions, agent config |
| Example config | `nexus-config.example.json` | Annotated config with two example personas |
| Setup guide | `SETUP.md` | Step-by-step install + troubleshooting |

## Configuration

Copy `nexus-config.example.json` to `nexus-config.json` and edit before running `install.sh`:

```json
{
  "project": { "name": "my-project", "description": "..." },
  "tech_stack": {
    "frontend": "Next.js 15",
    "backend": "FastAPI",
    "verification": {
      "type_check": "rtk tsc",
      "lint": "rtk lint"
    }
  },
  "personas": [
    {
      "name": "forge",
      "role": "TypeScript implementer",
      "owns": ["app/"],
      "do_not_touch": ["ingestion/"]
    }
  ],
  "hooks": { "gated_source_paths": "app/,src/" }
}
```

See `nexus-config.example.json` for a complete annotated example and `SETUP.md` for full instructions.

## Domain Personas

The template ships 4 core agents (nexus, scout, lens, quill). You add domain specialists via the `personas` array in `nexus-config.json` — `install.sh` runs `generate-project.py` which creates the agent files from `DOMAIN-AGENT-TEMPLATE.md`.

See `SETUP.md` under "Creating domain-specific personas".

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `REPO_ROOT` | `$(pwd)` | Absolute path to repo root |
| `DB_PATH` | `.memory/project.db` | SQLite memory database |
| `GATED_SOURCE_PATHS` | `app/,src/,lib/` | Lens gate source prefixes |
| `CONTEXT_RESET_AT` | `10` | Messages before context-reset warning |
| `NEXUS_PROJECT_NAME` | from config | Project identifier in logs |

## Requirements

- Claude Code (`claude` CLI)
- Python 3.10+
- SQLite3 (built into Python)
- Optional: `sqlite-vec` for semantic memory (`python3 .memory/migrations/apply_M001.py`)

## License

MIT — see LICENSE file.
