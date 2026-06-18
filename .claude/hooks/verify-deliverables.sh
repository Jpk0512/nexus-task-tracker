#!/bin/bash
# SubagentStop hook: validates that a sub-agent's final output conforms to its
# persona contract. Checks:
#   1. A completion marker (## NEXUS:DONE | BLOCKED | NEEDS-DECISION |
#      CHECKPOINT | REVISE) appears as an H2 in the response.
#   2. For implementers (Forge/Pipeline/Atlas/Quill): files_changed matches
#      expected_paths from .claude/hooks/deliverables.json.
#   3. For read-only personas (Scout/Lens): no files were modified.
#
# Emits decision:block if the contract is violated, with a clear diff so
# Nexus sees the discrepancy. Does NOT re-run verification commands (that's
# the orchestrator's job) — only checks the agent's output shape.
#
# Fail-CLOSED on internal error: this is an ENFORCEMENT gate. If the validator
# crashes (bad deliverables.json, python traceback, …) we must NOT silently
# allow the sub-agent to finish — a swallowed traceback could mask a real
# forbidden_paths / must_not_modify violation in the same run. On any internal
# error we emit a LOUD decision:block naming the failure so the orchestrator
# sees it, rather than failing open. Legitimate "nothing to check" exits
# (no persona, no message, persona has no manifest entry) still pass silently.
#
# Wired via .claude/settings.json hooks.SubagentStop matcher "" (all).

# NOTE: deliberately NOT `set -e`. With `set -e` a non-zero exit from the
# validator subprocess (an internal error we WANT to surface) aborts the
# script before the block jq runs — the violation is swallowed and the gate
# fails open. We handle the validator's exit status explicitly instead.

INPUT=$(cat)

