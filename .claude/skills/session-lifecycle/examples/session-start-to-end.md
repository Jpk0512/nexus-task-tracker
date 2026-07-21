# Worked example — a full session, start to end

**Start (Part 1):**
```bash
python3 .memory/log.py context dump
```
```
## Project Context — 2026-07-13

**Last session**: sess_0091 — Implemented the settings-page read route.
**Continuing from**: wire up the settings form's save action.

### Open Tasks
| ID | Title | Status | Assignee |
|----|-------|--------|----------|
| TASK-042 | Settings save action | in_progress | forge-wire |

### Recent Decisions
- DEC-014: use optimistic UI update on save (accepted)

New session started: sess_0092
```
```bash
python3 .memory/log.py session start --branch $(git branch --show-current)
```

**Mid-session (Part 2 — snapshot after a meaningful step, before the task is fully done):**
```bash
python3 .memory/log.py context snapshot \
  --action-type code_change \
  --files-modified '["app/api/settings/route.ts"]' \
  --summary "Added PATCH handler for settings save; UI wiring still pending"
```

**Task completion (Part 2 — task-done):**
```bash
python3 .memory/log.py task update --id TASK-042 --status done
python3 .memory/log.py context snapshot \
  --action-type code_change \
  --summary "Completed TASK-042: settings save action wired end-to-end" \
  --task-updates '[{"id":"TASK-042","status":"done"}]'
```

**End (Part 2 — session-end, always explicit):**
```bash
python3 .memory/log.py session end \
  --summary "Completed TASK-042 (settings save action). Verified via curl PATCH + UI screenshot." \
  --next_step "Start TASK-043: add settings validation errors to the UI"
```

**Non-obvious delta:** the `session-end` call is the ONLY thing that closes the session —
the Stop hook writes a snapshot on every turn but never calls `session end` itself.
Skipping this step leaves the session open and `docs/drift-report.md` (if present) has no
comparison baseline for the next session's staleness check.
