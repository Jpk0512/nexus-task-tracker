> **Canonical (DEC-060/061; owner-ratified 2026-07-05); dispatch-wiring deferred to R3 — loadable now, not yet orchestrator-routed.** Reviewer
> fragment for `review-panel` (DEC-060/061). Not wired to any agent.

# Lens: Performance

## What this lens checks
Whether the diff introduces an avoidable per-request or per-item cost that scales badly:
N+1 query patterns, an added synchronous call inside a loop, an unbounded fan-out
(no cap on `downstream_consumers`-style calls), or a newly-added full-table/full-collection
scan where an indexed or paginated lookup already existed. Re-derive the request/data
shape from the diff BEFORE reading the producer's claim that "this is fine at current
scale" — a claim about scale needs a number, not an assertion.

Checks in scope: a loop that issues one query/call per iteration where a single
batched call would do; a newly-introduced synchronous await inside a hot path that used
to be fire-and-forget or batched; missing pagination/limit on a query that can return
an unbounded result set; a cache or memoization removed without a stated reason.

## Worked example
```diff
--- a/app/api/views/route.ts
+++ b/app/api/views/route.ts
@@ -20,8 +20,11 @@ export async function GET(req: Request) {
   const ids = await listViewIds();
-  const views = await getViewsBatch(ids);
+  const views = [];
+  for (const id of ids) {
+    views.push(await getView(id));
+  }
   return Response.json(views);
 }
```

Finding (meets the bar):
```json
{
  "file_line": "app/api/views/route.ts:24",
  "claim": "batched getViewsBatch(ids) replaced with a per-id await inside a for loop",
  "evidence": "getViewsBatch(ids) issued one query for the full id list; the rewrite issues one getView(id) call per iteration and awaits each sequentially, turning a single query into N sequential round-trips with no stated cap on listViewIds() — a classic N+1 with unbounded N.",
  "severity": "high"
}
```
The non-obvious part: this diff would pass both correctness and architectural-fit (it
returns the same shape, from the same layer) — performance is the lens whose entire job
is noticing the batched call became sequential, which no other lens is scoped to catch.