# Best-effort: extract the assistant's final message text and any tool-result
# files-changed list. Hook input shape varies; check multiple paths.
ASSISTANT_TEXT=$(printf '%s' "$INPUT" | jq -r '
  .last_assistant_message //
  .response.text //
  .tool_response.text //
  ""
' 2>/dev/null)

AGENT_PERSONA=$(printf '%s' "$INPUT" | jq -r '
  .agent_persona //
  .tool_input.subagent_type //
  .subagent_type //
  ""
' 2>/dev/null)

# EXTRACT_OK canary (S1-22): a non-empty SubagentStop JSON payload from which
# NO assistant text is extractable means the harness schema may have drifted —
# a silent exit 0 here would disarm the whole SubagentStop wall invisibly.
# Warn LOUDLY (still exit 0: warn, not block), once per session via a flag file.
if [ -z "$ASSISTANT_TEXT" ]; then
    is_payload=$(printf '%s' "$INPUT" | jq -r 'if (type == "object") and (length > 0) then "yes" else "no" end' 2>/dev/null || echo "no")
    if [ "$is_payload" = "yes" ]; then
        sid=$(printf '%s' "$INPUT" | jq -r '.session_id // "unknown"' 2>/dev/null | tr -cd 'A-Za-z0-9_-' | cut -c1-64)
        flag="${TMPDIR:-/tmp}/.nexus-extract-miss-verify-deliverables-${sid:-unknown}"
        if [ ! -e "$flag" ]; then
            : > "$flag" 2>/dev/null || true
            jq -n '{hookSpecificOutput:{hookEventName:"SubagentStop",additionalContext:"[verify-deliverables] EXTRACT-MISS: SubagentStop payload had no extractable assistant text — possible harness schema drift"}}'
        fi
    fi
    exit 0
fi

# If we can't identify the persona, exit silently — there is genuinely nothing
# to enforce, so a silent pass is correct here.
if [ -z "$AGENT_PERSONA" ]; then
    exit 0
fi

# Resolve the deliverables manifest. The install-time token is overridable via
# env so the gate is testable and degrades loudly (not silently) if the token
# was never rendered.
INSTALL_ROOT="${_HOOK_INSTALL_ROOT:-/Users/john.keeney/nexus-task-tracker}"
DELIVERABLES="${_HOOK_DELIVERABLES:-$INSTALL_ROOT/.claude/hooks/deliverables.json}"
if [ ! -f "$DELIVERABLES" ]; then
    # Unrendered token or missing manifest: this is an ENFORCEMENT gate, so a
    # missing manifest is an internal error, not a clean pass. Surface it loudly
    # via a block rather than fail-open-silent. If the token is literally
    # unrendered we still want the orchestrator to notice the gate is inert.
    reason="[SubagentStop verifier] INTERNAL ERROR — deliverables manifest not found at '$DELIVERABLES' (install token /Users/john.keeney/nexus-task-tracker may be unrendered). The contract gate could not run; blocking fail-closed so a possible contract violation is NOT silently allowed. Fix the install-time substitution or set _HOOK_DELIVERABLES."
    jq -n --arg r "$reason" '{decision: "block", reason: $r}'
    exit 2
fi

# Capture the FULL input JSON for defensive tool-use scanning (must_not_run_bash).
export _RAW_INPUT="$INPUT"

# Pass the assistant text via env var (stdin is consumed by the heredoc).
export _ASSISTANT_TEXT="$ASSISTANT_TEXT"

# Run the validator in python for sane JSON + regex handling.
# CRITICAL: capture stdout (the report) and stderr (any traceback) SEPARATELY.
# Folding them with 2>&1 lets a traceback masquerade as the report, which the
# downstream jq then masks with `2>/dev/null` — exactly the swallow we must
# avoid. The validator exits 0 on success, 3 on an internal error.
report_err=$(mktemp)
report=$(python3 - "$AGENT_PERSONA" "$DELIVERABLES" 2>"$report_err" <<'PY'
import sys, os, json, re, fnmatch

try:
    persona = sys.argv[1].lower()
    deliv_path = sys.argv[2]
    text = os.environ.get("_ASSISTANT_TEXT", "")
    raw_input_str = os.environ.get("_RAW_INPUT", "")

    with open(deliv_path) as f:
        config = json.load(f)

    # Case-insensitive lookup; manifest keys are lowercase.
    # First try exact match; then retry with a base-name fallback so that
    # forge-ui/forge-wire/forge-*-pro resolve to "forge", pipeline-data/
    # pipeline-async/pipeline-*-pro resolve to "pipeline", and quill-ts/
    # quill-py resolve to "quill". scout/hermes/atlas/lens/palette/lens-fast
    # have explicit keys and resolve by exact match.
    def _base_name(p):
        if p.startswith("forge-"):
            return "forge"
        if p.startswith("pipeline-"):
            return "pipeline"
        if p.startswith("quill-"):
            return "quill"
        return None

    expectations = None
    for key, val in config.items():
        if key.lower() == persona:
            expectations = val
            break
    if not expectations:
        base = _base_name(persona)
        if base is not None:
            for key, val in config.items():
                if key.lower() == base:
                    expectations = val
                    break
    if not expectations:
        sys.exit(0)

    issues = []

    # 1. Required completion marker (H2)
    required_markers = expectations.get("required_markers", [])
    if required_markers:
        found = None
        for m in required_markers:
            pat = r"(?m)^" + re.escape(m) + r"\b"
            if re.search(pat, text):
                found = m
                break
        if not found:
            issues.append(
                f"Missing completion marker. Expected one of: {', '.join(required_markers)} as H2."
            )

    # 2. Parse files_changed from the agent's JSON output (per CONTRACT.md schema).
    # Look for `"files_changed": [...]` inside any fenced JSON block in the response.
    files_changed: list[str] = []
    for block in re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL):
        try:
            obj = json.loads(block)
        except json.JSONDecodeError:
            continue
        fc = obj.get("files_changed")
        if isinstance(fc, list) and all(isinstance(x, str) for x in fc):
            files_changed = fc
            break

    def _matches_any(path: str, globs: list[str]) -> bool:
        for g in globs:
            # Normalize: fnmatch doesn't handle ** the same as shells. Translate
            # **/foo  -> match any depth; foo/** -> match anything under foo.
            if "**" in g:
                # ** -> any number of path segments
                pattern = g.replace("**/", "*/").replace("/**", "/*")
                # Also try a literal-component match: foo/**/bar -> foo/*/bar OR
                # use a startswith check for foo/** segments.
                if fnmatch.fnmatch(path, g) or fnmatch.fnmatch(path, pattern):
                    return True
                # Final attempt: any segment of the path matches the glob
                if g.endswith("/**"):
                    root = g[:-3].rstrip("/")
                    if path == root or path.startswith(root + "/"):
                        return True
                if g.startswith("**/"):
                    tail = g[3:]
                    if fnmatch.fnmatch(path, tail) or fnmatch.fnmatch(
                        os.path.basename(path), tail
                    ):
                        return True
            else:
                if fnmatch.fnmatch(path, g):
                    return True
        return False

    # 3. forbidden_paths: any files_changed entry hitting a forbidden glob = MAJOR
    forbidden = expectations.get("forbidden_paths", [])
    if forbidden and files_changed:
        hits = [p for p in files_changed if _matches_any(p, forbidden)]
        if hits:
            issues.append(
                f"forbidden_paths violation. Files written outside permitted scope: "
                f"{', '.join(hits[:10])}. Forbidden globs: {', '.join(forbidden)}."
            )

    # 4. must_not_modify: Scout / Lens declare a whole-tree ban. Any non-empty
    #    files_changed (excluding the file-dump paths under .memory/) = MAJOR.
    must_not_modify = expectations.get("must_not_modify", [])
    if must_not_modify and files_changed:
        # Allow Scout/Lens to dump reports under .memory/<scout|lens>-reports/
        allowed_dump_re = re.compile(r"^\.?\/?\.memory/(scout|lens)-reports/")
        illegal = [p for p in files_changed if not allowed_dump_re.search(p)]
        if illegal:
            issues.append(
                f"must_not_modify violation ({persona} is read-only): "
                f"{', '.join(illegal[:10])}. Only .memory/{persona}-reports/ dumps are allowed."
            )

    # 5. must_not_run_bash: Atlas declares no Bash. Defensive scan over the entire
    #    SubagentStop input JSON for any "tool_name": "Bash" / "name": "Bash" /
    #    "type": "tool_use", "name": "Bash" pattern, regardless of where the
    #    transcript array lives in the payload.
    if expectations.get("must_not_run_bash") and raw_input_str:
        # Match any JSON-shaped Bash invocation. Conservative: looks for the
        # canonical Anthropic tool-use pattern, OR a bare "Bash" tool_name field.
        bash_pat = re.compile(
            r'("tool_name"\s*:\s*"Bash")'              # Claude Code hook event
            r'|("name"\s*:\s*"Bash"[^}]*"input"\s*:)'  # tool_use block
            r'|("tool"\s*:\s*"Bash"\b)',               # alternate shape
            re.DOTALL,
        )
        if bash_pat.search(raw_input_str):
            issues.append(
                f"must_not_run_bash violation: {persona} has disallowedTools: Bash by "
                "deliverables.json but the SubagentStop transcript contains a Bash "
                "tool invocation. Design-only personas may not execute shell commands; "
                "delegate execution to Pipeline."
            )

    # 6. required_verification: every named command's signature must appear in the
    #    agent's verification_result block (parsed from the same JSON we used for
    #    files_changed; falls back to scanning the whole assistant text).
    required_verification = expectations.get("required_verification", [])
    if required_verification:
        verification_text = ""
        for block in re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL):
            try:
                obj = json.loads(block)
            except json.JSONDecodeError:
                continue
            vr = obj.get("verification_result")
            if isinstance(vr, str):
                verification_text = vr
                break
            if isinstance(vr, dict):
                verification_text = json.dumps(vr)
                break
        haystack = verification_text or text
        missing_cmds = []
        for cmd in required_verification:
            # Allow any prefix-match of the command's first 2 tokens
            head = " ".join(cmd.split()[:2])
            pat = re.compile(re.escape(head), re.IGNORECASE)
            if not pat.search(haystack):
                missing_cmds.append(cmd)
        if missing_cmds:
            issues.append(
                f"required_verification missing: {persona} brief required "
                f"[{', '.join(required_verification)}] but the verification_result "
                f"does not show: [{', '.join(missing_cmds)}]. Run each command and "
                "capture its verbatim output in verification_result before ## NEXUS:DONE."
            )

    if issues:
        print(json.dumps({
            "persona": persona,
            "issues": issues,
            "files_changed_seen": files_changed,
        }))
except SystemExit:
    raise
except BaseException as exc:
    # Any internal error MUST surface, never be swallowed. Emit a machine-readable
    # error report on stdout and exit 3 so the shell can fail-closed (block).
    import traceback
    print(json.dumps({
        "internal_error": f"{type(exc).__name__}: {exc}",
        "traceback": traceback.format_exc(),
    }))
    sys.exit(3)
PY
)
py_status=$?
err_text=$(cat "$report_err")
rm -f "$report_err"

