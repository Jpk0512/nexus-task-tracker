# Dashboard OS — Implementation Progress

**Runtime:** dashboard host-dev `:5179` · API docker `:3003` · postgres/redis docker  
**Seed:** `local-dev` / `local-dev-user`

---

## Timeline

| Time | Phase | Status | Notes |
|---|---|---|---|
| 2026-07-20 | Bootstrap | done | PLAN + docker + seed |
| 2026-07-20 | Phase A | done | SoftIcon, sidebar IA, routes, Create Project, Home tiles |
| 2026-07-20 | Phase B | done | DoNowCard, Focus+Needs you, health strip, redirects |
| 2026-07-20 | Phase C | done | Capture Dump/Todos/Outline; promote Todo·Task·Note; Home dump default |
| 2026-07-20 | Phase D | done | Project resource strip; tabs Notes/Skills; linked notes by projectId |
| 2026-07-20 | Phase E | done | Notes branding + ZenNotes; Skills grid; /notes /skills pages |
| 2026-07-20 | Phase F | done | MCPs list, Secrets vault, Health debt, Activity feed |
| 2026-07-20 | Phase G | done | Meetings paste → actions → Todos |
| 2026-07-20 | Phase H | done | ⌘K actions expanded; Ask vault companion stub (LM Studio/Gemini labeled) |
| 2026-07-20 | Phase I | done | Starter seed workshop + continue card (agent runtime later) |
| 2026-07-20 | Phase J | partial | Rituals live data; empty states; redirects; polish ongoing |

---

## Phase checklist

### Phase A — Foundation shell
- [x] SoftIcon component
- [x] Sidebar IA rewrite (plain icons, no scrollbar CSS, Create Project)
- [x] Route aliases / redirects (notes, skills, my-tasks→focus, inbox→focus?tab=needs-you)
- [x] Settings grouped shell (pre-existing left-nav groups)
- [x] Create Project page shell
- [x] Visual verify · commit · push

### Phase B — Attention
- [x] Focus page (Do now | Needs you)
- [x] Needs-you data slice + badge
- [x] Home Do now + health strip
- [x] Visual verify · commit · push

### Phase C — Capture
- [x] Capture page Dump | Todos | Outline
- [x] Safe promote Todo · Task · Note (create then archive)
- [x] Home multi-mode capture (Dump default)
- [x] Visual verify · commit · push

### Phase D — Project Place
- [x] Resource strip on overview
- [x] Tab cleanup (Notes / Skills labels)
- [x] Linked notes match `projects/{projectId}/`
- [x] Visual verify · commit · push

### Phase E — Notes / Skills
- [x] Notes branding + vault path cue + Open ZenNotes
- [x] Skills square grid + /skills route
- [x] Visual verify · commit · push

### Phase F — Ops
- [x] MCP catalog page
- [x] Secrets page (masked local store)
- [x] Health + Activity pages
- [x] Visual verify · commit · push

### Phase G — Meetings MVP
- [x] Meetings paste + heuristic extract
- [x] Actions → Todos
- [x] Visual verify · commit · push

### Phase H — Command + AI
- [x] ⌘K catalogue: focus/capture/notes/skills/meetings/health/…
- [x] Companion / Ask vault stub (provider labeled)
- [ ] Live LM Studio / Gemini streaming (host runtime)

### Phase I — Starter
- [x] Thin workshop seed + phase rail
- [x] Home continue card
- [ ] Agent grill / handoff / board materialize

### Phase J — Polish
- [x] Rituals with live counts
- [x] Crash fix: useInbox outside provider → useInboxCounts
- [ ] Density / empty-state pass remaining
- [ ] Docker production image bake of dashboard

---

## Deferred (tracked, not blocking MVP shell)

| ID | Item | Why deferred |
|---|---|---|
| D1 | Full Starter agent workshop | Needs host OAuth Claude/Codex |
| D2 | Infisical production bridge | Env + service |
| D3 | LM Studio/Gemini live Ask | Runtime wire |
| D4 | Electron shell | Explicitly out of scope |
| D5 | Transcription REST full pipeline | Meetings heuristic sufficient for MVP |
| D6 | Outline zoom / promote to board | Local outline works |

---

## Shots

`implementation/shots/` — phase-a through full-*.png evidence.
