---
description: Log a unit of work — task progress, decision, or context snapshot — during or at the close of a session, via the .memory/log.py CLI.
argument-hint: <task-done|task-start|decision|snapshot|session-end> [details]
---

This is the single home of the `.memory/log.py` logging CLI syntax. Do not
re-author this syntax elsewhere — point back here instead.

Log a unit of work against `.memory/project.db` using `.memory/log.py`, per
the request in `$ARGUMENTS`. Pick the matching case below.

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
