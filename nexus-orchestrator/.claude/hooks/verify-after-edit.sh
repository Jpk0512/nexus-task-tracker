#!/bin/bash
# Verify-after-edit hook: runs tsc / ruff on changed TS/PY files and injects
# results as additionalContext.
#
# DUAL ENTRY:
#   (1) PostToolUse on Write|Edit|MultiEdit — runs against a single file from
#       .tool_input.file_path. Inform-only (mid-edit feedback).
#   (2) SubagentStop — defensive fallback: parses files_changed from the
#       sub-agent's response JSON and runs the check on every relevant file.
#       This catches the case where sub-agent Edit events don't bubble up
#       to the parent's PostToolUse (harness-version-specific behavior).
#
# Wired via .claude/settings.json hooks.PostToolUse "Write|Edit|MultiEdit"
# AND hooks.SubagentStop "".
#
# Skips: files under .claude/ or .memory/ (orchestration scaffolding).
# Does NOT block — informs only.

set -e

INPUT=$(cat)

EVENT=$(printf '%s' "$INPUT" | jq -r '.hook_event_name // .event // ""' 2>/dev/null)

PROJECT_ROOT="${REPO_ROOT:-$(pwd)}"

# Collect candidate file paths into the FILES array.
declare -a FILES=()

if [ -z "$EVENT" ] || [ "$EVENT" = "PostToolUse" ]; then
  fp=$(printf '%s' "$INPUT" | jq -r '.tool_input.file_path // .tool_response.filePath // ""' 2>/dev/null)
  if [ -n "$fp" ]; then
    FILES+=("$fp")
  fi
fi

if [ "$EVENT" = "SubagentStop" ] || [ -z "$EVENT" ]; then
  # Best-effort parse of files_changed from the agent's last assistant message
  text=$(printf '%s' "$INPUT" | jq -r '
    .last_assistant_message //
    .response.text //
    .tool_response.text //
    ""
  ' 2>/dev/null)
  if [ -n "$text" ]; then
    paths=$(printf '%s' "$text" | python3 -c '
import sys, re, json
text = sys.stdin.read()
out = []
for block in re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL):
    try:
        obj = json.loads(block)
    except json.JSONDecodeError:
        continue
    fc = obj.get("files_changed")
    if isinstance(fc, list):
        for p in fc:
            if isinstance(p, str):
                out.append(p)
        break
for p in out:
    print(p)
' 2>/dev/null)
    while IFS= read -r p; do
      [ -n "$p" ] && FILES+=("$p")
    done <<< "$paths"
  fi
fi

if [ ${#FILES[@]} -eq 0 ]; then exit 0; fi

# Per-file check accumulator
accumulator=""

for FILE_PATH in "${FILES[@]}"; do
  # Normalize relative paths to absolute under project root
  case "$FILE_PATH" in
    /*) ;;
    *) FILE_PATH="$PROJECT_ROOT/$FILE_PATH" ;;
  esac

  # Skip files outside the project root
  case "$FILE_PATH" in
    "$PROJECT_ROOT"/*) ;;
    *) continue ;;
  esac

  # Skip orchestration scaffolding
  case "$FILE_PATH" in
    */.claude/*|*/.memory/*) continue ;;
  esac

  # Determine check kind
  case "$FILE_PATH" in
    *.ts|*.tsx)
      check_kind="ts"
      ;;
    *.py)
      check_kind="py"
      ;;
    *)
      continue
      ;;
  esac

  if [ ! -f "$FILE_PATH" ]; then continue; fi

  result=""
  if [ "$check_kind" = "ts" ]; then
    cd "$PROJECT_ROOT/app" 2>/dev/null || cd "$PROJECT_ROOT"
    raw=$(rtk tsc --noEmit --skipLibCheck 2>&1 | head -40)
    if [ -n "$raw" ]; then
      result="rtk tsc on $FILE_PATH:\n$raw"
    fi
  elif [ "$check_kind" = "py" ]; then
    case "$FILE_PATH" in
      */ingestion/*) cd "$PROJECT_ROOT/ingestion" ;;
      *) cd "$PROJECT_ROOT" ;;
    esac
    raw=$(uv run ruff check "$FILE_PATH" 2>&1 | head -40)
    if [ -n "$raw" ] && ! echo "$raw" | grep -q "^All checks passed"; then
      result="uv run ruff check $FILE_PATH:\n$raw"
    fi
  fi

  if [ -n "$result" ]; then
    accumulator="${accumulator}\n${result}"
  fi
done

if [ -n "$accumulator" ]; then
  full="[verify-after-edit] post-change check findings:${accumulator}"
  event_out="${EVENT:-PostToolUse}"
  jq -n --arg r "$full" --arg ev "$event_out" '{
    hookSpecificOutput: {
      hookEventName: $ev,
      additionalContext: $r
    }
  }'
fi

exit 0
