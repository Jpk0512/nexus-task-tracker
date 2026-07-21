# Broker Dispatch Gate — Full Detail

`SKILL.md` §9 keeps only the ritual sequence. This is the full mechanics + the block
strings you will see.

Every `Task` dispatch is mechanically gated by the `.claude/hooks/broker-gate.py`
PreToolUse hook. It blocks dispatch unless `nexus_validate_brief_tool` ran THIS turn. The
ritual is **validate → ping → dispatch**:

1. Call `mcp__nexus-broker__nexus_validate_brief_tool` with the brief you are about to
   dispatch. It writes `.memory/files/broker_state.json` with `approved` + `called_at`.
2. After running `notepad list`, call `mcp__nexus-broker__nexus_notepad_ping`.
3. Dispatch the `Task`. The gate checks `approved=true` AND `called_at` is < **300 s**
   old (the turn-stale window). If you stall past that between validate and dispatch,
   re-call validate. The notepad ping's own freshness window is **900 s**.

**Block strings you will see (and the fix for each):**
- `broker rejected dispatch to '<persona>' — Task dispatch not allowed. Call
  nexus_validate_brief with a valid brief first.` → the brief failed validation; fix the
  brief, re-validate.
- `broker_state.json has no called_at timestamp — nexus_validate_brief was not called
  this turn.` → you never validated; validate now.
- `broker_state.json is stale (<N>s old, max <window>s) — call nexus_validate_brief again
  for this turn.` → re-validate, then dispatch promptly.

**Fail-CLOSED:** if `.memory/files/broker_state.json` is missing/malformed/unreadable
(e.g. the nexus-broker MCP is not running), the gate **blocks** the Task (exit 2) — a
down broker must be loud, not silently bypassed. Set `NEXUS_BROKER_ALLOW_DEGRADED=1` to
allow Tasks while degraded (a LOUD `additionalContext` warning fires every turn until the
broker is restored); unset it and restart the broker to re-arm.

**Disambiguation:** this is the **nexus-broker validation MCP** (`python -m
broker.server`), a local validation server this project installs alongside Nexus — NOT a
Redis message broker. The two broker tools (`nexus_validate_brief_tool`,
`nexus_notepad_ping`) are the only MCP tools the orchestrator calls itself.
