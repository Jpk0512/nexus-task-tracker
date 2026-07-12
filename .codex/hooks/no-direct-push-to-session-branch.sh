#!/usr/bin/env bash
# PreToolUse:Bash hook: gate WHO may push to the session branch (not WHETHER work
# uses it).
#
# Nexus personas work DIRECTLY on the branch the session was created from — the
# current/active branch at session start, detected at runtime via
# `git branch --show-current`. That branch MAY be `main` OR any other branch (some
# projects are worked off a non-main branch); the working branch is DYNAMIC, never
# hardcoded. Commit-on-the-session-branch IS the checkpoint — every commit is
# revertable, so there are NO per-task feature branches, NO worktrees, NO
# pull-request-for-merge ceremony. The push target is therefore not in question;
# the only control is the IDENTITY of the pusher. Only the nexus-orchestrator or
# the user may push the session branch; a sub-agent must NOT push on its own (it
# COMMITS on the session branch and lets the orchestrator/user push), unless the
# user has explicitly authorized THIS push via the bypass token.
#
# Detection: the session branch is detected dynamically (`git branch
# --show-current`), with a fallback to the branch named in the push command's
# target argument. Any `git push` (incl. `--force` and `origin <branch>` variants,
# and chained `git checkout <branch> && git push`) that targets the session branch
# triggers evaluation.
#
# Allow conditions (any one is sufficient):
#   - CLAUDE_AGENT_TYPE is "nexus-orchestrator" or unset/empty (user session)
#   - Command contains the bypass token: # BYPASS:USER-APPROVED-PUSH
#
# Exit code 2 on block (hard deny, typed PUSH_SESSION_BRANCH_DENIED). Exit 0 on
# allow. Output contract mirrors worktree-guard.sh / socraticode-gate.sh
# (nested hookSpecificOutput with permissionDecision: deny).

set -euo pipefail

INPUT=$(cat)
CMD=$(printf '%s' "$INPUT" | jq -r '.tool_input.command // ""' 2>/dev/null || true)
AGENT_TYPE="${CLAUDE_AGENT_TYPE:-}"

# Nothing to evaluate — pass through.
if [ -z "$CMD" ]; then
    exit 0
fi

# ── Step 0: detect the session branch dynamically ─────────────────────────────
# The branch the session is on. If it cannot be determined (detached HEAD, not a
# repo), SESSION_BRANCH is empty and we fall back to the push-target argument.
SESSION_BRANCH=$(git branch --show-current 2>/dev/null || true)

# ── Step 1: does the command push to the session branch? ──────────────────────
# Build the push-detection patterns from $SESSION_BRANCH (NOT a literal `main`).
# If the session branch is unknown, fall back to detecting the target branch from
# the push command itself (`git push <remote> <branch>` / `git push --force ...`).
is_push_to_branch=$(python3 - "$CMD" "$SESSION_BRANCH" <<'PY'
import re
import sys

cmd = sys.argv[1] if len(sys.argv) > 1 else ""
session_branch = sys.argv[2] if len(sys.argv) > 2 else ""

if not cmd.strip():
    print("0")
    sys.exit(0)

# `git` with optional global flags before the subcommand: -C <path>, -c k=v,
# --git-dir[=| ]<d>, --work-tree[=| ]<d>. Keeps `git -C /repo push ...` and
# `git --git-dir=/repo/.git push ...` inside the detection net (S2-11).
GIT = r"\bgit(?:\s+(?:-C\s+\S+|-c\s+\S+|--git-dir(?:=\S+|\s+\S+)|--work-tree(?:=\S+|\s+\S+)))*"
PUSH = GIT + r"\s+push\b"


def has_push(s: str) -> bool:
    return bool(re.search(PUSH, s))


# When we know the session branch, gate any push that names it (or any chained
# `git checkout <branch> && git push`). HEAD / @ name the current branch — i.e.
# the session branch — so `git push origin HEAD`, `git push origin @`, and
# `refs/heads/<branch>` refspec aliases are gated too (S2-11).
if session_branch:
    b = re.escape(session_branch)
    patterns = [
        PUSH + rf".*\b{b}\b",
        PUSH + rf".*--force\b.*\b{b}\b",
        PUSH + rf".*\borigin\s+{b}\b",
        PUSH + r".*\bHEAD\b",                       # HEAD == the current (session) branch
        PUSH + r".*\s@(?:\{[^}]*\})?(?::\S*)?(?=\s|$)",  # @ is git's HEAD alias
        PUSH + rf".*\brefs/heads/{b}\b",            # explicit refspec alias to the session branch
        GIT + rf"\s+checkout\s+{b}\b.*&&.*\bgit\s+push\b",
    ]
    for pat in patterns:
        if re.search(pat, cmd):
            print("1")
            sys.exit(0)
    # A bare `git push` with no explicit branch still targets the current branch
    # (the session branch) under the default push config — gate it too.
    if re.search(PUSH + r"(?!\s+\S+\s+\S)", cmd) and "--help" not in cmd:
        # No explicit refspec means "push the current (session) branch".
        if not re.search(PUSH + r".*\b\S+\s+\S+", cmd):
            print("1")
            sys.exit(0)
    print("0")
    sys.exit(0)

# Fallback: session branch unknown. Gate ANY push (we cannot prove it does not
# target the session branch, so the identity rule still applies).
if has_push(cmd):
    print("1")
    sys.exit(0)

print("0")
PY
)

