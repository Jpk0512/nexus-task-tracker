# Implementation Progress Log

**Started:** 2026-07-20  
**Branch:** main  

---

## Checkpoint log

| Time | Phase | Status | Notes |
|---|---|---|---|
| 2026-07-20 | Bootstrap | done | PLAN + docker + seed |
| 2026-07-20 | Phase A | done | SoftIcon, sidebar IA, routes, Create Project, Home tiles; shots in implementation/shots/ |
| 2026-07-20 | Phase B | done | DoNowCard on Home (Attention Graph rank) |
| 2026-07-20 | Phase C | partial | Capture Dump|Todos|Outline; promote create-then-archive |

---

## Phase checklist

### Phase A — Foundation shell
- [x] SoftIcon component
- [x] Sidebar IA rewrite (plain icons, no scrollbar CSS, Create Project)
- [x] Route aliases / redirects (notes→knowledge, skills→library; new shells)
- [ ] Settings grouped shell
- [x] Create Project page shell
- [ ] Docker up + visual verify
- [ ] Commit + push

### Phase B — Attention
- [ ] Focus page
- [ ] Needs-you data slice
- [ ] Home Do now + pulse + health strip
- [ ] Visual verify · commit · push

### Phase C — Capture
- [ ] Capture page Dump | Todos | Outline
- [ ] Safe promote
- [ ] Home multi-mode capture
- [ ] Visual verify · commit · push

### Phase D — Project Place
- [ ] Resource strip
- [ ] Initiative composer UX
- [ ] Tab cleanup
- [ ] Visual verify · commit · push

### Phase E — Notes / Skills
- [ ] Notes alias + notebook cues
- [ ] Open ZenNotes button
- [ ] Skills square grid
- [ ] Visual verify · commit · push

### Phase F — Ops
- [ ] MCP catalog page
- [ ] Secrets page shell
- [ ] Health + Activity pages
- [ ] Visual verify · commit · push

### Phase G — Meetings MVP
- [ ] Meetings page shell + paste
- [ ] Actions → tasks
- [ ] Visual verify · commit · push

### Phase H–J
- [ ] Command bar expansion
- [ ] Companion stub
- [ ] Starter thin UI
- [ ] Polish backlog documented

---

## Blockers

| ID | Issue | Resolution |
|---|---|---|
| B1 | Docker base `local-mimrai/node:20-alpine` missing | Tag from node:20-alpine |

---

## Shots

Evidence screenshots land in `implementation/shots/`.
