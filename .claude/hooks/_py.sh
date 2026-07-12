#!/bin/sh
# _py.sh — Nexus hook Python resolver shim
#
# Guarantees every hook runs under Python >=3.11 by finding the best available
# interpreter on this machine. exec-replaces itself with the chosen interpreter
# so exit code, stdout, and stderr pass through unchanged (broker-gate.py
# depends on this — it must not be wrapped in a subshell).
#
# Resolution order:
#   1. $NEXUS_HOOK_PYTHON  — CI / developer override (must be >=3.11)
#   2. python3.13 / python3.12 / python3.11 on PATH
#   3. Absolute Homebrew paths (Apple-Silicon /opt/homebrew then Intel /usr/local),
#      both versioned binary and opt/ formula link, for 3.13/3.12/3.11
#   4. Bare python3 on PATH, if it reports >=3.11
#   5. Repo-local broker venv (path derived from this script's location, NOT cwd)
#   6. FAIL LOUD — one stderr line naming the fix, exit 1
#
# Test-isolation seam (mirrors _HOOK_REPO_ROOT in broker-gate.py /
# _HOOK_SKILL_MAP_PATH in skills-required-guard.sh):
#   _NEXUS_HOOK_SKIP_DISCOVERY=1  — when NON-EMPTY, steps 2-5 (INCLUDING the
#       resolution cache below) are skipped; only step 1 ($NEXUS_HOOK_PYTHON)
#       is consulted. If $NEXUS_HOOK_PYTHON is unset, empty, or resolves to a
#       Python <3.11, execution falls straight to step 6 (fail loud). NEVER
#       set this in production; it exists solely so test suites can prove
#       rc=1 + stderr on a Homebrew dev box where steps 2-5 would otherwise
#       always resolve a >=3.11 interpreter.
#
# Resolution cache (R3-T10 / N15 — SPEED): full candidate discovery spawns a
# real python process per candidate tried (`_version_ok` execs the binary) —
# measured ~90ms wall-clock on a typical dev box before landing on a winner,
# paid on EVERY hook invocation that routes through this shim. The winning
# interpreter for a given machine's toolchain does not change within a
# session (or usually ever, short of an uninstall), so once resolved it is
# cached to a file and subsequent calls trust it on a bare `-x` file test
# (no subprocess) instead of re-running the whole candidate walk. A cache
# whose target has been removed/moved fails the `-x` test and falls straight
# through to full discovery, which repopulates the cache — self-healing,
# never a hard failure mode. $NEXUS_HOOK_PYTHON (step 1, above) is always
# re-checked fresh on every call and is NEVER cached — an explicit override
# must win immediately, every time.
_PY_CACHE_FILE="${NEXUS_PY_RESOLVED_CACHE:-${TMPDIR:-/tmp}/.nexus_py_resolved_interpreter}"

set -e

# Per-candidate version check: succeeds if candidate is >=3.11
_version_ok() {
    "$1" -c 'import sys;sys.exit(0 if sys.version_info[:2]>=(3,11) else 1)' 2>/dev/null
}

# --- 1. Explicit CI/developer override ---
if [ -n "${NEXUS_HOOK_PYTHON:-}" ]; then
    if [ -x "$NEXUS_HOOK_PYTHON" ] && _version_ok "$NEXUS_HOOK_PYTHON"; then
        exec "$NEXUS_HOOK_PYTHON" "$@"
    fi
    # Set but invalid — fall through. If _NEXUS_HOOK_SKIP_DISCOVERY is set,
    # execution proceeds directly to step 6 (fail loud). Otherwise, steps 2-5
    # may still resolve a Python >=3.11 before the loud error fires.
fi

# Steps 2-5 may be skipped for test isolation (see header comment).
if [ -z "${_NEXUS_HOOK_SKIP_DISCOVERY:-}" ]; then

    # --- Cache fast-path: trust a previously-resolved interpreter outright. ---
    if [ -f "$_PY_CACHE_FILE" ]; then
        _cached="$(cat "$_PY_CACHE_FILE" 2>/dev/null || true)"
        if [ -n "$_cached" ] && [ -x "$_cached" ]; then
            exec "$_cached" "$@"
        fi
    fi

    # --- 2. Versioned binaries on PATH ---
    for _v in python3.13 python3.12 python3.11; do
        if command -v "$_v" >/dev/null 2>&1 && _version_ok "$_v"; then
            _resolved="$(command -v "$_v")"
            printf '%s' "$_resolved" > "$_PY_CACHE_FILE" 2>/dev/null || true
            exec "$_resolved" "$@"
        fi
    done

    # --- 3. Absolute Homebrew paths (Apple-Silicon then Intel) ---
    for _base in /opt/homebrew/bin /usr/local/bin; do
        for _v in python3.13 python3.12 python3.11; do
            _c="${_base}/${_v}"
            if [ -x "$_c" ] && _version_ok "$_c"; then
                printf '%s' "$_c" > "$_PY_CACHE_FILE" 2>/dev/null || true
                exec "$_c" "$@"
            fi
        done
    done
    for _arch in /opt/homebrew /usr/local; do
        for _ver in 3.13 3.12 3.11; do
            _c="${_arch}/opt/python@${_ver}/bin/python3"
            if [ -x "$_c" ] && _version_ok "$_c"; then
                printf '%s' "$_c" > "$_PY_CACHE_FILE" 2>/dev/null || true
                exec "$_c" "$@"
            fi
        done
    done

    # --- 4. Bare python3 on PATH if >=3.11 ---
    if command -v python3 >/dev/null 2>&1 && _version_ok python3; then
        _resolved="$(command -v python3)"
        printf '%s' "$_resolved" > "$_PY_CACHE_FILE" 2>/dev/null || true
        exec "$_resolved" "$@"
    fi

    # --- 5. Repo-local broker venv (derived from this script's own path, not cwd) ---
    _script_dir="$(cd "$(dirname "$0")" && pwd)"
    _repo_root="$(cd "${_script_dir}/../.." && pwd)"
    _venv_py="${_repo_root}/nexus-broker/.venv/bin/python3"
    if [ -x "$_venv_py" ] && _version_ok "$_venv_py"; then
        printf '%s' "$_venv_py" > "$_PY_CACHE_FILE" 2>/dev/null || true
        exec "$_venv_py" "$@"
    fi

fi

# --- 6. No Python >=3.11 found — fail loud ---
echo "[NEXUS] ERROR: No Python >=3.11 found. Hooks require modern Python." >&2
echo "[NEXUS] Fix: brew install python@3.12  — or set NEXUS_HOOK_PYTHON=/path/to/python3.12" >&2
exit 1
