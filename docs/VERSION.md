# Nexus Version Reference

This document is the authoritative reference for how Nexus versions are tracked,
where the version string lives, and what the install/update tooling writes. Every
statement below is verified against `nexus-package/install.sh` (lines 596-668) and
`.memory/log.py` (`_installed_nexus_version`).

---

## Source of truth

```
nexus-package/VERSION          — package-side version string (e.g. 1.14.0)
<project>/.memory/.nexus-version   — installed version, single line + newline
<project>/.nexus-ledger.json       — structured install/update record
```

`VERSION` is the canonical tag for the shipped package. The two per-project
files are written by the install/update tooling and are read by the
orchestrator at runtime (e.g. for the SessionStart health banner and for
`_installed_nexus_version()` in `log.py`).

---

## What install.sh writes

Both files are written by an inline Python block in `install.sh` (PYSTAMP).
The block runs as part of the "Stamp the installed version" phase, after all
files are copied and before `.memory` tooling is delivered.

### `.memory/.nexus-version`

A single-line file containing the version string followed by a newline:
```
1.14.0\n
```

Written by:
```python
(memory / ".nexus-version").write_text(version + "\n")
```

where `version = (pkg / "VERSION").read_text().strip()`.

### `.nexus-ledger.json`

A JSON object at `<project>/.nexus-ledger.json` (note: project root, not
`.memory/`). The file is created on first install and updated in-place on every
subsequent install/update.

Schema:
```json
{
  "version":      "<current version string>",
  "installed_at": "<ISO-8601 UTC — set once, never overwritten>",
  "updated_at":   "<ISO-8601 UTC — refreshed on every stamp>",
  "source":       "plexus",
  "phase_markers": [
    {
      "version":    "<version string>",
      "applied_at": "<ISO-8601 UTC>",
      "summary":    "<theme line from CHANGELOG.md, or empty string>"
    }
  ]
}
```

Field semantics:

| Field | Written by | Behaviour |
|---|---|---|
| `version` | Every stamp | Always set to the current package version |
| `installed_at` | First stamp only | `setdefault` — never overwritten by updates |
| `updated_at` | Every stamp | Always refreshed to the current UTC timestamp |
| `source` | Every stamp | Always `"plexus"` |
| `phase_markers` | Every stamp | Append-only, deduped by `version`. If an entry for this version already exists, its `applied_at` and `summary` are refreshed in-place rather than duplicated. |

`summary` is parsed from the first `### Theme:` line in the `## <version> — …`
section of `CHANGELOG.md`. Empty string if the file is absent or the section
has no Theme line.

---

## Reading the version at runtime

The orchestrator reads `.memory/.nexus-version`:
```python
# From log.py _installed_nexus_version():
base = Path(memory_dir) if memory_dir is not None else DB_PATH.parent
text = (base / ".nexus-version").read_text().strip()
return text or "unknown"
```

`memory_dir` defaults to `DB_PATH.parent` (the `.memory/` directory holding
`project.db`). The function is fail-soft: any `OSError` (file missing,
unreadable, empty) returns `"unknown"` so feedback capture and the health
banner never fail because version attribution is unavailable.

To check the installed version from the shell:
```bash
cat .memory/.nexus-version
```

To read the full ledger:
```bash
cat .nexus-ledger.json
```

---

## Version ordering

`log.py _version_tuple()` parses a version string into a comparable tuple:
- `"1.14.0"` → `(1, 14, 0)`
- `"unknown"` or empty → `(-1,)` (sorts lowest — treated as the oldest version)
- Non-numeric segments degrade to `0` (fail-soft for malformed legacy stamps)
- Extra segments beyond `X.Y.Z` are included as additional tuple elements

This ordering is used internally to determine whether a stored version predates
the current one (e.g. for upgrade decisions). It is not surfaced to operators
directly.

---

## safe_update.py

`tools/safe_update.py` (the Plexus live-source update path) contains a
`_stamp_version` function that writes the same two files using the same logic as
the PYSTAMP block in `install.sh`. The inline comment in `install.sh` explicitly
notes: _"phase_markers: append-only, deduped by version. Mirrors
safe_update._stamp_version."_

Both paths produce identical file layouts. There is no behavioral difference
between a fresh install and an update as far as the version files are concerned,
except that `installed_at` is preserved by `setdefault` on updates.

---

## CLAUDE.md statement

The installed project's `CLAUDE.md` contains:
> The installed Nexus version is in `.memory/.nexus-version` (a single line,
> e.g. `1.14.0`); `.nexus-ledger.json` carries the same version plus
> `installed_at`/`updated_at`. Both files are written at install/update time
> by `install.sh` and `tools/safe_update.py`. When asked "what version are
> you on?", read `.memory/.nexus-version` and report it.

This document provides the exact implementation detail behind that statement.
