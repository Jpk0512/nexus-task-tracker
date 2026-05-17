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
# Wired via .claude/settings.json hooks.SubagentStop matcher "" (all).

set -e

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

# If we can't identify the persona or read the message, exit silently.
if [ -z "$AGENT_PERSONA" ] || [ -z "$ASSISTANT_TEXT" ]; then
    exit 0
fi

DELIVERABLES="${DELIVERABLES_PATH:-$(pwd)/.claude/hooks/deliverables.json}"
if [ ! -f "$DELIVERABLES" ]; then exit 0; fi

# Capture the FULL input JSON for defensive tool-use scanning (must_not_run_bash).
export _RAW_INPUT="$INPUT"

# Run the validator in python for sane JSON + regex handling.
# Pass the assistant text via env var (stdin is consumed by the heredoc).
export _ASSISTANT_TEXT="$ASSISTANT_TEXT"
report=$(python3 - "$AGENT_PERSONA" "$DELIVERABLES" <<'PY' 2>&1
import sys, os, json, re, fnmatch

persona = sys.argv[1].lower()
deliv_path = sys.argv[2]
text = os.environ.get("_ASSISTANT_TEXT", "")
raw_input_str = os.environ.get("_RAW_INPUT", "")

with open(deliv_path) as f:
    config = json.load(f)

# Case-insensitive lookup; manifest keys are lowercase
expectations = None
for key, val in config.items():
    if key.lower() == persona:
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
PY
)

if [ -n "$report" ]; then
    issues=$(printf '%s' "$report" | jq -r '.issues | join("; ")' 2>/dev/null)
    persona_name=$(printf '%s' "$report" | jq -r '.persona' 2>/dev/null)
    reason="[SubagentStop verifier] $persona_name persona contract violation: $issues. Per CONTRACT.md + deliverables.json: emit a completion marker (## NEXUS:DONE|BLOCKED|NEEDS-DECISION|CHECKPOINT|REVISE), respect Output-Dir STRICT boundaries, and read-only personas must not modify files outside their allowed dump path."
    jq -n --arg r "$reason" '{
      decision: "block",
      reason: $r
    }'
fi

exit 0
