#!/usr/bin/env bash
# Stop hook — writes .memory/files/ from current DB state.
# GATED: skips if no state-changing events occurred this session (cuts ~70% of invocations).
# Requires: ANTHROPIC_API_KEY or AI_API_BASE_URL + ANTHROPIC_API_KEY, python3, sqlite3.

set -euo pipefail

# DB/repo are injectable via _MEM_DB/_MEM_REPO (used by the test harness to point
# the hook at a temp DB). Fall back to the git-derived project paths otherwise.
REPO_ROOT="${_MEM_REPO:-$(git -C "$(dirname "$0")" rev-parse --show-toplevel 2>/dev/null || echo "")}"
if [[ -z "$REPO_ROOT" ]]; then
    exit 0
fi

DB="${_MEM_DB:-$REPO_ROOT/.memory/project.db}"
FILES_DIR="$REPO_ROOT/.memory/files"

if [[ ! -f "$DB" ]]; then
    exit 0
fi

# --- Gate: skip if no state-changing events this session ---
SESSION_ID=$(sqlite3 "$DB" \
    "SELECT id FROM sessions WHERE ended_at IS NULL ORDER BY started_at DESC LIMIT 1" 2>/dev/null || true)

if [[ -z "$SESSION_ID" ]]; then
    exit 0
fi

# NATIVE-27: Validate SESSION_ID to a strict allowlist before interpolating into
# sqlite3 queries. The sqlite3 CLI has no bind-param API in heredoc mode, so we
# sanitize at the shell level: only UUID-ish characters are permitted; anything
# else (quotes, semicolons, SQL keywords) causes a hard exit rather than
# interpolation into a live query.
if [[ ! "$SESSION_ID" =~ ^[A-Za-z0-9_-]+$ ]]; then
    printf '[memory-consolidator] ABORT: SESSION_ID contains disallowed characters (len=%d)\n' \
        "${#SESSION_ID}" >&2
    exit 0
fi

