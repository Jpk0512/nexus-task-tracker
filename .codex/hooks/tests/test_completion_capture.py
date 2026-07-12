"""OPT-040 A1/A3 — tests for completion-capture.py (completion-event ledger).

The hook appends one JSONL row per SubagentStop to
.memory/files/completion_events.jsonl.  It is FAIL-SOFT: always exits 0.

Tests use the subprocess pattern matching sibling hooks (test_return_validator,
test_dispatch_announce):  JSON on stdin, assert exit code + written row.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

HOOKS_DIR = Path(__file__).parent.parent
HOOK = HOOKS_DIR / "completion-capture.py"


# ── helpers ───────────────────────────────────────────────────────────────────

def _run_hook(
    payload: dict,
    files_dir: Path | None = None,
    *,
    extra_env: dict[str, str] | None = None,
) -> tuple[int, str, str]:
    """Run completion-capture.py with a JSON payload on stdin.

    If *files_dir* is supplied it is injected via _HOOK_MEMORY_FILES_DIR so the
    hook writes to a temp location without touching the live ledger.
    """
    env = os.environ.copy()
    if files_dir is not None:
        env["_HOOK_MEMORY_FILES_DIR"] = str(files_dir)
    if extra_env:
        env.update(extra_env)

    result = subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=10,
        env=env,
    )
    return result.returncode, result.stdout, result.stderr


def _read_ledger(files_dir: Path) -> list[dict]:
    ledger = files_dir / "completion_events.jsonl"
    if not ledger.exists():
        return []
    rows = []
    for line in ledger.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        rows.append(json.loads(line))
    return rows


def _make_done_return(persona: str = "hermes", session_id: str = "S-test-001") -> dict:
    """Minimal SubagentStop payload for a DONE return with files_changed."""
    body = json.dumps({
        "status": "complete",
        "completion_marker": "## NEXUS:DONE",
        "files_changed": ["src/foo.py", "tests/test_foo.py"],
        "verification_result": "2 passed, 0 failed",
        "acceptance_met": [{"criterion": "tests pass", "met": True, "evidence": "pytest"}],
    })
    return {
        "session_id": session_id,
        "agent_persona": persona,
        "last_assistant_message": (
            "Work complete.\n\n"
            "```json\n" + body + "\n```\n\n"
            "## NEXUS:DONE\n"
        ),
    }


# ── happy path: appends a well-formed row ─────────────────────────────────────

def test_happy_path_appends_one_row(tmp_path: Path) -> None:
    """A valid SubagentStop payload appends exactly one JSONL row."""
    rc, _, _ = _run_hook(_make_done_return(), files_dir=tmp_path)
    assert rc == 0, "hook must exit 0 (fail-soft)"

    rows = _read_ledger(tmp_path)
    assert len(rows) == 1, f"Expected 1 row, got {len(rows)}: {rows}"


def test_row_fields_present(tmp_path: Path) -> None:
    """The appended row has all required fields."""
    _run_hook(_make_done_return(persona="hermes", session_id="S-abc"), files_dir=tmp_path)
    row = _read_ledger(tmp_path)[0]

    assert set(row.keys()) >= {"ts", "session_id", "persona", "marker",
                               "files_changed_count", "prompt_hash"}, (
        f"Missing fields in row: {row}"
    )


def test_marker_done_extracted(tmp_path: Path) -> None:
    """marker field is 'DONE' for a ## NEXUS:DONE return."""
    _run_hook(_make_done_return(), files_dir=tmp_path)
    row = _read_ledger(tmp_path)[0]
    assert row["marker"] == "DONE", f"Expected DONE, got {row['marker']!r}"


def test_files_changed_count(tmp_path: Path) -> None:
    """files_changed_count matches the length of the files_changed list."""
    _run_hook(_make_done_return(), files_dir=tmp_path)
    row = _read_ledger(tmp_path)[0]
    # _make_done_return writes ["src/foo.py", "tests/test_foo.py"]
    assert row["files_changed_count"] == 2, (
        f"Expected 2, got {row['files_changed_count']}"
    )


