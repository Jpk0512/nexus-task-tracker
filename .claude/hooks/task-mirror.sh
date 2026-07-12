#!/usr/bin/env bash
# PostToolUse:Task hook — surfaces the dispatch→return lifecycle as VISIBLE
# additionalContext so the user can SEE which agents were dispatched and when a
# dispatch closed out (P5-01 / VIS-01 / GAP-11).
#
# Why this exists: the *native* Claude Code task list is the authoritative,
# user-visible view of work (project.db tasks diverged to 0 while native showed
# open work). The orchestrator drives the native TaskCreate/TaskUpdate tools;
# this hook does NOT own that store. Its job is to make the dispatch/return
# LIFECYCLE traceable inside the session transcript:
#
#   • on a Task DISPATCH (no accepted return marker yet)  → emit
#       "[task-mirror] DISPATCH persona=<X> task=<T> …  (native list: in_progress)"
#   • on an accepted NEXUS:DONE return                    → emit
#       "[task-mirror] DONE     persona=<X> task=<T> …  (native list: completed)"
#   • on a REVISE/BLOCKED return                          → emit
#       "[task-mirror] <MARKER> persona=<X> task=<T> …  (still in_progress)"
#
# The line names the native-list transition the orchestrator SHOULD reflect, so
# a missing TaskUpdate is visible as drift rather than silent. Advisory only —
# PostToolUse is never blocked; always exit 0.
#
# Wired via .claude/settings.json hooks.PostToolUse matcher "Task".

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Read the PostToolUse payload from stdin.
INPUT=$(cat)

# Bail quietly on an empty / non-JSON payload — nothing to mirror.
[ -n "$INPUT" ] || exit 0

# ---------------------------------------------------------------------------
# Extract (persona, task_id, marker) from the payload in ONE python pass.
# Mirrors the brief-parsing in stall-counter.sh / broker-gate.py: the Task brief
# lives in tool_input.{description,prompt,value,input} (sometimes a fenced JSON
# block); the return text lives in tool_response/tool_result/output.
# Output: four lines — PERSONA, TASK_ID, MARKER, HAVE_SIGNAL. The first three may
# be blank; HAVE_SIGNAL is "1" only when stdin parsed as a dict AND a real
# dispatch/return signal (a persona or a NEXUS marker) was found, else "0". The
# bash side stays SILENT when HAVE_SIGNAL=0 so malformed/noise payloads do not
# emit a spurious DISPATCH line.
# ---------------------------------------------------------------------------
PARSED=$(printf '%s' "$INPUT" | python3 -c '
import json, re, sys

try:
    d = json.load(sys.stdin)
except Exception:
    print("\n\n\n0")
    sys.exit(0)

if not isinstance(d, dict):
    print("\n\n\n0")
    sys.exit(0)

ti = d.get("tool_input") or d.get("input") or {}
if not isinstance(ti, dict):
    ti = {}


def brief_obj(tool_input):
    """Best-effort dict from the brief (fenced JSON or raw JSON in a field)."""
    for field in ("description", "prompt", "input", "value"):
        raw = tool_input.get(field, "")
        if not isinstance(raw, str) or not raw.strip():
            continue
        for blk in re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL):
            try:
                return json.loads(blk)
            except Exception:
                pass
        try:
            obj = json.loads(raw)
            if isinstance(obj, dict):
                return obj
        except Exception:
            pass
    return {}


brief = brief_obj(ti)

# Persona: explicit subagent_type wins, then brief.subagent_type/persona.
persona = (
    ti.get("subagent_type", "")
    or d.get("subagent_type", "")
    or brief.get("subagent_type", "")
    or brief.get("persona", "")
)
persona = str(persona).strip().lower()

# task_id: brief.task_id, else any TASK-NNN anywhere in the brief text/fields.
task_id = str(brief.get("task_id", "") or "").strip()
if not task_id:
    hay = " ".join(
        v for v in (
            str(ti.get(f, "")) for f in ("description", "prompt", "input", "value")
        ) if v
    )
    m = re.search(r"\b(TASK-\d+)\b", hay)
    if m:
        task_id = m.group(1)

# Return marker: last NEXUS:* marker in the tool response text.
text = ""
for key in ("tool_response", "tool_result", "output", "response"):
    v = d.get(key, "")
    if isinstance(v, str):
        text = v
        break
    if isinstance(v, dict):
        text = v.get("text", "") or json.dumps(v)
        break
    if isinstance(v, list):
        text = " ".join(
            str(it.get("text", "")) for it in v if isinstance(it, dict)
        )
        break

