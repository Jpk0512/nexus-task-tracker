---
description: Dump project context from .memory/project.db, present it as markdown, and start a new session log entry.
---

> **Note:** run this at the start of a session — manually, or via the
> orchestrator's session-start ritual (see `Skill session-lifecycle`).

Load current project state from the project memory database and surface it
as structured context, then start a new session.

1. Run context dump:
   ```bash
   python3 .memory/log.py context dump --tasks in_progress --decisions 5
   ```

2. Parse the context-dump JSON output and present:
   - **Last session**: ID, summary, next step, branch
   - **Open tasks (in_progress only)**: render the returned `open_tasks` (already in_progress-only) as a table. Do NOT expand the full backlog — it is intentionally omitted. Directly beneath, print a one-line **backlog status counts** summary built from `task_status_counts` (e.g. `Backlog: 40 todo · 3 in_progress · 2 blocked — query project.db for the full list`).
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

### Open Tasks (in_progress)
_Backlog: <task_status_counts, e.g. 40 todo · 3 in_progress · 2 blocked> — run /project-context or query project.db for the full list._
| ID | Title | Status | Assignee |
|----|-------|--------|----------|
...

### Recent Decisions
- DEC-XXX: <title> (<status>)
...

New session started: <new_session_id>
```
