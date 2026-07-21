> **Canonical (DEC-060/061; owner-ratified 2026-07-05); dispatch-wiring deferred to R3 — loadable now, not yet orchestrator-routed.** Reviewer
> fragment for `review-panel` (DEC-060/061). Not wired to any agent.

# Lens: Does It Run

## What this lens checks
Whether the diff, as written, actually executes — not whether it looks plausible.
This lens exists because a checklist/"be thorough" reviewer measured zero lift
(LSN-016 vs LSN-017): the fix is to re-derive execution by tracing imports, types, and
call signatures against what the diff actually changed, not by reading the producer's
claim that "tests pass" or "this compiles." If verification commands and their output
are available, this lens's first move is comparing the verbatim output against what the
diff claims — a claimed "N passed" with no matching command+output is itself a finding.

Checks in scope: a renamed/removed export still referenced elsewhere in the diff or in
unchanged call sites; a new import that doesn't exist in the target module; a type
signature change that breaks an existing caller not included in the diff; a test
assertion that would pass even if the implementation were deleted (tautological or
mocked-to-always-pass); a verification claim ("tests pass," "verified manually") with no
accompanying command + verbatim output.

## Worked example
```diff
--- a/app/api/views/route.ts
+++ b/app/api/views/route.ts
@@ -1,5 +1,5 @@
-import { getView } from "@/lib/db/read";
+import { getViewById } from "@/lib/db/read";
 
 export async function GET(req: Request, { params }: { params: { id: string } }) {
-  const view = await getView(params.id);
+  const view = await getViewById(params.id);
```

Finding (meets the bar):
```json
{
  "file_line": "app/api/views/route.ts:1",
  "claim": "import renamed getView -> getViewById; lib/db/read.ts export not shown in this diff",
  "evidence": "The diff renames the imported symbol at both the import line and the call site consistently, but lib/db/read.ts itself is not part of this diff — cannot confirm getViewById exists as an export there rather than still being named getView. Producer's claim of 'tests pass' has no verbatim test command or output attached to this diff to check against.",
  "severity": "uncertain"
}
```
The non-obvious part: internal consistency (the rename matches at both call sites) is
not the same as "it runs" — the export existing in the untouched file is the actual
question, and an unverifiable claim becomes an explicit UNCERTAIN finding rather than a
silent PASS on the strength of the diff "looking clean."