marker = ""
# CONTRACT marker grammar (docs/agents/CONTRACT.md): the completion marker is a
# line STARTING WITH `## NEXUS:<MARKER>` (up to three leading #, optional leading
# whitespace), possibly FOLLOWED BY CONTENT on the same line (a colon + prose, or
# a JSON body). TASK-086: a strict `\s*$` end-anchor falsely rejected a well-formed
# marker that carried trailing content (`## NEXUS:DONE {json}` / `## NEXUS:DONE: …`),
# emitting RETURN-NO-MARKER and keeping the task IN_PROGRESS. Anchor the START of
# line and require a word boundary AFTER the marker token (so trailing content is
# allowed, `## NEXUS:DONEISH` is NOT matched, and a mid-prose `## NEXUS:DONE` —
# which is not at line start — is still correctly rejected as a truncation case).
mm = re.findall(
    r"(?m)^[ \t]*#{0,3}[ \t]*NEXUS:(DONE|REVISE|BLOCKED|NEEDS-DECISION|CHECKPOINT|DEFER-REQUEST)\b",
    text,
)
if mm:
    marker = mm[-1]

# Distinguish a RETURN (response text present) from a DISPATCH (no text).
# A RETURN with text but no well-formed marker line needs a truncation advisory,
# BUT only when the payload has a recognized persona from tool_input (standard
# Task tool structure). Top-level-only persona or no-persona payloads are not
# treated as advisory-worthy returns — they remain DISPATCH or silent.
has_tool_input_persona = bool(
    ti.get("subagent_type", "")
    or brief.get("subagent_type", "")
    or brief.get("persona", "")
)
is_return = bool(text and text.strip()) and has_tool_input_persona
truncation_advisory = is_return and not marker

# A real Task dispatch always carries a subagent_type; a real return always
# carries a NEXUS marker. If neither is present this payload is not a
# dispatch/return worth surfacing — signal the bash side to stay silent.
# Exception: a return WITH text and a tool_input persona but NO marker → advisory.
have_signal = "1" if (persona or marker or truncation_advisory) else "0"

print(persona or "")
print(task_id or "")
print(marker or "")
print(have_signal)
print("1" if truncation_advisory else "0")
' 2>/dev/null)

PERSONA=$(printf '%s' "$PARSED" | sed -n '1p')
TASK_ID=$(printf '%s' "$PARSED" | sed -n '2p')
MARKER=$(printf '%s' "$PARSED" | sed -n '3p')
HAVE_SIGNAL=$(printf '%s' "$PARSED" | sed -n '4p')
TRUNCATION_ADVISORY=$(printf '%s' "$PARSED" | sed -n '5p')

# No identifiable dispatch/return signal (non-JSON, non-dict, or an empty/noise
# payload) — stay silent rather than emit a misleading "DISPATCH persona=unknown".
[ "$HAVE_SIGNAL" = "1" ] || exit 0

# Defaults for the visible line — never print an empty field.
[ -n "$PERSONA" ] || PERSONA="unknown"
[ -n "$TASK_ID" ] || TASK_ID="(no task id)"

# ---------------------------------------------------------------------------
# Decide the lifecycle phase and the native-list transition it implies.
#   DONE              → completed
#   REVISE / BLOCKED  → still in_progress (a retry is coming)
#   NEEDS-DECISION    → still in_progress (paused for the user)
#   no marker + text  → RETURN with no well-formed marker → truncation advisory
#   no marker, no text→ DISPATCH → in_progress
# ---------------------------------------------------------------------------
case "$MARKER" in
    DONE)
        PHASE="DONE"
        NATIVE="native task list: mark this task COMPLETED (TaskUpdate status=completed)"
        ;;
    REVISE|BLOCKED)
        PHASE="$MARKER"
        NATIVE="native task list: keep IN_PROGRESS — a corrective re-dispatch is required"
        ;;
    NEEDS-DECISION)
        PHASE="NEEDS-DECISION"
        NATIVE="native task list: keep IN_PROGRESS — paused for a user decision"
        ;;
    *)
        if [ "$TRUNCATION_ADVISORY" = "1" ]; then
            PHASE="RETURN-NO-MARKER"
            NATIVE="keep IN_PROGRESS — no well-formed NEXUS marker found; possible truncation — verify by diff before marking done"
        else
            PHASE="DISPATCH"
            NATIVE="native task list: this dispatch should appear as IN_PROGRESS (TaskCreate/TaskUpdate)"
        fi
        ;;
esac

CONTEXT="[task-mirror] ${PHASE} persona=${PERSONA} task=${TASK_ID} — ${NATIVE}"

# Emit as additionalContext (visible in-session) + a stderr echo so the
# dispatch→return lifecycle is traceable even if additionalContext is collapsed.
printf '{"hookSpecificOutput":{"hookEventName":"PostToolUse","additionalContext":"%s"}}\n' \
    "$(printf '%s' "$CONTEXT" | sed 's/\\/\\\\/g; s/"/\\"/g')"
printf '%s\n' "$CONTEXT" >&2

exit 0