def test_session_id_extracted(tmp_path: Path) -> None:
    """session_id field matches the payload session_id."""
    _run_hook(_make_done_return(session_id="S-session-42"), files_dir=tmp_path)
    row = _read_ledger(tmp_path)[0]
    assert row["session_id"] == "S-session-42", f"Got {row['session_id']!r}"


def test_ts_is_iso8601_utc(tmp_path: Path) -> None:
    """ts field is a non-empty string that looks like an ISO-8601 UTC timestamp."""
    _run_hook(_make_done_return(), files_dir=tmp_path)
    row = _read_ledger(tmp_path)[0]
    ts = row.get("ts", "")
    assert isinstance(ts, str) and len(ts) >= 20, f"Bad ts: {ts!r}"
    assert "+" in ts or ts.endswith("Z"), f"ts not UTC-aware: {ts!r}"


def test_marker_revise(tmp_path: Path) -> None:
    """marker is REVISE when the assistant text contains ## NEXUS:REVISE."""
    payload = {
        "session_id": "S-rev",
        "agent_persona": "lens",
        "last_assistant_message": (
            "Issues found.\n\n## NEXUS:REVISE\n"
        ),
    }
    _run_hook(payload, files_dir=tmp_path)
    rows = _read_ledger(tmp_path)
    assert len(rows) == 1
    assert rows[0]["marker"] == "REVISE"


def test_marker_blocked(tmp_path: Path) -> None:
    """marker is BLOCKED when the assistant text contains ## NEXUS:BLOCKED."""
    payload = {
        "session_id": "S-blk",
        "agent_persona": "hermes",
        "last_assistant_message": "Can't proceed.\n\n## NEXUS:BLOCKED\n",
    }
    _run_hook(payload, files_dir=tmp_path)
    assert _read_ledger(tmp_path)[0]["marker"] == "BLOCKED"


def test_marker_unknown_when_no_h2(tmp_path: Path) -> None:
    """marker is 'unknown' when no H2 completion-marker heading is present."""
    payload = {
        "session_id": "S-nomarker",
        "agent_persona": "scout",
        "last_assistant_message": "Here is some context about the codebase.",
    }
    _run_hook(payload, files_dir=tmp_path)
    assert _read_ledger(tmp_path)[0]["marker"] == "unknown"


def test_files_changed_count_zero_when_no_json(tmp_path: Path) -> None:
    """files_changed_count is 0 when the return has no JSON block."""
    payload = {
        "session_id": "S-nojson",
        "agent_persona": "scout",
        "last_assistant_message": "Done.\n\n## NEXUS:DONE\n",
    }
    _run_hook(payload, files_dir=tmp_path)
    assert _read_ledger(tmp_path)[0]["files_changed_count"] == 0


def test_files_changed_count_zero_when_no_files_changed_key(tmp_path: Path) -> None:
    """files_changed_count is 0 when the JSON block has no files_changed key."""
    body = json.dumps({"status": "complete", "verification_result": "ok"})
    payload = {
        "session_id": "S-nofc",
        "agent_persona": "hermes",
        "last_assistant_message": (
            "Done.\n\n```json\n" + body + "\n```\n\n## NEXUS:DONE\n"
        ),
    }
    _run_hook(payload, files_dir=tmp_path)
    assert _read_ledger(tmp_path)[0]["files_changed_count"] == 0


def test_multiple_stops_append_multiple_rows(tmp_path: Path) -> None:
    """Each SubagentStop call appends a new row; ledger grows monotonically."""
    _run_hook(_make_done_return(persona="hermes", session_id="S-m1"), files_dir=tmp_path)
    _run_hook(_make_done_return(persona="lens", session_id="S-m2"), files_dir=tmp_path)
    rows = _read_ledger(tmp_path)
    assert len(rows) == 2, f"Expected 2 rows, got {len(rows)}"
    assert rows[0]["persona"] == "hermes"
    assert rows[1]["persona"] == "lens"


# ── fail-soft: malformed / edge-case payloads always exit 0 ──────────────────

