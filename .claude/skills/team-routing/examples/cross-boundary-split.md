# Worked example — splitting a cross-boundary brief

**Input:** a feature request lands: "add a settings page that reads user preferences from
the database and displays them." Drafted as one brief for a single UI implementer.

**Ownership-intersection check (before dispatch):**
1. File-glob list: `app/components/settings/**` (UI), `app/api/settings/route.ts`
   (server-side API route), `models/user_prefs.malloy` (schema, if the read needs a new
   model).
2. Check against the forbidden-directory table (`references/ownership-and-isolation.md`):
   the UI implementer persona cannot touch `app/api/**`; the server-side implementer
   cannot touch `app/components/**`.
3. **Crosses two ownership lines** — split required.

**Split brief (Fan-out-and-synthesize shape, per `Skill nexus-dispatch-catalog`):**
- Leg 1 → server-side implementer persona: `app/api/settings/route.ts` (read from the
  DB, return JSON).
- Leg 2 → UI implementer persona: `app/components/settings/**` (fetch from the new route,
  render).
- If a new DB model is needed: a third leg → the schema/data-modeling persona designs it
  FIRST (pairing rule: schema design precedes the persona that executes the migration).

**Isolation decision:** 2 independent code-writing legs (server route + UI component, no
shared file scope) → worktree isolation is the DEFAULT (`references/ownership-and-isolation.md`
§Isolation discipline) — register a worktree per leg before spawning.

**Verify:** each leg gets its own Lens verify keyed to its own `files_changed`, per-leg
(not one generic Lens call over the merged diff) since the legs ran in separate
worktrees.

**Non-obvious delta:** the split happens BEFORE dispatch, at brief-authoring time — never
"let the UI persona figure out it needs a route and ask for help." A cross-boundary brief
that reaches Lens is already a REVISE, which is strictly more expensive than splitting
up front.
