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

# Find the most recent open session
result=$(python3 - <<'PY' 2>/dev/null
import os, sqlite3, json, sys
try:
    conn = sqlite3.connect(os.environ.get("DB_PATH", os.path.join(os.getcwd(), ".memory", "project.db")))
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
