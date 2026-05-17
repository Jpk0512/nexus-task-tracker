#!/usr/bin/env bash
# Post-install validation for the Nexus Orchestrator template.
# Usage: ./scripts/validate-install.sh [/path/to/project]
# Exit 0 if all checks pass, exit 1 if any fail.

set -e

TARGET="${1:-$(pwd)}"
TARGET="$(cd "$TARGET" && pwd)"  # resolve to absolute

PASS=0
FAIL=0
WARN=0

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

pass() { echo -e "${GREEN}  PASS${NC}  $1"; PASS=$((PASS + 1)); }
fail() { echo -e "${RED}  FAIL${NC}  $1"; FAIL=$((FAIL + 1)); }
warn() { echo -e "${YELLOW}  WARN${NC}  $1"; WARN=$((WARN + 1)); }

echo ""
echo "Nexus Orchestrator — Installation Validation"
echo "  Target: $TARGET"
echo ""

# ── Required Files ────────────────────────────────────────────────────────────
echo "--- Required files ---"

check_file() {
  local path="$TARGET/$1"
  local label="${2:-$1}"
  if [[ -f "$path" ]]; then
    pass "$label"
  else
    fail "$label (not found: $path)"
  fi
}

check_file ".claude/settings.json"         ".claude/settings.json"
check_file ".claude/agents/nexus-orchestrator.md" ".claude/agents/nexus-orchestrator.md"
check_file ".memory/schema.sql"            ".memory/schema.sql"
check_file ".memory/log.py"               ".memory/log.py"
check_file "docs/agents/CONTRACT.md"      "docs/agents/CONTRACT.md"

# ── Memory Database ───────────────────────────────────────────────────────────
echo ""
echo "--- Memory database ---"

DB="$TARGET/.memory/project.db"
if [[ -f "$DB" ]]; then
  if sqlite3 "$DB" "SELECT count(*) FROM sqlite_master;" &>/dev/null; then
    TABLE_COUNT=$(sqlite3 "$DB" "SELECT count(*) FROM sqlite_master WHERE type='table';")
    pass ".memory/project.db initialized ($TABLE_COUNT tables)"
  else
    fail ".memory/project.db exists but sqlite3 cannot open it"
  fi
else
  fail ".memory/project.db not found (run: python3 .memory/log.py init)"
fi

# ── Hook Files Executable ─────────────────────────────────────────────────────
echo ""
echo "--- Hook executability ---"

HOOKS_DIR="$TARGET/.claude/hooks"
if [[ -d "$HOOKS_DIR" ]]; then
  HOOK_COUNT=0
  NON_EXEC=0
  for hook in "$HOOKS_DIR"/*.sh "$HOOKS_DIR"/*.py; do
    [[ -f "$hook" ]] || continue
    HOOK_COUNT=$((HOOK_COUNT + 1))
    if [[ ! -x "$hook" ]]; then
      fail "Not executable: $(basename "$hook")"
      NON_EXEC=$((NON_EXEC + 1))
    fi
  done
  if [[ "$NON_EXEC" -eq 0 && "$HOOK_COUNT" -gt 0 ]]; then
    pass "All $HOOK_COUNT hook files are executable"
  elif [[ "$HOOK_COUNT" -eq 0 ]]; then
    warn "No .sh or .py hook files found in $HOOKS_DIR"
  fi
  # Count total files for reference
  TOTAL_HOOKS=$(ls "$HOOKS_DIR" | wc -l | tr -d ' ')
  if [[ "$TOTAL_HOOKS" -lt 20 ]]; then
    warn "Only $TOTAL_HOOKS files in hooks dir (expected 20+)"
  else
    pass "Hooks directory has $TOTAL_HOOKS files (>=20)"
  fi
else
  fail ".claude/hooks/ directory not found"
fi

# ── Env Vars Check ────────────────────────────────────────────────────────────
echo ""
echo "--- Environment variables ---"

check_env_or_default() {
  local varname="$1"
  local default_desc="$2"
  local val="${!varname:-}"
  if [[ -n "$val" ]]; then
    pass "$varname is set: $val"
  else
    warn "$varname not set — will use default ($default_desc)"
  fi
}

check_env_or_default "REPO_ROOT"          "pwd"
check_env_or_default "DB_PATH"            "<cwd>/.memory/project.db"
check_env_or_default "GATED_SOURCE_PATHS" "app/,src/,lib/"
check_env_or_default "CONTEXT_RESET_AT"   "10"

# ── Env Template Sourced ──────────────────────────────────────────────────────
echo ""
echo "--- Hook env file ---"

ENV_FILE="$TARGET/.claude/hooks/.env"
ENV_TEMPLATE="$TARGET/.claude/hooks/.env.template"

if [[ -f "$ENV_FILE" ]]; then
  pass ".claude/hooks/.env exists"
elif [[ -f "$ENV_TEMPLATE" ]]; then
  warn ".env not present (using defaults). Copy .env.template to .env to customize."
else
  warn ".env.template not found in hooks dir"
fi

# ── Python syntax check on patched hooks ─────────────────────────────────────
echo ""
echo "--- Hook syntax ---"

for pyfile in lens-gate.sh root-cause-gate.sh; do
  fpath="$TARGET/.claude/hooks/$pyfile"
  if [[ -f "$fpath" ]]; then
    if python3 -m py_compile "$fpath" 2>/dev/null; then
      pass "$pyfile Python syntax OK"
    else
      fail "$pyfile has Python syntax errors"
    fi
  fi
done

if bash -n "$TARGET/.claude/hooks/socraticode-gate.sh" 2>/dev/null; then
  pass "socraticode-gate.sh bash syntax OK"
else
  fail "socraticode-gate.sh has bash syntax errors"
fi

# ── LM Studio (optional — degrades gracefully if offline) ────────────────────
echo ""
echo "--- LM Studio connectivity ---"

if curl -sf --max-time 3 "http://127.0.0.1:1234/v1/models" > /dev/null 2>&1; then
  pass "LM Studio reachable at http://127.0.0.1:1234"
else
  warn "LM Studio not reachable at http://127.0.0.1:1234 (router + semantic memory will degrade gracefully)"
  echo "        Start LM Studio and load: qwen3.5-0.8b-intent-classification + nomic-embed-text-v1.5"
fi

# ── sqlite-vec (required for M001 semantic memory) ───────────────────────────
echo ""
echo "--- sqlite-vec (semantic memory) ---"

if python3 -c "import sqlite_vec" 2>/dev/null; then
  pass "sqlite-vec Python package importable"
else
  warn "sqlite-vec not installed — M001 migration and semantic search will be unavailable"
  echo "        Install with: pip install sqlite-vec"
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "─────────────────────────────────────"
echo "  Results: ${PASS} passed  ${FAIL} failed  ${WARN} warnings"
echo "─────────────────────────────────────"
echo ""

if [[ "$FAIL" -gt 0 ]]; then
  echo "Fix the FAIL items above before running 'claude'."
  exit 1
fi

echo "All required checks passed."
exit 0
