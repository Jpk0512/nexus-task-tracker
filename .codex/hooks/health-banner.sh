#!/usr/bin/env bash
# health-banner.sh — SessionStart hook; emits a nested hookSpecificOutput banner.
# Always surfaces the installed Nexus version ("Nexus v<version>" from
# .memory/.nexus-version, silent if absent) so the orchestrator can self-report;
# adds the health summary only when not green.
#
# META-FAILURE SHOUT: the persistence layer (.memory/log.py) is what every health
# check, memory write, and session log depends on. If it is ABSENT the install is
# incomplete and the self-test cannot even run — that is the single most important
# moment to SHOUT, not to silently no-op. We detect that case explicitly here and
# emit a loud additionalContext banner via the same hookSpecificOutput contract.

# ---------------------------------------------------------------------------
# WRITABILITY PRE-FLIGHT (NATIVE-58) — runs BEFORE set -e so probes are safe
# Root cause guarded: if .memory/ is absent or non-writable every subsequent
# `log.py session start` call silently no-ops under `|| true` / `2>/dev/null`,
# orphaning the session with zero log rows. touch+unlink is the only reliable
# probe for CREATE-in-dir permission — `mkdir -p` returning 0 only proves the
# dir exists, not that files can be created inside it.
# ---------------------------------------------------------------------------
_SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# Honour an injected REPO_ROOT (tests / CI) before falling back to the hook's own location.
_REPO_ROOT="${REPO_ROOT:-$(cd "$_SCRIPT_DIR/../.." && pwd)}"
_MEMORY_DIR="$_REPO_ROOT/.memory"
_PREFLIGHT_PROBE="$_MEMORY_DIR/.write_probe_$$"
_MEMORY_WRITABLE=true

if [ ! -d "$_MEMORY_DIR" ]; then
    mkdir -p "$_MEMORY_DIR" 2>/dev/null || true
fi

if [ ! -d "$_MEMORY_DIR" ]; then
    _MEMORY_WRITABLE=false
elif ! touch "$_PREFLIGHT_PROBE" 2>/dev/null; then
    _MEMORY_WRITABLE=false
else
    rm -f "$_PREFLIGHT_PROBE" 2>/dev/null || true
fi

if [ "$_MEMORY_WRITABLE" = "false" ]; then
    echo "NEXUS MEMORY UNWRITABLE: $_MEMORY_DIR — sessions will NOT be recorded. Fix permissions or recreate the directory, then restart the session." >&2
    mkdir -p "$_MEMORY_DIR" 2>/dev/null || true
    python3 -c "
import json, sys
print(json.dumps({'hookSpecificOutput': {'hookEventName': 'SessionStart', 'additionalContext': 'NEXUS MEMORY UNWRITABLE: $_MEMORY_DIR — sessions will NOT be recorded. All memory hooks will silently no-op. Fix before any work: check permissions / disk space, then restart the session.'}}))
" 2>/dev/null || true
    exit 0
fi

set -e
cd "$(dirname "$0")/../.."   # project root

# Resolve a Python ≥3.11 via the _py.sh shim when present (composes with the
# NATIVE-4 resolver) without owning it; fall back to bare python3 otherwise.
PY="$(dirname "$0")/_py.sh"
[ -x "$PY" ] || PY="python3"

version=""
if [ -f .memory/.nexus-version ]; then
  version=$(head -n1 .memory/.nexus-version 2>/dev/null | tr -d '[:space:]')
fi

# logpy_state drives the meta-failure shout:
#   missing  → .memory/log.py absent (persistence layer not initialized)
#   present  → log.py exists; run the health self-test and report as usual
#   broken   → log.py exists but `health` exited nonzero (command absent /
#              crashed / errored). This is an install that LOOKS present but
#              cannot self-test — a distinct, loud failure mode. We do NOT
#              discard stderr here: a broken self-test must never masquerade
#              as a healthy (quiet) install.
logpy_state="present"
out=""
health_err=""
if [ -f .memory/log.py ]; then
  # Capture stdout and stderr separately and the exit code explicitly. Do NOT
  # 2>/dev/null — a nonzero rc with diagnostic stderr is exactly the broken
  # case we must SHOUT about, not silently swallow.
  health_err=$(mktemp 2>/dev/null || echo "${TMPDIR:-/tmp}/nexus-health-err.$$")
  # `set -e` is active: a bare failing assignment would abort the whole hook
  # before we can branch on rc. Capture rc explicitly with `&& / ||` so a
  # nonzero exit is handled here (the broken-install SHOUT) rather than killing
  # the script. We deliberately do NOT swallow stderr (it lands in $health_err).
  rc=0
  out=$("$PY" .memory/log.py health --no-runtime --json 2>"$health_err") || rc=$?
  if [ "$rc" -ne 0 ]; then
    logpy_state="broken"
    # Keep a short tail of stderr for the shout; bounded so the banner stays sane.
    health_err=$(tail -c 400 "$health_err" 2>/dev/null | tr '\n' ' ')
  else
    rm -f "$health_err" 2>/dev/null || true
    health_err=""
  fi
