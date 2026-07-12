#!/usr/bin/env bash
# PreToolUse:Bash hook: keep work on the session branch — no unregistered
# worktrees, no divergent per-task branches.
#
# Nexus personas work DIRECTLY on the branch the session was created from (the
# current/active branch at session start, detected at runtime via
# `git branch --show-current`, which may be `main` or any other branch).
# Commit-on-the-session-branch IS the checkpoint — every commit is revertable, so
# there is nothing to isolate. Divergent history (worktrees, new per-task branches)
# orphans work — the exact failure mode this discipline forbids.
#
# Three tiers, by destructiveness:
#
#   1. `git worktree add`  → registry-ownership check. The target path
#      (resolved absolute) must have a LIVE (non-expired) record in
#      .memory/files/worktree_registry.json, written by nexus_register_worktree.
#      LIVE record → ALLOW (exit 0) with a LOUD additionalContext reminder that
#      teardown + merge-back is mandatory (Article XIII.c self-managed
#      lifecycle). No record, an EXPIRED record, or a missing/unreadable/corrupt
#      registry file → DENY (exit 2), fail-closed: ownership cannot be verified,
#      so the command is refused. The old NEXUS_ALLOW_WORKTREE=1 env
#      escape-hatch is RETIRED — it no longer has any effect; only a registered
#      path is honored (parity with the live nexus-installer twin).
#
#   2. `git checkout -b <new>` / `git switch -c <new>` / `git branch <new>` →
#      DENY (exit 2, typed NEW_BRANCH_DENIED) by default. Constitution Article
#      XIV says NO new per-task feature branches — enforcement follows the doc.
#      Escape hatch: append the literal trailing comment
#      '# BYPASS:USER-APPROVED-BRANCH' to the command (explicit user approval
#      only). With the token present the command is allowed (typed
#      NEW_BRANCH_BYPASS) but a LOUD additionalContext warning still fires
#      demanding merge-back + delete.
#
#   3. `git commit` → N71 Decision A companion check (twin of the live
#      nexus-installer hook), gated by the PRESENCE of
#      .claude/deploy-governance.enabled. Flag absent → byte-inert (identical
#      to pre-N71 silent pass). Flag present AND the commit runs inside a
#      registered self-modifying worktree AND the staged diff mixes a
#      flag-file change (.claude/*.enabled|.flag) with a hook-body change
#      (.claude/hooks/**/*.sh|*.py) → DENY (exit 2): wire (flag OFF) and
#      activate (flag ON) must be separate commits.
#
# Everything else (git status, git log, `git branch` with no new name, `git
# worktree list/remove/prune`, an ordinary git commit outside the Decision A
# conditions above) → silent pass.
#
# Detection runs on the parsed command string and is segment-aware (so the rule
# fires on chained / subshelled invocations too, e.g. `foo && git worktree add`).
#
# Output contract mirrors no-direct-push-to-session-branch.sh / socraticode-gate.sh.

set -euo pipefail

HOOKS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=gate-lib.sh
source "${HOOKS_DIR}/gate-lib.sh"
# shellcheck source=heartbeat-emitter.sh
# set -e (bash 3.2 on macOS) treats a failed `source` of a
# missing file as fatal even inside `|| { ... }` — guard with an
# explicit -f test instead so a missing heartbeat-emitter.sh never
# aborts the gate (best-effort telemetry must never break allow/deny).
if [ -f "${HOOKS_DIR}/heartbeat-emitter.sh" ]; then
    # shellcheck source=heartbeat-emitter.sh
    source "${HOOKS_DIR}/heartbeat-emitter.sh" 2>/dev/null || true
fi
# Belt-and-suspenders: even if the source succeeded but the file did not define
# both helpers (truncated/edited), guarantee they exist before first use.
command -v ms_now >/dev/null 2>&1 || ms_now() { python3 -c "import time; print(int(time.time()*1000))" 2>/dev/null || echo 0; }
command -v emit_heartbeat >/dev/null 2>&1 || emit_heartbeat() { :; }

_HB_START_MS=$(ms_now 2>/dev/null || echo 0)
_hb() {
  local decision="$1"
  local _elapsed=$(( $(ms_now 2>/dev/null || echo 0) - _HB_START_MS ))
  emit_heartbeat "worktree-guard" "PreToolUse" "$decision" "$_elapsed" 2>/dev/null || true
}

