#!/usr/bin/env bash
# Nexus Orchestrator Installer
# Usage: ./install.sh [--target /path/to/project] [--config nexus-config.json]
#
# Flags:
#   --target DIR     Target project directory (default: current directory)
#   --config FILE    Config JSON file to use (default: nexus-config.json)
#   --help           Show this help message

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# ── Defaults ──────────────────────────────────────────────────────────────────
TARGET="$(pwd)"
CONFIG=""
HELP=0

# ── Arg parsing ───────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --target)
      TARGET="$2"
      shift 2
      ;;
    --config)
      CONFIG="$2"
      shift 2
      ;;
    --help|-h)
      HELP=1
      shift
      ;;
    *)
      echo "ERROR: Unknown argument: $1" >&2
      echo "Run with --help for usage." >&2
      exit 1
      ;;
  esac
done

if [[ "$HELP" == "1" ]]; then
  cat <<EOF
Nexus Orchestrator Installer

USAGE:
  ./install.sh [--target /path/to/project] [--config nexus-config.json]

FLAGS:
  --target DIR     Target project directory (default: current directory)
  --config FILE    Path to nexus-config.json with project customizations
                   (default: look for nexus-config.json in current dir)
  --help           Show this message

EXAMPLES:
  # Install into current directory with interactive prompts
  ./install.sh

  # Install into a specific project dir using a config file
  ./install.sh --target ~/my-project --config ~/my-project/nexus-config.json

  # Install using default config discovery
  ./install.sh --target ~/my-project

WHAT IT DOES:
  1. Copies .claude/hooks/, .memory/, and template files to TARGET
  2. Runs generate-project.py to produce project-specific docs/agents/ files
  3. Initializes the SQLite memory database
  4. Makes all hooks executable
  5. Validates the installation

EOF
  exit 0
fi

echo ""
echo "Nexus Orchestrator Installer"
echo "   Template: $SCRIPT_DIR"
echo "   Target:   $TARGET"
echo ""

# ── Step 1: Validate target directory ─────────────────────────────────────────
if [[ ! -d "$TARGET" ]]; then
  echo "ERROR: Target directory does not exist: $TARGET" >&2
  echo "  Create it first with: mkdir -p $TARGET" >&2
  exit 1
fi

TARGET="$(cd "$TARGET" && pwd)"  # resolve to absolute

# ── Step 2: Check for existing install (warn, don't abort) ───────────────────
# ── Check for existing Nexus install ─────────────────────────────────────────
EXISTING_NEXUS=0
if [[ -f "$TARGET/nexus-config.json" ]] || [[ -f "$TARGET/.claude/agents/nexus-orchestrator.md" ]]; then
  EXISTING_NEXUS=1
fi

if [[ "$EXISTING_NEXUS" == "1" ]]; then
  echo ""
  echo "WARNING: Existing Nexus install detected in $TARGET"
  echo "   (nexus-config.json or .claude/agents/nexus-orchestrator.md already exists)"
  echo ""
  echo "   For existing projects, use the install skill instead:"
  echo "     1. Open Claude Code in $TARGET"
  echo "     2. Run: Skill nexus-install"
  echo "   The skill safely merges with your existing configuration."
  echo ""
  echo "   Continuing with install.sh WILL OVERWRITE existing hook and memory files."
  echo "   Press Ctrl-C within 10 seconds to abort."
  sleep 10
elif [[ -d "$TARGET/.claude/hooks" ]]; then
  echo "WARNING: Existing .claude/hooks/ found in $TARGET"
  echo "   Files will be overwritten. Press Ctrl-C within 5 seconds to abort."
  sleep 5
fi

# ── Step 3: Copy template files to target ────────────────────────────────────
echo "[1/7] Copying hook files..."

mkdir -p "$TARGET/.claude/hooks"
cp -r "$SCRIPT_DIR/.claude/hooks/." "$TARGET/.claude/hooks/"

# Copy core agent files (do not overwrite the persona-derived files that the
# generator will produce later — these are the canonical agents only).
mkdir -p "$TARGET/.claude/agents"
for agent in nexus-orchestrator.md scout.md lens.md quill.md quill-py.md DOMAIN-AGENT-TEMPLATE.md; do
  if [[ -f "$SCRIPT_DIR/.claude/agents/$agent" ]]; then
    cp "$SCRIPT_DIR/.claude/agents/$agent" "$TARGET/.claude/agents/$agent"
    echo "   copied .claude/agents/$agent"
  fi
done

# Copy the skills catalog. Skills are idempotent — overwrite is safe.
if [[ -d "$SCRIPT_DIR/.claude/skills" ]]; then
  mkdir -p "$TARGET/.claude/skills"
  cp -r "$SCRIPT_DIR/.claude/skills/." "$TARGET/.claude/skills/"
  echo "   copied .claude/skills/ ($(ls "$SCRIPT_DIR/.claude/skills" | wc -l | tr -d ' ') skills)"
fi

# Copy settings.json only if not already present (preserve user customizations).
if [[ ! -f "$TARGET/.claude/settings.json" && -f "$SCRIPT_DIR/.claude/settings.json" ]]; then
  cp "$SCRIPT_DIR/.claude/settings.json" "$TARGET/.claude/settings.json"
  echo "   copied .claude/settings.json"
fi

# Copy governance + contract docs.
mkdir -p "$TARGET/docs/agents"
for doc in CONSTITUTION.md; do
  if [[ ! -f "$TARGET/docs/$doc" && -f "$SCRIPT_DIR/docs/$doc" ]]; then
    cp "$SCRIPT_DIR/docs/$doc" "$TARGET/docs/$doc"
    echo "   copied docs/$doc"
  fi