else
  logpy_state="missing"
fi

VERSION="$version" HEALTH_ERR="$health_err" "$PY" - <<'PYEOF' "$out" "$logpy_state"
import json, os, sys
version = os.environ.get("VERSION", "").strip()
lines = []
if version:
    lines.append(f"Nexus v{version}")

raw = sys.argv[1] if len(sys.argv) > 1 else ""
logpy_state = sys.argv[2] if len(sys.argv) > 2 else "present"
health_err = os.environ.get("HEALTH_ERR", "").strip()


def _shout_broken(detail):
    # log.py EXISTS but its health self-test could not produce a usable report
    # (nonzero exit, crash, or non-JSON output). This is a DISTINCT failure
    # from absent log.py and must NEVER be reported as a silent-healthy install:
    # a broken self-test means we genuinely do not know the install's health.
    msg = (
        "⚠ NEXUS HEALTH SELF-TEST BROKEN — .memory/log.py is present but its "
        "`health` self-test did not return a usable report. The install LOOKS "
        "present but cannot verify itself, so its true health is UNKNOWN — do "
        "NOT treat this as healthy. Repair before doing any work: re-run the "
        "installer or update Nexus so `python3 .memory/log.py health "
        "--no-runtime --json` emits a valid report."
    )
    if detail:
        msg += f" (self-test said: {detail})"
    lines.append(msg)


if logpy_state == "missing":
    # Persistence layer absent — the install is incomplete and NOTHING that
    # depends on .memory/ can run. Shout loudly; never silently no-op here.
    lines.append(
        "⚠ NEXUS INSTALL INCOMPLETE — .memory/log.py is missing (the "
        "persistence layer is not initialized). The self-test could not run. "
        "Repair before doing any work: re-run the installer, or  python3 "
        ".memory/log.py init  if schema.sql is present. Do NOT start workflows "
        "against a dead install."
    )
elif logpy_state == "broken":
    # The health command exited nonzero (absent command / crash / error).
    _shout_broken(health_err)
else:
    # present: log.py ran the self-test with rc=0. It MUST be parseable JSON;
    # if it is not (or has no summary), the self-test is broken even though it
    # exited 0 — SHOUT, do not fall back to silent-healthy.
    try:
        data = json.loads(raw) if raw else None
        if not isinstance(data, dict) or 'summary' not in data:
            raise ValueError("missing summary")
        fails, warns = data['summary']['fails'], data['summary']['warns']
        if fails or warns:
            lines.append(
                f"⚠ Nexus health: {data['summary']['passes']} PASS · "
                f"{warns} WARN · {fails} FAIL"
            )
            for r in data['results']:
                if r['severity'] in ('FAIL', 'WARN'):
                    icon = '✗' if r['severity'] == 'FAIL' else '⚠'
                    lines.append(f"  {icon} {r['name']}: {r['message']}")
                    if r.get('hint'):
                        lines.append(f"     → {r['hint']}")
            lines.append("Run: python3 .memory/log.py health   for full report")
        # All green → stay quiet (version line only); a healthy install is silent.
    except (ValueError, KeyError):
        # rc=0 but the payload is not a parseable HealthReport. The self-test
        # ran but produced garbage — that is a broken self-test, NOT a healthy
        # install. SHOUT rather than silently emitting only the version line.
        _shout_broken("self-test returned non-JSON output")

if not lines:
    sys.exit(0)
banner = "\n".join(lines)
print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": banner}}))
PYEOF
