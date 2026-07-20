# Dashboard OS — Implementation Plan

**Branch:** session branch (`main` unless changed)  
**Target:** Web app on http://localhost:5179 (Docker Desktop)  
**Source of truth:** `docs/design/DASHBOARD-OS.md`, `UX-FEATURE-DESIGN.md` §14 locks, `FEATURE-UX-AUDIT.md`, `final.html` (visual), `additions.html` / `features-ux.html`  
**Out of scope for this build:** Electron packaging (menu-bar/dock are designed; web-first)

---

## 0. Locks (do not violate)

| Lock | Value |
|---|---|
| Soft icons | Content only — **not** sidebar |
| Sidebar | Plain stroke icons; **no visible scrollbar** |
| Home | Do now + project pulse + Needs you + health strip + starter |
| IA | Focus / Capture / Brain / Ops (+ Insight: Health, Activity, Rituals) |
| Inbox | **Needs you** = attention only |
| Capture | **Dump** default (not Task, not Inbox) |
| Focus | Merge Lens + My Tasks + Triage; Zen = mode later |
| Vault paths | `projects/{projectId}/` |
| AI | LM Studio primary · Gemini fallback · always labeled |
| Promote MVP | Capture + Todos + Meeting actions; safe (create then archive) |
| Starter | Dedicated nav **Create Project**; thin entry now, full workshop phased |
| Settings | Grouped left-nav + detail pane (not 6 flat cards) |

---

## 1. Architecture of the rebuild

```
Phase A — Foundation shell (visible win)
  SoftIcon · Sidebar IA · redirects · Settings shell · Create Project entry
Phase B — Attention
  Focus route · Needs-you · Do now · Home composition
Phase C — Capture
  Capture page · Dump · safe promote · outline stub
Phase D — Project Place
  Overview resource strip · initiative composer UX · tab cleanup
Phase E — Notes / Skills
  Rename Knowledge→Notes · notebook tree · Open ZenNotes · Skills squares
Phase F — Ops
  MCP catalog page · Secrets shell (Infisical stub) · Health · Activity
Phase G — Meetings MVP
  Meetings page · paste/upload · actions → tasks · file note
Phase H — Command + AI
  ⌘K expansion · companion stub · Ask vault stub (LM Studio/Gemini)
Phase I — Starter workshop
  FEAT-003 thin→full (phases UI + runtime later)
Phase J — Polish
  Filters everywhere · rituals · templates · empty states · density
```

Each phase: implement → docker rebuild/hot reload → browser visual check → commit → push.

---

## 2. File ownership map

| Area | Primary paths |
|---|---|
| SoftIcon | `app/apps/dashboard/src/components/ui/soft-icon.tsx` (new) |
| Sidebar | `app/apps/dashboard/src/components/app-sidebar/*` |
| Home | `app/apps/dashboard/src/components/home/*` |
| Focus | `app/apps/dashboard/src/app/team/[team]/(navigation)/focus/` (new) + lens reuse |
| Capture | `app/apps/dashboard/src/app/.../capture/` (new) + todos |
| Create Project / Starter | `app/apps/dashboard/src/app/.../create-project/` (new) |
| Settings | `app/apps/dashboard/src/app/.../settings/*` layout redesign |
| Notes | knowledge components + routes rename/alias |
| Skills | library list-view → square grid + route alias |
| Health / Activity / Rituals | new pages under navigation |
| MCPs / Secrets | elevate from settings |
| API | existing tRPC; new routers only when needed |
| Docker | `app/docker-compose.local.yaml` |

---

## 3. Phase acceptance (summary)

### A — Foundation
- [ ] SoftIcon component + Story-less usage on Home tiles
- [ ] Sidebar matches IA (plain icons, no scrollbar, Create Project item)
- [ ] Old routes redirect (my-tasks→focus, knowledge→notes alias, library→skills alias, inbox→focus?needs=you)
- [ ] Settings grouped shell
- [ ] Create Project page shell (entry to starter)

### B — Attention
- [ ] `/focus` with Needs you | Today | Upcoming | Anytime | Someday | Logbook
- [ ] Home Do now top 5 with reason chips
- [ ] Needs you counts on sidebar + Home rail

### C — Capture
- [ ] `/capture` Dump | Todos | Outline
- [ ] Safe promote (create then archive)
- [ ] Home capture bar multi-mode Dump default

### D–J
See PROGRESS.md checkboxes per phase as we enter them.

---

## 4. Verification protocol (every phase)

1. Code: `cd app && bun run check-types` (or dashboard filter) when feasible  
2. Docker: rebuild/restart affected services  
3. Browser: `aside` or `agent-browser` open `http://localhost:5179/team/local-dev`  
4. Screenshot evidence into `implementation/shots/`  
5. Commit with phase id · push origin  

---

## 5. Risk & order notes

- Prefer **aliases + redirects** over deleting old routes immediately.  
- Knowledge engine stays; product shape changes first.  
- Infisical/LM Studio: UI shells + env config first; real probes when services available.  
- Full Starter workshop after A–D so seal has a place to land.  
- Electron deferred; design Mac affordances as web-equivalent (⌘K, capture drawer).

---

## 6. Definition of done (this engagement)

User-visible parity with `final.html` master vision for:
1. Shell IA + Home + Focus + Capture  
2. Project Place polish + Create Project entry  
3. Notes/Skills rename + visual  
4. Health + Activity + Meetings MVP shell  
5. MCP catalog page + Settings grouped  
6. Documented remaining backlog for embeddings/Infisical live/Starter full workshop  

Not every P2 must ship if blocked on external services — must be **documented** with next steps.
