# Mid-Session and End-of-Session Logging Commands

Full command reference for logging a unit of work — task progress, decision, or context
snapshot — during or at the close of a session, via `.memory/log.py`.

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

## When to use which

| Situation | Command |
|---|---|
| Starting work on a tracked task | `task-start` |
| Finishing a tracked task | `task-done` (both the task update AND the snapshot — the snapshot is what makes the completion visible in `context dump` next session) |
| A non-trivial architectural or implementation choice was made | `decision` |
| Meaningful progress happened but the session isn't ending | `snapshot` |
| The session is wrapping up | `session-end` — ALWAYS run this explicitly; the Stop hook does not do it for you |
