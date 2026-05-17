---
name: log-work
description: Log a unit of work to .memory/project.db — task progress (start/done), architectural decisions, or context snapshots. Use whenever the user marks work complete, makes a non-trivial decision, or asks to record state mid-session.
---

# log-work

Log a unit of work — task progress, decision, or context snapshot — to the project SQLite DB.

## Usage
`/log-work <action> [args]`

Where `<action>` is one of: `task-done`, `task-start`, `decision`, `snapshot`, `session-end`

---

## task-done
Mark a task complete and log what was done.

**Trigger**: `/log-work task-done TASK-XXX`

```bash
python3 .memory/log.py task update --id TASK-XXX --status done
python3 .memory/log.py context snapshot \
  --action-type code_change \
  --summary "Completed TASK-XXX: <one-line description>" \
  --task-updates '[{"id":"TASK-XXX","status":"done"}]'
```

---

## task-start
Mark a task in-progress.

**Trigger**: `/log-work task-start TASK-XXX`

```bash
python3 .memory/log.py task update --id TASK-XXX --status in_progress
```

---

## decision
Log an architectural or implementation decision.

**Trigger**: `/log-work decision`

Prompt for:
- `--title` "Short imperative title"
- `--context` "Why this decision was needed"
- `--decision` "What was decided"
- `--rationale` "Why this option over alternatives"

Then run:
```bash
python3 .memory/log.py decision add \
  --title "..." \
  --context "..." \
  --decision "..." \
  --rationale "..."
```

---

## snapshot
Log the current state mid-session without ending it.

**Trigger**: `/log-work snapshot`

```bash
python3 .memory/log.py context snapshot \
  --action-type <planning|code_change|verification|research|fix> \
  --files-modified '["path/to/file1", "path/to/file2"]' \
  --summary "<what just happened>"
```

---

## session-end
Close the current session with the user-authored summary and next step.

**Trigger**: `/log-work session-end`

```bash
python3 .memory/log.py session end \
  --summary "<what was accomplished this session>" \
  --next_step "<what to pick up next>"
```

The Stop hook only writes a context snapshot; it never closes the session,
so this command is the canonical way to end one.
