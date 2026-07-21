---
description: Run the Nexus health self-test (.memory/health.py via log.py) and validate that every foundational skill named in docs/agents/SKILL_MAP.md resolves to exactly one installed skill dir under .claude/skills/.
argument-hint: [--table|--md|--json] [--drift]
---

This command has two jobs. Run both; report both.

### 1. Health self-test

Run the single-project health self-test, which executes `run_checks` from
`.memory/health.py` via the `log.py` CLI shim. Default to a human-readable
table unless `$ARGUMENTS` requests otherwise:

```bash
python3 .memory/log.py health --table --no-color
```

Honor `$ARGUMENTS` if present (e.g. `--json`, `--md`, `--drift`) by passing it
through instead of the default flags. Report any FAIL or WARN line verbatim,
with its hint.

### 2. SKILL_MAP resolution check

`docs/agents/SKILL_MAP.md` is the authoritative source for the minimum skills
each code-writing persona requires. Every skill name listed in its `skills`
column MUST resolve to exactly one directory under `.claude/skills/`. This is
a real correctness check — SKILL_MAP.md can drift from what's actually
installed (a renamed, removed, or never-created skill dir silently breaks
Gate 2 advisories). Do NOT write a new script for this — run this exact
grep/test loop:

```bash
for skill in $(grep -oE '^\| [a-z0-9-]+ \| [^|]+ \| [a-zA-Z0-9,. -]+ \|$' docs/agents/SKILL_MAP.md \
    | grep -v '^| persona |' \
    | awk -F'|' '{print $4}' | tr ',' '\n' | sed 's/^ *//; s/ *$//' | sort -u); do
  count=$(find .claude/skills -maxdepth 1 -mindepth 1 -type d -name "$skill" | wc -l | tr -d ' ')
  if [ "$count" -eq 1 ]; then
    echo "OK    $skill"
  elif [ "$count" -eq 0 ]; then
    echo "MISS  $skill  (no dir under .claude/skills/)"
  else
    echo "DUP   $skill  ($count dirs match — ambiguous)"
  fi
done
```

Report every `MISS` and `DUP` line — these are SKILL_MAP.md/`.claude/skills/`
drift and should be surfaced to the user, not silently ignored. `OK` lines
need no further comment.

### Output

Summarize both checks in one short report:

```
## Nexus Health — <date>

### health.py self-test
<PASS/FAIL/WARN summary, or "all clear">

### SKILL_MAP.md -> .claude/skills/ resolution
<OK count> resolved, <MISS count> missing, <DUP count> ambiguous
<list any MISS/DUP lines>
```
