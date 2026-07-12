#!/usr/bin/env bash
# SessionStart hook — prints a LOUD summary of OPEN work at session start so the
# user immediately SEES what was in flight (P5-01 / VIS-01 / GAP-11).
#
# Why this exists: project.db is the durable record of dispatched tasks, but its
# task list diverged silently from the native Claude Code task panel (DB tasks=0
# vs native=17). This hook reads the OPEN tasks straight from project.db and
# prints them at SessionStart, so the native list is reconciled against ground
# truth by a human who can see both — closing the DB↔native blind spot.
#
# Read path: `log.py context dump` (the SAME query UserPromptSubmit already uses)
# returns open_tasks = rows with status NOT IN ('done','cancelled'), each with
# {id,title,status,priority,assigned_to}. log.py self-bootstraps into the
# sqlite-vec-capable interpreter via its re-exec guard, so we prefer the project
# venv python and fall back to system python3.
#
# Advisory only — SessionStart is never blocked; always exit 0.
#
# CAPPED MODE (R5/N45): when .claude/sessionstart-cap.enabled exists, the
# per-task lines STOP going to stderr/additionalContext — the full list (every
# open task, same detail as the uncapped banner) is written instead to
# .memory/files/session-task-reconcile-latest.md, and both stderr and the
# model-facing additionalContext shrink to counts + the top-3 in_progress ids +
# a pointer at that file. Flag ABSENT => byte-for-byte the original verbose
# banner (this is a no-op merge to main until the flag is created separately).
# Full detail eventually moves behind the broker JIT surface (N47); this hook
# only emits the pointer, not the body, once capped.
#
# Wired via .claude/settings.json hooks.SessionStart.

REPO_ROOT="${REPO_ROOT:-$(cd "$(dirname "$0")/../.." && pwd)}"
LOG_PY="$REPO_ROOT/.memory/log.py"

# NEXUS_SESSIONSTART_CAP_FLAG lets tests point at an isolated flag file without
# touching the real repo's .claude/ dir; real invocations always fall back to
# the repo-relative default path.
CAP_FLAG="${NEXUS_SESSIONSTART_CAP_FLAG:-$REPO_ROOT/.claude/sessionstart-cap.enabled}"
CAPPED=false
[ -f "$CAP_FLAG" ] && CAPPED=true

# log.py is the only way in. If it is missing the install is broken — say so on
# stderr, but never wedge SessionStart.
if [ ! -f "$LOG_PY" ]; then
    printf '[session-task-reconcile] ERROR: %s not found — cannot reconcile open tasks.\n' "$LOG_PY" >&2
    exit 0
fi

# Resolve the interpreter: project venv first (sqlite-vec capable), then any
# python3 on PATH. log.py re-execs into the venv itself, so system python3 also
# works; we prefer the venv to avoid the re-exec round trip.
PYBIN=""
for cand in "$REPO_ROOT/.memory/.venv/bin/python" "$REPO_ROOT/.memory/.venv/bin/python3"; do
    if [ -x "$cand" ]; then
        PYBIN="$cand"
        break
    fi
done
if [ -z "$PYBIN" ]; then
    PYBIN="$(command -v python3 || true)"
fi
if [ -z "$PYBIN" ]; then
    printf '[session-task-reconcile] ERROR: no python3 interpreter found — cannot reconcile open tasks.\n' >&2
    exit 0
fi

ERR_LOG="$REPO_ROOT/.memory/files/memory-errors.log"

# Pull the context dump once; route its stderr to the durable memory-errors log
# (consistent with settings.json) rather than swallowing it.
DUMP="$("$PYBIN" "$LOG_PY" context dump 2>>"$ERR_LOG")"
if [ -z "$DUMP" ]; then
    printf '[session-task-reconcile] WARNING: `log.py context dump` produced no output — open-task reconciliation SKIPPED (see %s).\n' "$ERR_LOG" >&2
    exit 0
fi

# Render the open tasks into a human-readable block. Split in_progress (active,
# the headline) from other open statuses (todo/blocked) so the user sees what
# was MID-FLIGHT distinctly from the backlog. The python prints:
#   line 1: IN_PROGRESS count
#   line 2: OTHER open count
#   line 3: comma-separated ids of the first 3 in_progress tasks (capped mode)
#   lines 4+: pre-formatted "  <glyph> TASK-NNN [status/priority] (owner) — title"
RENDER=$(printf '%s' "$DUMP" | python3 -c '
import json, sys

try:
    d = json.load(sys.stdin)
except Exception:
    print("0"); print("0"); print("")
    sys.exit(0)

tasks = d.get("open_tasks", []) or []
in_prog = [t for t in tasks if t.get("status") == "in_progress"]
other = [t for t in tasks if t.get("status") != "in_progress"]

print(len(in_prog))
print(len(other))
print(",".join(str(t.get("id", "?")) for t in in_prog[:3]))


def fmt(t, glyph):
    tid = t.get("id", "?")
    status = t.get("status", "?")
    prio = t.get("priority", "?")
    owner = t.get("assigned_to") or "unassigned"
    title = (t.get("title") or "").strip()
    if len(title) > 90:
        title = title[:87] + "..."
    return f"  {glyph} {tid} [{status}/{prio}] ({owner}) — {title}"


for t in in_prog:
    print(fmt(t, "▶"))
for t in other:
    print(fmt(t, "•"))
' 2>>"$ERR_LOG")

IN_PROG_COUNT=$(printf '%s' "$RENDER" | sed -n '1p')
OTHER_COUNT=$(printf '%s' "$RENDER" | sed -n '2p')
TOP3_IDS=$(printf '%s' "$RENDER" | sed -n '3p')
TASK_LINES=$(printf '%s' "$RENDER" | sed -n '4,$p')

case "$IN_PROG_COUNT" in ''|*[!0-9]*) IN_PROG_COUNT=0 ;; esac
case "$OTHER_COUNT" in ''|*[!0-9]*) OTHER_COUNT=0 ;; esac

