#!/bin/bash
# Stop hook companion: when the orchestrator stops, detect whether a session
# is open with non-trivial activity (decisions logged this session or tasks
# transitioned to in_progress/done) and emit a systemMessage reminding Nexus
# to call `python3 .memory/log.py session end --summary ... --next_step ...`
# before the session closes.
#
# Wired via .claude/settings.json hooks.Stop AFTER the existing snapshot +
# sync_docs Step.

set -e

# The install-time token is overridable via env so the hook is testable and
# degrades LOUDLY (not silently) if the token was never rendered. The literal
# /Users/john.keeney/nexus-task-tracker stays the env default; install-time substitution renders it.
DB_PATH="${_HOOK_DB_PATH:-/Users/john.keeney/nexus-task-tracker/.memory/project.db}"

# Find the most recent open session. The Python reads _HOOK_DB_PATH (default:
# the rendered install path). It distinguishes three outcomes on stdout:
#   ""               → genuine clean pass (no open session / no activity)
#   {sid,...}        → emit the normal session-end reminder
#   {"unrendered":1} → install token never substituted; emit a LOUD advisory so
#                      the inert recorder is visible instead of fail-open-silent.
result=$(_HOOK_DB_PATH="$DB_PATH" python3 - <<'PY' 2>/dev/null
import sqlite3, json, sys, os

db_path = os.environ.get("_HOOK_DB_PATH", "/Users/john.keeney/nexus-task-tracker/.memory/project.db")

# Unrendered install token → the path points at a literal "/Users/john.keeney/nexus-task-tracker/..."
# that does not exist; sqlite would happily CREATE an empty db there and every
# query would raise OperationalError, which the old bare `except` swallowed into
# a silent no-reminder. Detect it up front and surface it loudly instead.
if "/Users/john.keeney/nexus-task-tracker" in db_path:
    print(json.dumps({"unrendered": 1}))
    sys.exit(0)

try:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    row = cur.execute(
        "SELECT id, started_at FROM sessions WHERE ended_at IS NULL ORDER BY started_at DESC LIMIT 1"
    ).fetchone()
    if row is None:
        sys.exit(0)
    sid = row["id"]
    # Non-trivial activity = at least one decision logged this session OR
    # at least one task transitioned to in_progress / done with updated_at
    # after the session started.
    dec_count = cur.execute(
        "SELECT count(*) FROM decisions WHERE session_id=?", (sid,)
    ).fetchone()[0]
    task_count = cur.execute(
        "SELECT count(*) FROM tasks WHERE updated_at >= ? AND status IN ('in_progress','done')",
        (row["started_at"],),
    ).fetchone()[0]
    if dec_count > 0 or task_count > 0:
        print(json.dumps({"sid": sid, "dec_count": dec_count, "task_count": task_count}))
    conn.close()
except Exception:
    sys.exit(0)
PY
)

if [ -z "$result" ]; then
    # No reminder needed
    exit 0
fi

# Unrendered install token → emit a LOUD systemMessage so the inert recorder is
# visible. This is advisory (Stop hook), so we never block — but we must not be
# silent about a gate that can no longer find the project db.
if [ "$(printf '%s' "$result" | jq -r '.unrendered // ""')" = "1" ]; then
    jq -n '{
      "systemMessage": "[Session Lifecycle] INSTALL NOT RENDERED — the /Users/john.keeney/nexus-task-tracker token was never substituted, so the session-end reminder cannot locate .memory/project.db and is INERT (no end-of-session reminder will ever fire). Re-run the Nexus install/render step (or set _HOOK_DB_PATH) to restore it. Meanwhile, remember to call: python3 .memory/log.py session end --summary \"<one-line>\" --next_step \"<one-line>\" yourself."
    }'
    exit 0
fi

# Parse the JSON details for the reminder
sid=$(printf '%s' "$result" | jq -r '.sid')
dec_count=$(printf '%s' "$result" | jq -r '.dec_count')
task_count=$(printf '%s' "$result" | jq -r '.task_count')

# Emit systemMessage. Hook protocol: writing to stdout the JSON output
# below tells Claude Code to surface a message and continue.
jq -n --arg sid "$sid" --arg dec "$dec_count" --arg tasks "$task_count" '{
  "systemMessage": ("[Session Lifecycle] Open session \($sid) has " + $dec + " decision(s) and " + $tasks + " task(s) updated this session. Before stopping, call: python3 .memory/log.py session end --summary \"<one-line>\" --next_step \"<one-line>\"  (this is NOT done automatically — the Stop hook only snapshots.)")
}'

exit 0
