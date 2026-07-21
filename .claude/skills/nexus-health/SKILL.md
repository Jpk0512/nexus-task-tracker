---
name: nexus-health
description: Run the Nexus health monitor for this project. Use when the user asks "is everything working", "check nexus health", "is the broker up", or after an upgrade. Returns a per-tier PASS/WARN/FAIL table.
---

# Skill: nexus-health

## Trigger
User wants visibility into install health. Phrases: "health check", "is nexus working", "is the broker up", "did the upgrade break anything", "what's nexus saying".

## What this skill does
Invokes `python3 .memory/log.py health` and reads the output. Reports the summary + any FAIL/WARN lines with hints. Recommends next action per failure.

## How to invoke
Run: `python3 .memory/log.py health`

Optional flags:
- `--no-runtime` — fast static-only (<1s)
- `--drift` — also compare target vs canonical package
- `--json` / `--md` — alternate formats
- `--history` — show last 10 logged FAIL events
- `--verbose` — include PASS hints / extra detail

## Reporting back
Surface the summary line (`N PASS · W WARN · F FAIL`), then enumerate any non-PASS items with their hints. If all green, just say "All checks PASS".