def test_malformed_json_exits_0(tmp_path: Path) -> None:
    """Malformed JSON on stdin exits 0 and writes no row."""
    env = os.environ.copy()
    env["_HOOK_MEMORY_FILES_DIR"] = str(tmp_path)
    result = subprocess.run(
        [sys.executable, str(HOOK)],
        input="not valid json {{{",
        capture_output=True,
        text=True,
        timeout=10,
        env=env,
    )
    assert result.returncode == 0
    assert _read_ledger(tmp_path) == []


def test_empty_stdin_exits_0(tmp_path: Path) -> None:
    """Empty stdin exits 0 and writes no row."""
    env = os.environ.copy()
    env["_HOOK_MEMORY_FILES_DIR"] = str(tmp_path)
    result = subprocess.run(
        [sys.executable, str(HOOK)],
        input="",
        capture_output=True,
        text=True,
        timeout=10,
        env=env,
    )
    assert result.returncode == 0
    assert _read_ledger(tmp_path) == []


def test_array_payload_exits_0(tmp_path: Path) -> None:
    """A JSON array (not an object) exits 0 and writes no row."""
    rc, _, _ = _run_hook([], files_dir=tmp_path)  # type: ignore[arg-type]
    # _run_hook json.dumps the value — list encodes as []
    assert rc == 0
    assert _read_ledger(tmp_path) == []


def test_empty_object_payload_writes_row(tmp_path: Path) -> None:
    """An empty {} payload still writes a row with fallback 'unknown' values."""
    rc, _, _ = _run_hook({}, files_dir=tmp_path)
    assert rc == 0
    rows = _read_ledger(tmp_path)
    # An empty dict is a valid (if sparse) SubagentStop payload — we still
    # record the event with sentinel values so the ledger stays complete.
    assert len(rows) == 1
    assert rows[0]["persona"] == "unknown"
    assert rows[0]["session_id"] == "unknown"
    assert rows[0]["marker"] == "unknown"
    assert rows[0]["files_changed_count"] == 0


def test_tool_input_subagent_type_fallback(tmp_path: Path) -> None:
    """persona falls back to tool_input.subagent_type when outer keys absent."""
    payload = {
        "session_id": "S-ti",
        "tool_input": {"subagent_type": "quill-py"},
        "last_assistant_message": "## NEXUS:DONE\n",
    }
    _run_hook(payload, files_dir=tmp_path)
    assert _read_ledger(tmp_path)[0]["persona"] == "quill-py"


def test_prompt_hash_empty_when_no_router_decisions(tmp_path: Path) -> None:
    """prompt_hash is '' when router_decisions.jsonl does not exist."""
    _run_hook(_make_done_return(session_id="S-ph"), files_dir=tmp_path)
    row = _read_ledger(tmp_path)[0]
    assert row["prompt_hash"] == ""


def test_prompt_hash_recovered_from_router_decisions(tmp_path: Path) -> None:
    """prompt_hash is recovered from a matching router_decisions.jsonl row."""
    expected_hash = hashlib.sha256(b"my-test-prompt").hexdigest()
    decision_row = {
        "session_id": "S-rph",
        "prompt_hash": expected_hash,
        "pred_persona": "hermes",
        "ts": "2026-06-12T00:00:00+00:00",
    }
    decisions = tmp_path / "router_decisions.jsonl"
    decisions.write_text(json.dumps(decision_row) + "\n")

    payload = _make_done_return(session_id="S-rph")
    _run_hook(payload, files_dir=tmp_path)

    row = _read_ledger(tmp_path)[0]
    assert row["prompt_hash"] == expected_hash


def test_row_is_valid_json(tmp_path: Path) -> None:
    """Every appended row must be parseable as JSON (no garbage lines)."""
    _run_hook(_make_done_return(), files_dir=tmp_path)
    ledger = tmp_path / "completion_events.jsonl"
    for line in ledger.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        obj = json.loads(line)  # raises if invalid
        assert isinstance(obj, dict), f"Row is not a dict: {obj!r}"