TOTAL=$((IN_PROG_COUNT + OTHER_COUNT))

# No open work at all — print a short clean line so the user knows the panel is
# legitimately empty (not that the hook silently no-op'd). Same in both modes.
if [ "$TOTAL" -eq 0 ]; then
    printf '[session-task-reconcile] No open tasks in project.db — native task list should also be empty.\n' >&2
    exit 0
fi

if [ "$CAPPED" = "true" ]; then
    REPORT_DIR="$REPO_ROOT/.memory/files"
    REPORT_PATH="$REPORT_DIR/session-task-reconcile-latest.md"
    mkdir -p "$REPORT_DIR" 2>/dev/null || true

    {
        echo "# Session Task Reconcile — full report"
        echo ""
        echo "Source: project.db (authoritative). Reconcile the NATIVE task list against"
        echo "this — every row below should have a matching native task entry."
        echo ""
        echo "Summary: ${IN_PROG_COUNT} in_progress, ${OTHER_COUNT} other open (${TOTAL} total)"
        echo ""
        echo "## Tasks"
        echo ""
        printf '%s\n' "$TASK_LINES"
        echo ""
        echo "▶ = in_progress (was mid-flight last session — resume or close it out)."
        echo "• = open backlog (todo/blocked)."
        echo "If the native panel shows MORE/FEWER tasks than this, they have DRIFTED —"
        echo "TaskCreate the missing ones / TaskUpdate stale ones to completed."
    } > "$REPORT_PATH" 2>>"$ERR_LOG" || true

    {
        echo ""
        echo "================================================================================"
        echo "  📋  OPEN TASKS AT SESSION START (capped) — ${IN_PROG_COUNT} in_progress, ${OTHER_COUNT} other open (${TOTAL} total)"
        echo "================================================================================"
        echo "  Top in_progress: ${TOP3_IDS:-none}"
        echo "  Full list (all ${TOTAL} open tasks): $REPORT_PATH"
        echo "================================================================================"
        echo ""
    } >&2

    MODEL_CTX="$(
        printf '[session-task-reconcile] OPEN TASKS AT SESSION START (capped) — %s in_progress, %s other open (%s total). Top in_progress: %s. Full report (all %s open tasks): %s — read it or run /project-context for detail. Source: project.db (authoritative).\n' \
            "$IN_PROG_COUNT" "$OTHER_COUNT" "$TOTAL" "${TOP3_IDS:-none}" "$TOTAL" "$REPORT_PATH"
    )"

    jq -n --arg ctx "$MODEL_CTX" '{
        hookSpecificOutput: {
            hookEventName: "SessionStart",
            additionalContext: $ctx
        }
    }'

    exit 0
fi

{
    echo ""
    echo "================================================================================"
    echo "  📋  OPEN TASKS AT SESSION START — ${IN_PROG_COUNT} in_progress, ${OTHER_COUNT} other open"
    echo "================================================================================"
    echo "  Source: project.db (authoritative). Reconcile the NATIVE task list against"
    echo "  this — every row below should have a matching native task entry."
    echo "--------------------------------------------------------------------------------"
    printf '%s\n' "$TASK_LINES"
    echo "--------------------------------------------------------------------------------"
    if [ "$IN_PROG_COUNT" -gt 0 ]; then
        echo "  ▶ = in_progress (was mid-flight last session — resume or close it out)."
    fi
    echo "  • = open backlog (todo/blocked)."
    echo "  If the native panel shows MORE/FEWER tasks than this, they have DRIFTED —"
    echo "  TaskCreate the missing ones / TaskUpdate stale ones to completed."
    echo "================================================================================"
    echo ""
} >&2

# Also surface the open-task list to the MODEL as SessionStart additionalContext.
# The stderr banner above is human-visible only; the orchestrator never sees it,
# so the DB↔native drift it is meant to fix stays invisible to the agent that must
# reconcile it (GAP-11). SOTA 3.7/3.8: open tracked tasks are part of the durable
# protected set that must be re-grounded at the head of every new context. Emit the
# SAME list (same source of truth) as a nested hookSpecificOutput object — the only
# shape the harness surfaces — so a zero-knowledge / post-compaction orchestrator
# resumes with its in-flight + backlog tasks visible and can TaskCreate/TaskUpdate
# to close the drift.
# Model-facing block is COST-BUDGETED: show only the in_progress rows (the
# headline a zero-knowledge orchestrator must resume), not the full backlog.
# TASK_LINES lists the IN_PROG_COUNT in_progress rows FIRST, so slice the head.
IN_PROG_LINES=""
if [ "$IN_PROG_COUNT" -gt 0 ]; then
    IN_PROG_LINES=$(printf '%s' "$TASK_LINES" | sed -n "1,${IN_PROG_COUNT}p")
fi

MODEL_CTX="$(
    printf '[session-task-reconcile] OPEN TASKS AT SESSION START — %s in_progress (listed below), %s other open (todo/blocked) NOT listed to save context — run /project-context for the full backlog. Source: project.db (authoritative). Reconcile the NATIVE task list against this and TaskCreate/TaskUpdate to close drift. ▶=in_progress (was mid-flight last session — resume or close it out).\n%s\n' \
        "$IN_PROG_COUNT" "$OTHER_COUNT" "$IN_PROG_LINES"
)"

jq -n --arg ctx "$MODEL_CTX" '{
    hookSpecificOutput: {
        hookEventName: "SessionStart",
        additionalContext: $ctx
    }
}'

exit 0