INPUT=$(cat)
CMD=$(printf '%s' "$INPUT" | jq -r '.tool_input.command // ""' 2>/dev/null || true)

# Nothing to evaluate — pass through.
if [ -z "$CMD" ]; then
    _hb allow
    exit 0
fi

# ── Classify the command: WORKTREE_ADD / NEW_BRANCH / NONE ────────────────────
VERDICT=$(python3 - <<'PY' "$CMD"
import re
import shlex
import sys

cmd = sys.argv[1] if len(sys.argv) > 1 else ""
if not cmd.strip():
    print("NONE")
    sys.exit(0)


def strip_heredocs(s: str) -> str:
    """Drop heredoc bodies so their contents aren't parsed as commands."""
    out, lines, i = [], s.split("\n"), 0
    while i < len(lines):
        line = lines[i]
        out.append(line)
        m = re.search(r"<<-?\s*([\"']?)([A-Za-z_][A-Za-z0-9_]*)\1", line)
        if m:
            delim, strip_tabs, j = m.group(2), "<<-" in line, i + 1
            while j < len(lines):
                test = lines[j].lstrip("\t") if strip_tabs else lines[j]
                if test == delim:
                    out.append(lines[j])
                    break
                j += 1
            i = j + 1
            continue
        i += 1
    return "\n".join(out)


def split_segments(s: str) -> list[str]:
    """Split on top-level ; | & && || newline, respecting quotes and parens."""
    segs, cur, i, n = [], [], 0, len(s)
    in_single = in_double = False
    paren = 0
    while i < n:
        ch = s[i]
        if ch == "\\" and i + 1 < n:
            cur.append(ch)
            cur.append(s[i + 1])
            i += 2
            continue
        if ch == "'" and not in_double:
            in_single = not in_single
            cur.append(ch)
            i += 1
            continue
        if ch == '"' and not in_single:
            in_double = not in_double
            cur.append(ch)
            i += 1
            continue
        if not in_single and not in_double:
            if ch == "(":
                paren += 1
                cur.append(ch)
                i += 1
                continue
            if ch == ")":
                paren = max(0, paren - 1)
                cur.append(ch)
                i += 1
                continue
            if paren == 0:
                two = s[i : i + 2]
                if two in ("&&", "||", ";;"):
                    segs.append("".join(cur))
                    cur = []
                    i += 2
                    continue
                if ch in (";", "|", "&", "\n", "(", ")"):
                    segs.append("".join(cur))
                    cur = []
                    i += 1
                    continue
        cur.append(ch)
        i += 1
    if cur:
        segs.append("".join(cur))
    return segs


WRAPPERS = {"rtk", "sudo", "env", "time", "nice", "ionice", "exec",
            "command", "builtin", "xargs"}


def tokens_of(segment: str) -> list[str]:
    seg = segment.strip().lstrip("(").strip()
    if not seg:
        return []
    try:
        toks = shlex.split(seg, comments=True, posix=True)
    except ValueError:
        # Unbalanced quotes — fall back to a permissive whitespace split so a
        # `git worktree add` buried in a malformed line still trips the guard.
        toks = seg.split()
    # Drop leading VAR=val assignments.
    idx = 0
    while idx < len(toks) and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", toks[idx]):
        idx += 1
    toks = toks[idx:]
    # Peel all leading wrappers (rtk git ..., sudo env git ..., etc.).
    while toks and toks[0].rsplit("/", 1)[-1] in WRAPPERS:
        toks = toks[1:]
        # env may carry its own VAR=val pairs before the real command.
        j = 0
        while j < len(toks) and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", toks[j]):
            j += 1
        toks = toks[j:]
    return toks


def segment_has_bypass(raw_seg: str) -> bool:
    """Return True only when the BYPASS token appears as a trailing shell
    comment (# ...) on THIS segment — not in a separate segment."""
    TOKEN = "BYPASS:USER-APPROVED-BRANCH"
    s, n = raw_seg, len(raw_seg)
    in_single = in_double = False
    i = 0
    while i < n:
        ch = s[i]
        if ch == "\\" and i + 1 < n:
            i += 2
            continue
        if ch == "'" and not in_double:
            in_single = not in_single
            i += 1
            continue
        if ch == '"' and not in_single:
            in_double = not in_double
            i += 1
            continue
        if not in_single and not in_double and ch == "#":
            comment = s[i + 1:].strip()
            return TOKEN in comment
        i += 1
    return False


def is_new_name(arg: str) -> bool:
    """A positional branch name — not a flag and not an option value."""
    return bool(arg) and not arg.startswith("-")


worktree_add = False
worktree_path = ""
new_branch = False
new_branch_has_bypass = False
commit_detected = False

for seg in split_segments(strip_heredocs(cmd)):
    toks = tokens_of(seg)
    if len(toks) < 2:
        continue
    if toks[0].rsplit("/", 1)[-1] != "git":
        continue
    sub = toks[1]
    rest = toks[2:]

    # git worktree add [<flags>] <path> [<branch>]
    if sub == "worktree" and rest and rest[0] == "add":
        worktree_add = True
        # Flags that consume a following value (skip the value token too).
        VALUE_FLAGS = {"-b", "-B", "--reason"}
        args = rest[1:]
        k = 0
        while k < len(args):
            a = args[k]
            if a in VALUE_FLAGS:
                k += 2
                continue
            if a.startswith("-"):
                k += 1
                continue
            # First non-flag positional after 'add' is the worktree path.
            worktree_path = a
            break
        continue

    # git checkout -b <new> / -B <new>
    if sub in ("checkout", "switch"):
        for k, a in enumerate(rest):
            if a in ("-b", "-B", "-c", "-C", "--create", "--orphan"):
                # The next non-empty token is the new branch name.
                if k + 1 < len(rest) and is_new_name(rest[k + 1]):
                    new_branch = True
                    if segment_has_bypass(seg):
                        new_branch_has_bypass = True
                break
        continue

    # git branch <new>  (creating a branch). Distinguish from listing/editing:
    # only flag when there is exactly a positional name and no listing/delete/
    # move flags that change the meaning.
    if sub == "branch":
        positionals = [a for a in rest if is_new_name(a)]
        destructive_or_list = any(
            a in ("-d", "-D", "--delete", "-m", "-M", "--move",
                  "-c", "-C", "--copy", "-l", "--list", "-a", "--all",
                  "-r", "--remotes", "--show-current", "--edit-description",
                  "--set-upstream-to", "-u", "--unset-upstream", "--contains",
                  "--merged", "--no-merged")
            or a.startswith("--set-upstream-to=")
            for a in rest
        )
        # `git branch <name>` or `git branch <name> <start-point>` with no
        # management flags ⇒ branch creation.
        if positionals and not destructive_or_list:
            new_branch = True
            if segment_has_bypass(seg):
                new_branch_has_bypass = True
        continue

    # git commit (any form) — flagged for the N71/Decision-A commit-cadence
    # companion check (case COMMIT below). Detection only; the flag-gated
    # enforcement itself lives in the bash case statement.
    if sub == "commit":
        commit_detected = True
        continue

if worktree_add:
    # Path travels after a literal tab so bash can split verdict from path
    # without worrying about spaces/special chars inside the path itself.
    print("WORKTREE_ADD\t" + worktree_path)
elif new_branch:
    if new_branch_has_bypass:
        print("NEW_BRANCH|BYPASS")
    else:
        print("NEW_BRANCH")
elif commit_detected:
    print("COMMIT")
else:
    print("NONE")
PY
)

