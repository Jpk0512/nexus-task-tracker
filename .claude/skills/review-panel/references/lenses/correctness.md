> **Canonical (DEC-060/061; owner-ratified 2026-07-05); dispatch-wiring deferred to R3 — loadable now, not yet orchestrator-routed.** Reviewer
> fragment for `review-panel` (DEC-060/061). Not wired to any agent.

# Lens: Correctness

## What this lens checks
Does the diff do what the acceptance criteria say, on every path — not just the happy
path? Re-derive the expected behavior from the acceptance criteria BEFORE reading the
producer's claims about what the diff does. A correctness finding must trace a specific
input to a specific output (or a specific input to a missing/wrong branch), not assert
"looks correct" or "looks wrong."

Checks in scope: every branch implied by the acceptance criteria is present and
returns the right shape; boundary/edge cases named or implied by the criteria (empty
input, not-found, zero, max) are handled; the diff doesn't silently narrow or widen the
contract (e.g. an error case that used to 404 now falls through to a 200).

Adversarial framing: your job is to find the input that breaks this, not to confirm the
inputs the producer already tested.

## Worked example
Acceptance criterion: `GIVEN an unknown id WHEN GET /api/views/nope THEN 404 with
{error:'not_found'}`.

```diff
--- a/app/api/views/route.ts
+++ b/app/api/views/route.ts
@@ -12,7 +12,10 @@ export async function GET(req: Request, { params }: { params: { id: string } })
   const view = await getView(params.id);
-  return Response.json(view);
+  if (!view) {
+    return Response.json({ error: "not_found" }, { status: 404 });
+  }
+  return Response.json({ id: view.id, name: view.name, workbook_name: view.workbookName });
 }
```

Finding (meets the bar):
```json
{
  "file_line": "app/api/views/route.ts:15",
  "claim": "not-found branch added but success branch renames workbookName -> workbook_name, unverified against DB column casing",
  "evidence": "getView(params.id) return shape not shown in this diff; if the DB layer returns workbookName (camelCase) as claimed, the mapping is correct — but this cannot be confirmed from the diff alone",
  "severity": "uncertain"
}
```
The non-obvious part: the 404 branch is easy to verify and is correct; the harder,
easy-to-miss risk is the implicit field-rename on the success path, which is exactly
where an untraceable claim becomes an UNCERTAIN finding rather than a silent PASS.
