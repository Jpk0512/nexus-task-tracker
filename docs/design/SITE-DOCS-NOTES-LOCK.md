# Site Docs + Notes — storage & IA lock

Status: **Phase 1 shipping** (2026-07-20). Site Docs shell + disk R/W + Existing link path live; Notes multi-root and real map generation still open.
Source: user clarifications after Documents → Site Docs design critique.

## Storage (locked)

| Surface | Source of truth | Edit behavior | Nexus-owned extras |
|---|---|---|---|
| **Site Docs** | On-disk folders the user selects (typically a site’s `/docs`) | Edits in Nexus write through to disk | **Nexus Maps** (mermaid / flow / graph) stored **in the app**, not in the site tree |
| **Notes** | On-disk folders the user selects (**plural** — multi-root) | Edits write through to disk | None required for v1 |
| **Nexus Maps** | App storage (DB or app data dir) | Regenerable Nexus artifacts | Shown inside Site Docs UI per site, clearly labeled as Nexus-generated |

Site Docs is a **mirror / review surface** for evolving project documentation — not a second copy in Postgres.

## Site Docs IA (locked)

Same shell as Notes Option C (list rail + editor + collapsible inspector), with one substitution:

- Notes left rail: **Categories** (Daily, Permanent, …) → file tree
- Site Docs left rail: **Sites** (project/site list) → selecting a site shows **that site’s docs tree**, plus Nexus Maps entries for that site

```
┌─────────────┬──────────────────────────┬────────────┐
│ Sites       │  Editor (selected file)  │ Inspector  │
│  · Site A   │                          │ (optional) │
│  · Site B ◀ │                          │            │
│ ─────────── │                          │            │
│ Tree        │                          │            │
│  docs/…     │                          │            │
│  maps/… *   │                          │            │
└─────────────┴──────────────────────────┴────────────┘
* maps are Nexus items, listed with the site, not files in /docs
```

Nav label: **Documents → Site Docs**.

## Create project (locked intent)

Three paths:

1. **Blank** — new project; scaffold/link a docs folder when a root exists
2. **From idea** — Project Starter → same docs attachment model
3. **Existing** — pick folder on disk → name + meta → **choose which folder(s) appear in Site Docs** (default hint: `docs/`)

Creating a project registers it as a **Site** in Site Docs when a docs path is known.

## Notes multi-folder (locked intent)

Settings (or Notes chrome): add/remove/reorder disk roots that appear in Notes. All edits are live on disk.

## Explicit non-goals (v1)

- Do not duplicate site docs into the DB as source of truth
- Do not write Nexus Maps into the site’s `/docs` unless the user exports
- Do not merge Notes vault and Site Docs into one tree

## Open (implementation, not product)

- Path-picker UX (native dialog vs path text + validate)
- Bind-mount / allowlist for containerized API reading host paths
- Map generation: on-demand first vs auto-on-import
- Whether “Sites” list = all projects or only projects with a docs path

## Visual chrome note

App sidebar header and main viewer header share `h-12` so the border line is continuous.