# Internal validator error: fail CLOSED, loud. Never swallow a traceback into a
# silent allow — a crash here could be hiding a real contract violation.
if [ "$py_status" -ne 0 ]; then
    detail=$(printf '%s' "$report" | jq -r '.internal_error // empty' 2>/dev/null)
    if [ -z "$detail" ]; then
        # report wasn't our structured error JSON (e.g. the python interpreter
        # itself died before our handler). Fall back to the captured stderr.
        detail=$(printf '%s' "$err_text" | tail -n 3 | tr '\n' ' ')
    fi
    [ -z "$detail" ] && detail="validator exited $py_status with no diagnostic"
    reason="[SubagentStop verifier] INTERNAL ERROR — the contract validator crashed ($detail). Blocking fail-closed: an internal failure must NOT silently allow a sub-agent to finish, because the crash may be masking a real forbidden_paths / must_not_modify / completion-marker violation. Re-run after fixing the validator or deliverables.json."
    # LOUD: echo to stderr too so it shows even if the decision is not surfaced.
    printf '%s\n' "$reason" >&2
    jq -n --arg r "$reason" '{decision: "block", reason: $r}'
    exit 2
fi

if [ -n "$report" ]; then
    issues=$(printf '%s' "$report" | jq -r '.issues | join("; ")' 2>/dev/null)
    persona_name=$(printf '%s' "$report" | jq -r '.persona' 2>/dev/null)
    # Defensive: if the report parsed but yielded no issues string, do not emit
    # a malformed block — treat as a pass.
    if [ -n "$issues" ] && [ "$issues" != "null" ]; then
        reason="[SubagentStop verifier] $persona_name persona contract violation: $issues. Per CONTRACT.md + deliverables.json: emit a completion marker (## NEXUS:DONE|BLOCKED|NEEDS-DECISION|CHECKPOINT|REVISE), respect Output-Dir STRICT boundaries, and read-only personas must not modify files outside their allowed dump path."
        jq -n --arg r "$reason" '{
          decision: "block",
          reason: $r
        }'
    fi
fi

exit 0