CHANGED=$(sqlite3 "$DB" "
SELECT (
    (SELECT COUNT(*) FROM validation_log     WHERE session_id='$SESSION_ID') +
    (SELECT COUNT(*) FROM context_log        WHERE session_id='$SESSION_ID'
                                              AND action_type IN ('task_update','decision_add','task_add')) +
    (SELECT COUNT(*) FROM agent_notepad      WHERE session_id='$SESSION_ID') +
    (SELECT COUNT(*) FROM agent_root_cause_log WHERE session_id='$SESSION_ID')
) AS total
" 2>/dev/null || echo "0")

if [[ "$CHANGED" == "0" ]]; then
    exit 0
fi

# --- Gather context from DB ---
export _MEM_DB="$DB"
export _MEM_REPO="$REPO_ROOT"

CONTEXT=$(python3 - <<'PYEOF'
import sqlite3, json, os, sys

db_path = os.environ.get("_MEM_DB", "")
repo = os.environ.get("_MEM_REPO", "")
if not db_path or not repo:
    print("{}")
    sys.exit(0)

try:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    rows = conn.execute("""
        SELECT action_type, summary, logged_at
        FROM context_log ORDER BY logged_at DESC LIMIT 50
    """).fetchall()
    log_lines = [f"[{r['logged_at']}] {r['action_type']}: {(r['summary'] or '')[:120]}"
                 for r in rows]

    val_rows = conn.execute("""
        SELECT task_or_brief_hash, verdict, validated_at
        FROM validation_log ORDER BY validated_at DESC LIMIT 20
    """).fetchall()
    val_lines = [f"  {r['task_or_brief_hash']}: {r['verdict']} @ {r['validated_at']}"
                 for r in val_rows]

    task_rows = conn.execute("""
        SELECT id, title, status, assigned_to
        FROM tasks WHERE status IN ('todo','in_progress') ORDER BY id
    """).fetchall()
    task_lines = [f"  {r['id']} [{r['status']}] {r['title']} → {r['assigned_to'] or 'unassigned'}"
                  for r in task_rows]

    sess_row = conn.execute("""
        SELECT summary, next_step, ended_at FROM sessions
        WHERE ended_at IS NOT NULL ORDER BY ended_at DESC LIMIT 1
    """).fetchone()
    last_session = ""
    if sess_row:
        last_session = (f"summary: {(sess_row['summary'] or 'n/a')[:400]}\n"
                        f"next_step: {(sess_row['next_step'] or 'n/a')[:200]}")

    dec_rows = conn.execute("""
        SELECT id, title, decision, decided_at FROM decisions
        ORDER BY decided_at DESC LIMIT 5
    """).fetchall()
    dec_lines = [f"  {r['id']} [{r['decided_at']}]: {r['title']} → {(r['decision'] or '')[:100]}"
                 for r in dec_rows]

    conn.close()

    out = {
        "context_log": log_lines,
        "validation_log": val_lines,
        "open_tasks": task_lines,
        "last_session": last_session,
        "recent_decisions": dec_lines,
    }
    print(json.dumps(out))
except Exception as e:
    print(
        f"[memory-consolidator] context query FAILED ({type(e).__name__}): {e} "
        f"-- likely schema drift vs .memory/schema.sql; consolidation skipped",
        file=sys.stderr,
    )
    print("{}")
PYEOF
)

if [[ -z "$CONTEXT" || "$CONTEXT" == "{}" ]]; then
    exit 0
fi

# --- Call Haiku for Mem0-style ops ---
AI_BASE="${AI_API_BASE_URL:-https://api.anthropic.com}"
AI_MODEL="${MEMORY_CONSOLIDATOR_MODEL:-claude-haiku-4-5-20251001}"
API_KEY="${ANTHROPIC_API_KEY:-}"

if [[ -z "$API_KEY" ]]; then
    exit 0
fi

read -r -d '' SYSTEM_PROMPT <<'SYSTEOF' || true
You maintain a set of memory files for a Claude Code orchestrator session. Given DB context, output JSON with ops to apply to each file. Rules:
- progress.md: current task status summary, ≤500 words.
- session_state.md: last session summary + next_step, ≤300 words.
- verification_state.md: tasks × Lens verdicts table.
- reflections/INDEX.md: one-line entries for any new reflection files.

Output ONLY valid JSON:
{"ops": [{"action": "ADD|UPDATE|NOOP", "file": "progress.md|session_state.md|verification_state.md|reflections/INDEX.md", "content": "full new file content or NOOP"}]}

For NOOP, omit content. Keep content concise. Do not add commentary outside the JSON.
SYSTEOF

REQUEST_BODY=$(python3 -c "
import json, sys
ctx = json.loads(sys.argv[1])
user_msg = 'DB context:\n' + json.dumps(ctx, indent=2)[:6000]
payload = {
    'model': sys.argv[3],
    'max_tokens': 2048,
    'system': sys.argv[2],
    'messages': [{'role': 'user', 'content': user_msg}]
}
print(json.dumps(payload))
" "$CONTEXT" "$SYSTEM_PROMPT" "$AI_MODEL" 2>/dev/null)

if [[ -z "$REQUEST_BODY" ]]; then
    exit 0
fi

RESPONSE=$(curl -sf \
    -H "x-api-key: $API_KEY" \
    -H "anthropic-version: 2023-06-01" \
    -H "content-type: application/json" \
    -d "$REQUEST_BODY" \
    "${AI_BASE}/v1/messages" 2>/dev/null || true)

if [[ -z "$RESPONSE" ]]; then
    exit 0
fi

# --- Apply ops ---
# _MEM_RESPONSE is passed via the environment so the QUOTED heredoc (<<'APPLYEOF')
# never expands it — shell metacharacters and triple-quotes in the model response
# are inert data, not executable code (WF6 heredoc RCE fix).
_MEM_RESPONSE="$RESPONSE" _MEM_FILES_DIR="$FILES_DIR" python3 - <<'APPLYEOF'
import json, os, sys, re

files_dir = os.environ["_MEM_FILES_DIR"]
response_raw = os.environ.get("_MEM_RESPONSE", "")

try:
    resp = json.loads(response_raw)
    text = resp.get("content", [{}])[0].get("text", "")
    # Strip markdown code fences if present
    text = re.sub(r'^[\s\S]*?(\{[\s\S]*\})\s*$', r'\1', text.strip())
    ops_data = json.loads(text)
    ops = ops_data.get("ops", [])
except Exception as e:
    print(f"[memory-consolidator] parse error: {e}", file=sys.stderr)
    sys.exit(0)

WORD_LIMITS = {"progress.md": 500, "session_state.md": 300}

# CWE-22 containment: `fname` is model-controlled (Haiku output over sub-agent-writable
# DB rows), so a returned "../../tmp/PWNED.md" or an absolute path would escape files_dir.
# Restrict writes to the exact 4-file set enumerated in the SYSTEM_PROMPT, then re-assert
# the resolved path stays inside files_dir (belt-and-suspenders against symlink/realpath tricks).
ALLOWED = {"progress.md", "session_state.md", "verification_state.md", "reflections/INDEX.md"}
base = os.path.realpath(files_dir)

for op in ops:
    action = op.get("action", "NOOP")
    fname = op.get("file", "")
    content = op.get("content", "")

    if action == "NOOP" or not fname or not content:
        continue

    if fname not in ALLOWED:
        print(f"[memory-consolidator] SKIP unlisted file: {fname!r}", file=sys.stderr)
        continue

    # Enforce word limits
    limit = WORD_LIMITS.get(fname)
    if limit:
        words = content.split()
        if len(words) > limit:
            content = " ".join(words[:limit]) + "\n\n_[truncated by consolidator]_\n"

    fpath = os.path.join(files_dir, fname)
    real_fpath = os.path.realpath(fpath)
    if os.path.commonpath([real_fpath, base]) != base:
        print(f"[memory-consolidator] SKIP path escapes base: {fname!r}", file=sys.stderr)
        continue

    os.makedirs(os.path.dirname(fpath), exist_ok=True)

    if action in ("ADD", "UPDATE"):
        with open(fpath, "w", encoding="utf-8") as fh:
            fh.write(content)
        print(f"[memory-consolidator] {action} {fname}")

print("[memory-consolidator] done")
APPLYEOF
