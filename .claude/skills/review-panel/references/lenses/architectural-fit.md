> **Canonical (DEC-060/061; owner-ratified 2026-07-05); dispatch-wiring deferred to R3 — loadable now, not yet orchestrator-routed.** Reviewer
> fragment for `review-panel` (DEC-060/061). Not wired to any agent.

# Lens: Architectural Fit

## What this lens checks
Whether the diff respects the project's existing seams: layering (does UI code reach
past its API boundary into a data layer directly?), ownership boundaries (`do_not_touch`
scopes from the leg's brief), and whether the change introduces a second implementation
of something that already has a canonical home (a new ad-hoc parsing helper next to an
existing shared one). Re-derive the expected seam from the surrounding code structure
BEFORE reading the producer's claim that the change "fits the existing pattern."

Checks in scope: does the diff cross a boundary named in the leg's `do_not_touch` list;
does it duplicate a helper/table/mapping that already exists elsewhere in the tree
(triplicated logic is a real, previously-observed failure mode in this class of
project); does it introduce a second writer to a single-writer resource; does new code
match the layering convention of the file it's added to (e.g. a read-side route file
gaining a write call).

## Worked example
```diff
--- a/app/api/views/route.ts
+++ b/app/api/views/route.ts
@@ -10,6 +10,9 @@ import { getView } from "@/lib/db/read";
+import { getWriteConnection } from "@/lib/db/write";
 
 export async function GET(req: Request, { params }: { params: { id: string } }) {
+  const conn = getWriteConnection();
+  await conn.execute("UPDATE views SET last_viewed = now() WHERE id = ?", [params.id]);
   const view = await getView(params.id);
```

Finding (meets the bar):
```json
{
  "file_line": "app/api/views/route.ts:13",
  "claim": "read-side GET route acquires a write connection and issues an UPDATE",
  "evidence": "getWriteConnection() imported from lib/db/write and used inside a route file whose only other import is lib/db/read; this leg's brief scoped it to a read endpoint and this file's existing convention (every other handler in the directory) never writes. This is a second-writer / layering violation, not a stylistic nit.",
  "severity": "high"
}
```
The non-obvious part: this would pass a correctness check (the UPDATE is syntactically
fine and the GET still returns the right shape) — architectural-fit is the lens that
catches the boundary crossing correctness has no reason to look for.
