#!/usr/bin/env python3
# SubagentStop hook: captures a subagent's full response, persists it to
# .memory/subagent-returns/, and writes a ≤500-char summary to the notepad.
# Skips responses < 1K approx tokens. Non-blocking — never exits non-zero.
#
# Wired via .claude/settings.json hooks.SubagentStop (runs after other hooks).

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

# Install-time substitution renders /Users/john.keeney/nexus-task-tracker. Tests (and a runtime
# sanity check) can override via the _HOOK_INSTALL_ROOT env var. KEEP the literal
# /Users/john.keeney/nexus-task-tracker as the default so render_template still substitutes it.
REPO = os.environ.get("_HOOK_INSTALL_ROOT", "/Users/john.keeney/nexus-task-tracker")


def _emit_unrendered_warning() -> None:
    """The install-time /Users/john.keeney/nexus-task-tracker token was never rendered. This hook
    would otherwise silently no-op (REPO points at a literal-token path that
    does not exist), so a sub-agent return would never be persisted. Fail SAFE
    (do not block the return) but LOUD: emit a nested additionalContext warning
    naming the unrendered token so the orchestrator notices the hook is inert."""
    ctx = (
        "[return-summarizer] WARNING — the install-time /Users/john.keeney/nexus-task-tracker token was "
        "never rendered, so this SubagentStop hook cannot locate .memory/log.py and "
        "is silently NOT persisting sub-agent returns. Re-run the Nexus install/render "
        "step (or set _HOOK_INSTALL_ROOT) so returns are captured again."
    )
    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": "SubagentStop",
                "additionalContext": ctx,
            }
        },
        sys.stdout,
    )
    print(ctx, file=sys.stderr)


def main() -> int:
    # Unrendered install token: fail SAFE + LOUD instead of silent no-op.
    if REPO.startswith("__") and REPO.endswith("__"):
        _emit_unrendered_warning()
        return 0

    LOG_PY = f"{REPO}/.memory/log.py"

    raw = sys.stdin.read()
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return 0

    # Extract assistant text — harness passes various shapes.
    assistant_text: str = (
        payload.get("last_assistant_message")
        or payload.get("response", {}).get("text")
        or payload.get("tool_response", {}).get("text")
        or ""
    )
    if not assistant_text:
        return 0

    agent_persona: str = (
        payload.get("agent_persona")
        or payload.get("tool_input", {}).get("subagent_type")
        or payload.get("subagent_type")
        or "unknown"
    ).lower()

    # Write response to a temp file so log.py can read it without stdin clash.
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False, prefix="subagent-return-"
    ) as f:
        f.write(assistant_text)
        tmp_path = f.name

    try:
        result = subprocess.run(
            [
                sys.executable,
                LOG_PY,
                "subagent-return",
                "record",
                "--agent", agent_persona,
                "--full-response-file", tmp_path,
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode != 0 and result.stderr:
            # Log to stderr so the harness can surface it if needed, but
            # never block the subagent return.
            print(f"[return-summarizer] warning: {result.stderr.strip()}", file=sys.stderr)
    except (subprocess.TimeoutExpired, OSError):
        pass
    finally:
        try:
            Path(tmp_path).unlink(missing_ok=True)
        except OSError:
            pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
