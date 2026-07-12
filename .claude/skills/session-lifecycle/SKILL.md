---
name: session-lifecycle
description: Session lifecycle for .memory/project.db — start-of-session context resume (open tasks, recent decisions, last summary) AND mid/end-of-session logging (task progress, decisions, snapshots, session-end). Use at the start of any work session, when the user asks "what's the state of things", after /clear, whenever the user marks work complete or makes a non-trivial decision, or when logging state mid-session.
---

# session-lifecycle

Formerly two skills (`project-context` + `log-work`), merged: they are the two
halves of the same lifecycle against the same DB (`.memory/project.db` via
`.memory/log.py`) — one reads state IN at session start, the other writes state
OUT during and at the end of a session. `log-work` now redirects here (its
directory is a 3-line pointer stub; do not re-author content there).

---

## Part 1 — Resume (start of session)

Load current project state from the SQLite memory DB and surface it as structured context.

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

---

## Part 2 — Log (mid-session and end-of-session)

Log a unit of work — task progress, decision, or context snapshot — during or
at the close of a session.

### task-done
Mark a task complete and log what was done.

```bash
python3 .memory/log.py task update --id TASK-XXX --status done
python3 .memory/log.py context snapshot \
  --action-type code_change \
  --summary "Completed TASK-XXX: <one-line description>" \
  --task-updates '[{"id":"TASK-XXX","status":"done"}]'
```

### task-start
Mark a task in-progress.

```bash
python3 .memory/log.py task update --id TASK-XXX --status in_progress
```

### decision
Log an architectural or implementation decision. Prompt for `--title`,
`--context`, `--decision`, `--rationale`, then run:

```bash
python3 .memory/log.py decision add \
  --title "..." \
  --context "..." \
  --decision "..." \
  --rationale "..."
```

### snapshot
Log the current state mid-session without ending it.

```bash
python3 .memory/log.py context snapshot \
  --action-type <planning|code_change|verification|research|fix> \
  --files-modified '["path/to/file1", "path/to/file2"]' \
  --summary "<what just happened>"
```

### session-end
Close the current session with the user-authored summary and next step.

```bash
python3 .memory/log.py session end \
  --summary "<what was accomplished this session>" \
  --next_step "<what to pick up next>"
```

The Stop hook only writes a context snapshot; it never closes the session,
so this command is the canonical way to end one.