# ── Act on the verdict ────────────────────────────────────────────────────────
# WORKTREE_ADD carries the target path after a literal tab (see the python
# emitter above) — split it off before the case switch so we can resolve and
# look it up in the registry. Every other verdict has no tab.
WT_PATH=""
case "$VERDICT" in
    WORKTREE_ADD*)
        WT_PATH="${VERDICT#WORKTREE_ADD}"
        WT_PATH="${WT_PATH#$'\t'}"
        VERDICT="WORKTREE_ADD"
        ;;
esac

case "$VERDICT" in
    WORKTREE_ADD)
        # ── Registry-ownership check (parity with the live nexus-installer
        # twin) ────────────────────────────────────────────────────────────
        REPO_ROOT="$(cd "${HOOKS_DIR}/../.." && pwd)"
        if [ -z "$WT_PATH" ]; then
            ABS_WT_PATH=""
        elif [ "${WT_PATH:0:1}" = "/" ]; then
            ABS_WT_PATH="$WT_PATH"
        else
            ABS_WT_PATH="$(cd "$REPO_ROOT" 2>/dev/null && python3 -c "import os,sys; print(os.path.normpath(os.path.join(os.getcwd(), sys.argv[1])))" "$WT_PATH" 2>/dev/null || true)"
        fi

        REGISTRY_PATH="${REPO_ROOT}/.memory/files/worktree_registry.json"

        REG_VERDICT=$(python3 - "$REGISTRY_PATH" "$ABS_WT_PATH" <<'PY'
import json
import sys
from datetime import datetime, timezone

registry_path = sys.argv[1] if len(sys.argv) > 1 else ""
target_path = sys.argv[2] if len(sys.argv) > 2 else ""

if not target_path:
    print("DENY|no worktree path could be parsed from the command")
    sys.exit(0)

try:
    with open(registry_path, "r") as f:
        raw = f.read()
except (FileNotFoundError, OSError):
    print("DENY|registry file missing at " + registry_path)
    sys.exit(0)

try:
    registry = json.loads(raw)
    if not isinstance(registry, dict):
        raise ValueError("registry root is not an object")
except (ValueError, TypeError) as exc:
    print("DENY|registry file is corrupt/unreadable JSON (" + str(exc) + ")")
    sys.exit(0)

entry = registry.get(target_path)
if not isinstance(entry, dict):
    print("DENY|no registry record for " + target_path)
    sys.exit(0)

created_at = entry.get("created_at")
ttl_seconds = entry.get("ttl_seconds", 14400)
try:
    ttl_seconds = float(ttl_seconds)
except (TypeError, ValueError):
    print("DENY|registry record for " + target_path + " has an invalid ttl_seconds")
    sys.exit(0)

if not created_at:
    print("DENY|registry record for " + target_path + " is missing created_at")
    sys.exit(0)

try:
    created = datetime.fromisoformat(created_at)
except (ValueError, TypeError):
    print("DENY|registry record for " + target_path + " has an unparseable created_at")
    sys.exit(0)

if created.tzinfo is None:
    created = created.replace(tzinfo=timezone.utc)

now = datetime.now(tz=timezone.utc)
age_seconds = (now - created).total_seconds()

if age_seconds >= ttl_seconds:
    print(
        "DENY|registry record for "
        + target_path
        + " expired ("
        + str(int(age_seconds))
        + "s old, ttl "
        + str(int(ttl_seconds))
        + "s)"
    )
    sys.exit(0)

owner = entry.get("owner_id", "<unknown>")
print("ALLOW|" + target_path + " is live-owned by " + str(owner))
PY
)

        REG_STATUS="${REG_VERDICT%%|*}"
        REG_DETAIL="${REG_VERDICT#*|}"

        if [ "$REG_STATUS" = "ALLOW" ]; then
            _hb allow
            gate_advise PreToolUse "WORKTREE/ADD-ALLOWED" "WORKTREE ALLOWED — registry ownership verified: ${REG_DETAIL}. Registered worktrees are the DEFAULT isolation for parallel multi-part legs (RDEC-018 Option 3) under the Article XIII.c self-managed lifecycle. Merge it back to the session branch AND run 'git worktree remove' the moment the workflow completes — no orphan may survive." --stderr
            exit 0
        fi
        # Default: fail-closed hard deny — ownership could not be verified.
        _hb deny
        gate_deny PreToolUse "WORKTREE/ADD-BLOCKED" "BLOCK — git worktree add has no live registry record (${REG_DETAIL}). Registered worktrees are the DEFAULT isolation for parallel multi-part legs (RDEC-018 Option 3, Article XIII.c self-managed lifecycle): a bare/unregistered git worktree add stays denied. Register the path first via nexus_register_worktree (owner_id + ttl_seconds), then retry. Fail-closed: an unreadable/missing/corrupt registry or an unregistered/expired path is always denied."
        ;;
    "NEW_BRANCH|BYPASS")
        # Explicit user-approved bypass — allow, but make it impossible to miss.
        MSG="[worktree-guard] NEW_BRANCH_BYPASS — BRANCH ALLOWED via '# BYPASS:USER-APPROVED-BRANCH', but Nexus work stays on the session branch (Constitution Article XIV): NO new per-task feature branches. This bypass exists solely for explicit user approval. A branch only protects work if it lands: merge it back to the session branch and delete it the moment you are done — no orphan branch may survive."
        jq -n --arg msg "$MSG" '{
            hookSpecificOutput: {
                hookEventName: "PreToolUse",
                additionalContext: $msg
            }
        }'
        printf '%s\n' "$MSG" >&2
        _hb allow
        exit 0
        ;;
    NEW_BRANCH)
        # Default: hard deny — Article XIV says NO new per-task feature branches.
        MSG="[worktree-guard] NEW_BRANCH_DENIED — branch creation is forbidden. Nexus personas work directly on the session branch (Constitution Article XIV): NO new per-task feature branches — commit on the session branch; every commit is revertable, so there is nothing to isolate. A divergent branch strands work off the session branch exactly like a worktree does. If the USER has EXPLICITLY approved a branch, re-run the command with the trailing comment '# BYPASS:USER-APPROVED-BRANCH' and you MUST merge the branch back to the session branch and delete it on completion."
        jq -n --arg msg "$MSG" '{
            hookSpecificOutput: {
                hookEventName: "PreToolUse",
                permissionDecision: "deny",
                permissionDecisionReason: $msg
            }
        }'
        printf '%s\n' "$MSG" >&2
        _hb deny
        exit 2
        ;;
    COMMIT)
        # ── N71 Decision A: commit-cadence companion check (twin of the live
        # nexus-installer hook). Gated by the presence of
        # .claude/deploy-governance.enabled — with the flag ABSENT this branch
        # is byte-inert: identical exit 0 / empty stdout to the pre-N71
        # default-allow path.
        REPO_ROOT="$(cd "${HOOKS_DIR}/../.." && pwd)"
        DEPLOY_GOV_FLAG="${REPO_ROOT}/.claude/deploy-governance.enabled"
        if [ ! -f "$DEPLOY_GOV_FLAG" ]; then
            _hb allow
            exit 0
        fi
        # Only fires INSIDE a registered self-modifying worktree (the flag's own
        # scope) — an ordinary session-branch commit is byte-inert even with
        # the flag on.
        REGISTRY_PATH="${REPO_ROOT}/.memory/files/worktree_registry.json"
        IS_REGISTERED_WT=$(python3 - "$REGISTRY_PATH" "$REPO_ROOT" <<'PY'
import json
import sys
from datetime import datetime, timezone

registry_path = sys.argv[1] if len(sys.argv) > 1 else ""
here = sys.argv[2] if len(sys.argv) > 2 else ""

try:
    with open(registry_path, "r") as f:
        registry = json.loads(f.read())
    if not isinstance(registry, dict):
        raise ValueError("registry root is not an object")
except (FileNotFoundError, OSError, ValueError, TypeError):
    print("NO")
    sys.exit(0)

entry = registry.get(here)
if not isinstance(entry, dict):
    print("NO")
    sys.exit(0)

created_at = entry.get("created_at")
ttl_seconds = entry.get("ttl_seconds", 14400)
try:
    ttl_seconds = float(ttl_seconds)
except (TypeError, ValueError):
    print("NO")
    sys.exit(0)
if not created_at:
    print("NO")
    sys.exit(0)
try:
    created = datetime.fromisoformat(created_at)
except (ValueError, TypeError):
    print("NO")
    sys.exit(0)
if created.tzinfo is None:
    created = created.replace(tzinfo=timezone.utc)
age = (datetime.now(tz=timezone.utc) - created).total_seconds()
print("YES" if age < ttl_seconds else "NO")
PY
)
        if [ "$IS_REGISTERED_WT" != "YES" ]; then
            _hb allow
            exit 0
        fi
        STAGED="$(git -C "$REPO_ROOT" diff --cached --name-only 2>/dev/null || true)"
        FLAG_HIT=$(printf '%s\n' "$STAGED" | grep -E '^\.claude/[^/]+\.(enabled|flag)$' || true)
        HOOK_HIT=$(printf '%s\n' "$STAGED" | grep -E '^\.claude/hooks/.*\.(sh|py)$' || true)
        if [ -n "$FLAG_HIT" ] && [ -n "$HOOK_HIT" ]; then
            _hb deny
            gate_deny PreToolUse "WORKTREE/COMMIT-CADENCE-BLOCKED" "BLOCK — this staged commit mixes a flag-file change (${FLAG_HIT}) with a hook-body change (${HOOK_HIT}) in the SAME commit, inside a registered self-modifying worktree. Split wire (flag OFF, byte-inert) and activate (flag ON) into separate commits."
        fi
        _hb allow
        exit 0
        ;;
    *)
        _hb allow
        exit 0
        ;;
esac
