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

REPO = os.environ.get("REPO_ROOT", os.getcwd())
LOG_PY = os.environ.get("LOG_PY", os.path.join(REPO, ".memory", "log.py"))


def main() -> int:
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
