#!/usr/bin/env bash
# PreToolUse:Bash hook: keep work on the session branch — no worktrees, no
# divergent per-task branches.
#
# Nexus personas work DIRECTLY on the branch the session was created from (the
# current/active branch at session start, detected at runtime via
# `git branch --show-current`, which may be `main` or any other branch).
# Commit-on-the-session-branch IS the checkpoint — every commit is revertable, so
# there is nothing to isolate. Divergent history (worktrees, new per-task branches)
# orphans work — the exact failure mode this discipline forbids.
#
# Two tiers, by destructiveness:
#
#   1. `git worktree add`  → DENY (exit 2, typed WORKTREE_DENIED) by default. A
#      worktree creates a whole detached checkout whose commits never reach the
#      session branch unless someone remembers to merge + remove it — the
#      highest-risk orphan. Escape hatch: set NEXUS_ALLOW_WORKTREE=1 (the
#      "genuinely unavoidable" clause, which then DEMANDS an auto-merge-back-and-
#      remove rule). With the env set, the command is allowed but a LOUD
#      additionalContext warning still fires.
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
# Everything else (git status, git commit, git log, `git branch` with no new
# name, `git worktree list/remove/prune`) → silent pass.
#
# Detection runs on the parsed command string and is segment-aware (so the rule
# fires on chained / subshelled invocations too, e.g. `foo && git worktree add`).
#
# Output contract mirrors no-direct-push-to-session-branch.sh / socraticode-gate.sh.

set -euo pipefail

INPUT=$(cat)
CMD=$(printf '%s' "$INPUT" | jq -r '.tool_input.command // ""' 2>/dev/null || true)

# Nothing to evaluate — pass through.
if [ -z "$CMD" ]; then
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
new_branch = False
new_branch_has_bypass = False

for seg in split_segments(strip_heredocs(cmd)):
    toks = tokens_of(seg)
    if len(toks) < 2:
        continue
    if toks[0].rsplit("/", 1)[-1] != "git":
        continue
    sub = toks[1]
    rest = toks[2:]

    # git worktree add ...
    if sub == "worktree" and rest and rest[0] == "add":
        worktree_add = True
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

if worktree_add:
    print("WORKTREE_ADD")
elif new_branch:
    if new_branch_has_bypass:
        print("NEW_BRANCH|BYPASS")
    else:
        print("NEW_BRANCH")
else:
    print("NONE")
PY
)

# ── Act on the verdict ────────────────────────────────────────────────────────
case "$VERDICT" in
    WORKTREE_ADD)
        if [ "${NEXUS_ALLOW_WORKTREE:-}" = "1" ]; then
            # Escape hatch engaged — allow, but make it impossible to miss.
            MSG="[worktree-guard] WORKTREE ALLOWED via NEXUS_ALLOW_WORKTREE=1 — but Nexus work stays on the session branch. A worktree orphans every commit it holds unless you merge it back AND remove it. This is permitted ONLY with an automatic merge-back-and-remove rule: reconcile to the session branch and 'git worktree remove' the moment you are done."
            jq -n --arg msg "$MSG" '{
                hookSpecificOutput: {
                    hookEventName: "PreToolUse",
                    additionalContext: $msg
                }
            }'
            printf '%s\n' "$MSG" >&2
            exit 0
        fi
        # Default: hard deny.
        MSG="[worktree-guard] WORKTREE_DENIED — git worktree add is forbidden. Nexus personas work directly on the session branch and commit as checkpoints — every commit is revertable, so there is nothing to isolate. Worktrees ORPHAN work: their commits never reach the session branch unless someone remembers to merge and remove them, which is exactly the lost-work failure this discipline forbids. If isolation is genuinely unavoidable, re-run with NEXUS_ALLOW_WORKTREE=1 and you MUST auto-merge-back-and-remove the worktree on completion."
        jq -n --arg msg "$MSG" '{
            hookSpecificOutput: {
                hookEventName: "PreToolUse",
                permissionDecision: "deny",
                permissionDecisionReason: $msg
            }
        }'
        printf '%s\n' "$MSG" >&2
        exit 2
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
        exit 2
        ;;
    *)
        exit 0
        ;;
esac