done
for doc in CONTRACT.md TEST_CONTRACT.md; do
  if [[ ! -f "$TARGET/docs/agents/$doc" && -f "$SCRIPT_DIR/docs/agents/$doc" ]]; then
    cp "$SCRIPT_DIR/docs/agents/$doc" "$TARGET/docs/agents/$doc"
    echo "   copied docs/agents/$doc"
  fi
done

echo "[2/7] Copying memory system files..."

mkdir -p "$TARGET/.memory/migrations"
mkdir -p "$TARGET/.memory/files"

# Only copy files that don't already exist (memory system is idempotent)
for f in schema.sql log.py; do
  if [[ ! -f "$TARGET/.memory/$f" ]]; then
    cp "$SCRIPT_DIR/.memory/$f" "$TARGET/.memory/$f"
    echo "   copied .memory/$f"
  else
    echo "   skipped .memory/$f (already exists)"
  fi
done

if [[ -f "$SCRIPT_DIR/.memory/migrations/apply_M001.py" ]]; then
  if [[ ! -f "$TARGET/.memory/migrations/apply_M001.py" ]]; then
    cp "$SCRIPT_DIR/.memory/migrations/apply_M001.py" "$TARGET/.memory/migrations/apply_M001.py"
    echo "   copied .memory/migrations/apply_M001.py"
  else
    echo "   skipped .memory/migrations/apply_M001.py (already exists)"
  fi
fi

# Copy .gitignore only if target doesn't have one
if [[ ! -f "$TARGET/.gitignore" ]]; then
  cp "$SCRIPT_DIR/.gitignore" "$TARGET/.gitignore"
  echo "   copied .gitignore"
else
  echo "   skipped .gitignore (already exists — review manually)"
fi

# ── Step 4: Run generate-project.py ──────────────────────────────────────────
echo "[3/7] Running project generator..."

GENERATOR="$SCRIPT_DIR/scripts/generate-project.py"
if [[ ! -f "$GENERATOR" ]]; then
  echo "ERROR: generator script not found: $GENERATOR" >&2
  exit 1
fi

# Resolve config path
if [[ -n "$CONFIG" ]]; then
  if [[ ! -f "$CONFIG" ]]; then
    echo "ERROR: Config file not found: $CONFIG" >&2
    exit 1
  fi
  python3 "$GENERATOR" --config "$CONFIG" --target "$TARGET"
elif [[ -f "$TARGET/nexus-config.json" ]]; then
  echo "   Found nexus-config.json in target, using it."
  python3 "$GENERATOR" --config "$TARGET/nexus-config.json" --target "$TARGET"
elif [[ -f "nexus-config.json" ]]; then
  echo "   Found nexus-config.json in current dir, using it."
  python3 "$GENERATOR" --config "nexus-config.json" --target "$TARGET"
else
  echo "   No config file found. Running in interactive mode..."
  python3 "$GENERATOR" --interactive --target "$TARGET"
fi

# ── Step 5: Initialize memory database ───────────────────────────────────────
echo "[4/7] Initializing memory database..."

if [[ ! -f "$TARGET/.memory/project.db" ]]; then
  if command -v sqlite3 &>/dev/null; then
    sqlite3 "$TARGET/.memory/project.db" < "$TARGET/.memory/schema.sql"
    echo "   Created .memory/project.db"
  elif command -v python3 &>/dev/null; then
    python3 "$TARGET/.memory/log.py" init
    echo "   Created .memory/project.db via log.py init"
  else
    echo "WARNING: Neither sqlite3 nor python3 found. DB not initialized." >&2
    echo "   Run manually: python3 .memory/log.py init" >&2
  fi
else
  echo "   .memory/project.db already exists — skipping init"
fi

# ── Step 6: Optional M001 migration ──────────────────────────────────────────
echo "[5/7] Checking optional sqlite-vec migration (M001)..."

if [[ -f "$TARGET/.memory/migrations/apply_M001.py" ]]; then
  if python3 -c "import sqlite_vec" &>/dev/null 2>&1; then
    echo "   sqlite-vec found. Applying M001..."
    python3 "$TARGET/.memory/migrations/apply_M001.py" || {
      echo "WARNING: M001 migration failed (non-fatal). Semantic search will be unavailable." >&2
    }
  else
    echo "   sqlite-vec not installed. M001 skipped (semantic search disabled)."
    echo "   To enable: pip install sqlite-vec, then run: python3 .memory/migrations/apply_M001.py"
  fi
fi

# ── Step 7: Make all hooks executable ────────────────────────────────────────
echo "[6/7] Setting hook permissions..."

chmod +x "$TARGET/.claude/hooks"/*.sh 2>/dev/null || true
# Python hook files that are invoked directly
for pyfile in lens-gate.sh root-cause-gate.sh; do
  if [[ -f "$TARGET/.claude/hooks/$pyfile" ]]; then
    chmod +x "$TARGET/.claude/hooks/$pyfile"
  fi
done

# ── Step 8: Validate installation ─────────────────────────────────────────────
echo "[7/7] Validating installation..."

VALIDATOR="$SCRIPT_DIR/scripts/validate-install.sh"
if [[ -f "$VALIDATOR" ]]; then
  bash "$VALIDATOR" "$TARGET" || {
    echo "" >&2
    echo "WARNING: Validation found issues above. Review and fix before running claude." >&2
    exit 1
  }
else
  echo "WARNING: validate-install.sh not found — skipping validation" >&2
fi

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo "Installation complete! Run 'claude' in $TARGET to start."
echo "Read SETUP.md for next steps."
echo ""
echo "Quick start:"
echo "  cd $TARGET"
echo "  claude"
echo ""
