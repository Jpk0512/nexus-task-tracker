"""schemas.py — NEX-001 (DEC-100 pillar 4): explicit JSON Schemas for the
broker's highest-value MCP tool returns and the shared hook JSON envelope.

Every schema below is derived from the ACTUAL code that produces the shape it
pins, not hand-invented:

  BROKER_RESULT_SCHEMA          <- broker.server.BrokerResult (TypedDict)
  DISCOVER_RESULT_SCHEMA        <- broker.discovery.DiscoverResult (TypedDict)
  NOTEPAD_PING_RESULT_SCHEMA    <- broker.server.nexus_notepad_ping's literal
                                    return dict (no TypedDict declared there)
  FEEDBACK_TOOL_RESULT_SCHEMA   <- broker.server.nexus_submit_feedback's four
                                    return statements (ok=True w/ + w/o
                                    captured_at, ok=False variants)
  WORKTREE_RECORD_SCHEMA        <- broker.worktree_registry.WorktreeRecord
  RELEASE_WORKTREE_RESULT_SCHEMA <- release_worktree's `-> bool` return type
  HOOK_DENY_ENVELOPE_SCHEMA     <- .claude/hooks/_gate_deny.py deny()'s
                                    literal {"hookSpecificOutput": {...}} emit
  HOOK_ADVISE_ENVELOPE_SCHEMA   <- .claude/hooks/_gate_deny.py advise()'s
                                    literal {"hookSpecificOutput": {...}} emit

A drift in any of these (a renamed/removed/added key, a type change) fails the
contract test in this directory that validates against it — that is the whole
point: pin the shape so a future edit that silently breaks a caller (broker-
gate.py, the orchestrator, a persona reading tool output) is caught here
instead of at runtime.
"""
from __future__ import annotations

_SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"

# ---------------------------------------------------------------------------
# MCP tool return shapes
# ---------------------------------------------------------------------------

# broker.server.BrokerResult:
#   approved: bool
#   warnings: list[str]
#   errors: list[str]
#   approved_brief: dict[str, Any] | None
BROKER_RESULT_SCHEMA = {
    "$schema": _SCHEMA_DIALECT,
    "type": "object",
    "required": ["approved", "warnings", "errors", "approved_brief"],
    "additionalProperties": False,
    "properties": {
        "approved": {"type": "boolean"},
        "warnings": {"type": "array", "items": {"type": "string"}},
        "errors": {"type": "array", "items": {"type": "string"}},
        "approved_brief": {"type": ["object", "null"]},
    },
}

# broker.discovery.DiscoverResult:
#   personas: list[str]
#   persona_intents: dict[str, list[str]]
DISCOVER_RESULT_SCHEMA = {
    "$schema": _SCHEMA_DIALECT,
    "type": "object",
    "required": ["personas", "persona_intents"],
    "additionalProperties": False,
    "properties": {
        "personas": {"type": "array", "items": {"type": "string"}},
        "persona_intents": {
            "type": "object",
            "additionalProperties": {"type": "array", "items": {"type": "string"}},
        },
    },
}

# broker.server.nexus_notepad_ping's literal return:
#   {"notepad_logged_at": <ISO8601 str>, "status": "recorded"}
NOTEPAD_PING_RESULT_SCHEMA = {
    "$schema": _SCHEMA_DIALECT,
    "type": "object",
    "required": ["notepad_logged_at", "status"],
    "additionalProperties": False,
    "properties": {
        "notepad_logged_at": {"type": "string", "minLength": 1},
        "status": {"const": "recorded"},
    },
}

# broker.server.nexus_submit_feedback's four return statements:
#   ok=False (invalid severity/category/empty message, or subprocess failure):
#       {"ok": False, "error": <str>, "id": None}
#   ok=True happy path: {"ok": True, "id": <int|None>, "captured_at": <str|None>}
#   ok=True fallback (log.py stdout didn't parse as JSON): {"ok": True, "id": None}
FEEDBACK_TOOL_RESULT_SCHEMA = {
    "$schema": _SCHEMA_DIALECT,
    "type": "object",
    "oneOf": [
        {
            "type": "object",
            "additionalProperties": False,
            "required": ["ok", "id"],
            "properties": {
                "ok": {"const": True},
                "id": {"type": ["integer", "null"]},
                "captured_at": {"type": ["string", "null"]},
            },
        },
        {
            "type": "object",
            "additionalProperties": False,
            "required": ["ok", "error", "id"],
            "properties": {
                "ok": {"const": False},
                "error": {"type": "string", "minLength": 1},
                "id": {"type": "null"},
            },
        },
    ],
}

# broker.worktree_registry.WorktreeRecord — the value nexus_register_worktree
# returns (the `path` argument is the registry KEY, never part of the record).
WORKTREE_RECORD_SCHEMA = {
    "$schema": _SCHEMA_DIALECT,
    "type": "object",
    "required": ["owner_id", "branch", "created_at", "ttl_seconds"],
    "additionalProperties": False,
    "properties": {
        "owner_id": {"type": "string"},
        "branch": {"type": "string"},
        "created_at": {"type": "string", "minLength": 1},
        "ttl_seconds": {"type": "integer"},
    },
}

# broker.worktree_registry.release_worktree -> bool (nexus_release_worktree
# passes it through unchanged) — a bare JSON boolean, not an object.
RELEASE_WORKTREE_RESULT_SCHEMA = {
    "$schema": _SCHEMA_DIALECT,
    "type": "boolean",
}

# ---------------------------------------------------------------------------
# Hook JSON envelope shapes (.claude/hooks/_gate_deny.py deny()/advise(),
# consumed by broker-gate.py and every sibling PreToolUse/SubagentStop gate
# that imports it, plus the hand-inlined callers — dispatch-capture.py's
# _redispatch_advisory, return-validator.py's _emit_advisory — that emit the
# byte-identical shape without importing the module).
# ---------------------------------------------------------------------------

# _gate_deny.deny(): {"hookSpecificOutput": {"hookEventName": <str>,
#                                             "permissionDecision": "deny",
#                                             "permissionDecisionReason": <str>}}
HOOK_DENY_ENVELOPE_SCHEMA = {
    "$schema": _SCHEMA_DIALECT,
    "type": "object",
    "required": ["hookSpecificOutput"],
    "additionalProperties": False,
    "properties": {
        "hookSpecificOutput": {
            "type": "object",
            "required": ["hookEventName", "permissionDecision", "permissionDecisionReason"],
            "additionalProperties": False,
            "properties": {
                "hookEventName": {"type": "string", "minLength": 1},
                "permissionDecision": {"const": "deny"},
                "permissionDecisionReason": {"type": "string", "minLength": 1},
            },
        },
    },
}

# _gate_deny.advise(): {"hookSpecificOutput": {"hookEventName": <str>,
#                                               "additionalContext": <str>}}
# NEVER carries permissionDecision (the fail-open guard test_gate_deny_contract
# .py::test_advise_json_does_not_contain_permission_decision already pins) —
# additionalProperties:false on the inner object enforces the same thing here.
HOOK_ADVISE_ENVELOPE_SCHEMA = {
    "$schema": _SCHEMA_DIALECT,
    "type": "object",
    "required": ["hookSpecificOutput"],
    "additionalProperties": False,
    "properties": {
        "hookSpecificOutput": {
            "type": "object",
            "required": ["hookEventName", "additionalContext"],
            "additionalProperties": False,
            "properties": {
                "hookEventName": {"type": "string", "minLength": 1},
                "additionalContext": {"type": "string", "minLength": 1},
            },
        },
    },
}
