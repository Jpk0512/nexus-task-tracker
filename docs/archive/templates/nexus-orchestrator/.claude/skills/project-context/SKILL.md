---
name: project-context
description: Surface current project state from .memory/project.db — last session summary, open tasks grouped by feature, and recent decisions — then start a new session. Use at the start of any work session, when the user asks "what's the state of things", or after /clear.
---

# project-context

Load current project state from the SQLite memory DB and surface it as structured context.

## Steps

1. Run context dump:
   ```bash
   python3 .memory/log.py context dump
   ```

2. Parse the JSON output and present:
   - **Last session**: ID, summary, next step, branch
   - **Open tasks**: grouped by feature (FEAT-001, FEAT-002, etc.), with status and assignee
   - **Recent decisions**: last 5, title + status

3. Start a new session log entry:
   ```bash
   python3 .memory/log.py session start --branch $(git branch --show-current)
   ```

4. Output a compact markdown summary:

```
## Project Context — <date>

**Last session**: <session_id> — <summary>
**Continuing from**: <next_step>

### Open Tasks
| ID | Title | Status | Assignee |
|----|-------|--------|----------|
...

### Recent Decisions
- DEC-XXX: <title> (<status>)
...

New session started: <new_session_id>
```