if [ "$is_push_to_branch" != "1" ]; then
    exit 0
fi

# ── Step 2: bypass token present as a trailing comment on the push segment? ────
# Raw grep over the whole command allows evasion via `echo '# BYPASS:...' && git push`.
# Instead, require the token to be a trailing shell comment on the SAME segment as
# the push command (segment-aware check).
BYPASS_ON_PUSH=$(python3 - "$CMD" <<'PY'
import re, shlex, sys

cmd = sys.argv[1] if len(sys.argv) > 1 else ""
if not cmd.strip():
    print("0"); sys.exit(0)

TOKEN = "BYPASS:USER-APPROVED-PUSH"

GIT_PUSH = re.compile(r'\bgit(?:\s+(?:-C\s+\S+|-c\s+\S+|--git-dir(?:=\S+|\s+\S+)|--work-tree(?:=\S+|\s+\S+)))*\s+push\b')


def split_segments(s):
    segs, cur = [], []
    i, n = 0, len(s)
    in_single = in_double = False
    paren = 0
    while i < n:
        ch = s[i]
        if ch == "\\" and i + 1 < n:
            cur.append(ch); cur.append(s[i + 1]); i += 2; continue
        if ch == "'" and not in_double:
            in_single = not in_single; cur.append(ch); i += 1; continue
        if ch == '"' and not in_single:
            in_double = not in_double; cur.append(ch); i += 1; continue
        if not in_single and not in_double:
            if ch == "(":
                paren += 1; cur.append(ch); i += 1; continue
            if ch == ")":
                paren = max(0, paren - 1); cur.append(ch); i += 1; continue
            if paren == 0:
                two = s[i:i + 2]
                if two in ("&&", "||", ";;"):
                    segs.append("".join(cur)); cur = []; i += 2; continue
                if ch in (";", "|", "&", "\n"):
                    segs.append("".join(cur)); cur = []; i += 1; continue
        cur.append(ch); i += 1
    if cur:
        segs.append("".join(cur))
    return segs


def segment_has_bypass_comment(seg):
    """True when the BYPASS token appears as a trailing # comment in this segment."""
    s, n = seg, len(seg)
    in_single = in_double = False
    i = 0
    while i < n:
        ch = s[i]
        if ch == "\\" and i + 1 < n:
            i += 2; continue
        if ch == "'" and not in_double:
            in_single = not in_single; i += 1; continue
        if ch == '"' and not in_single:
            in_double = not in_double; i += 1; continue
        if not in_single and not in_double and ch == "#":
            return TOKEN in s[i + 1:]
        i += 1
    return False


for seg in split_segments(cmd):
    if GIT_PUSH.search(seg) and segment_has_bypass_comment(seg):
        print("1"); sys.exit(0)

print("0")
PY
)
if [ "$BYPASS_ON_PUSH" = "1" ]; then
    exit 0
fi

# ── Step 3: is caller nexus-orchestrator or the user (unset)? ─────────────────
if [ -z "$AGENT_TYPE" ] || [ "$AGENT_TYPE" = "nexus-orchestrator" ]; then
    exit 0
fi

# ── Block ─────────────────────────────────────────────────────────────────────
BRANCH_LABEL="${SESSION_BRANCH:-the session branch}"
MSG="[no-direct-push-to-session-branch] PUSH_SESSION_BRANCH_DENIED — a sub-agent must NOT push the session branch ('${BRANCH_LABEL}') on its own. Nexus personas work directly on the branch the session started on; commit-on-the-session-branch is the checkpoint and only the nexus-orchestrator (or the user) pushes it. COMMIT your work on the session branch and let the orchestrator (or the user) push. If the user has explicitly authorized THIS push, append the token '# BYPASS:USER-APPROVED-PUSH' to the command and re-run."

jq -n --arg msg "$MSG" '{
    hookSpecificOutput: {
        hookEventName: "PreToolUse",
        permissionDecision: "deny",
        permissionDecisionReason: $msg
    }
}' >&1

printf '%s\n' "$MSG" >&2

exit 2
