/**
 * Performance budget — iter-10 codex amendment #7.
 *
 * This module is the cultural source-of-truth for the perf rules every
 * dashboard surface should respect. There is no runtime enforcement (yet) —
 * the constants below are exported so feature code can import them rather
 * than hard-coding magic numbers, and the JSDoc on each section documents
 * the policy a future implementer (or reviewer) should hold the bar against.
 *
 * Sections:
 *   1. Pagination thresholds
 *   2. Aggregation strategy (count badges, sidebar totals)
 *   3. Lazy-loading + skeleton policy for detail views
 *   4. First-paint render budget
 *   5. Vector / semantic search reservation
 *
 * If you find yourself violating any of these, write a comment explaining
 * why and link the ADR — denser UI is fine, denser-and-slower is not.
 */

// ─── 1. Pagination thresholds ────────────────────────────────────────────
//
// List views must paginate. The default page size is 50; the absolute upper
// bound (e.g. "Show 200") is 200. Crossing 200 either means the user wants a
// filtered view (push them to a filter) or wants a count (use a count badge).
// Never render >200 entity rows in a single client tree without virtualisation.
export const LIST_PAGE_SIZE = 50;
export const LIST_PAGE_SIZE_MAX = 200;

// ─── 2. Aggregation strategy ─────────────────────────────────────────────
//
// Count badges (sidebar totals, "12 open · 3 overdue", etc.) MUST source from
// cached selectors — never from a per-render `array.filter().length` over the
// full dataset, and never from a per-row trpc query. The right shape:
//
//   const counts = useQuery(trpc.tasks.counts.queryOptions({ projectId }), {
//     staleTime: 60_000,
//   });
//
// Counts have a long staleTime; rows have a short one. Mixing them inverts
// the load — every keystroke in a filter shouldn't refetch the sidebar.
export const COUNT_BADGE_STALE_MS = 60_000;

// ─── 3. Lazy-load + skeleton ─────────────────────────────────────────────
//
// Detail views (task panel, project page, document page) render in this
// order:
//   1. The entity skeleton from the cache (free — already there from the
//      list view that opened it).
//   2. A skeleton for *related* data (comments, backlinks, activity).
//   3. The fetched related data, replacing the skeleton.
//
// Do not block the entity render on related-data fetches. Do not stack two
// suspense boundaries deep — the user sees the top entity within one paint.
export const DETAIL_RELATED_SKELETON_DELAY_MS = 100;

// ─── 4. First-paint render budget ────────────────────────────────────────
//
// Complex views (Triage board, Tasks-with-grouping, Knowledge tree) must not
// block first paint for more than 200ms on a mid-spec machine. If a single
// component routinely costs more, split the work behind `useDeferredValue`
// or move it off the critical path.
export const FIRST_PAINT_BUDGET_MS = 200;

// ─── 5. Vector / semantic search reservation ─────────────────────────────
//
// Vector search (pgvector embeddings, similarity over knowledge / library) is
// reserved for *explicit* user invocation — a "Find similar" button, the
// "Smart suggestions" panel toggle, etc. It MUST NOT fire on every keystroke
// of a filter box or every row of a list. Reason: each query incurs an
// embedding round-trip on first run, and the pg query plan is not cheap.
//
// Plain-text search (LIKE / ilike / pg_trgm) handles the keystroke case.
export const VECTOR_SEARCH_IS_OPT_IN = true;

/**
 * Convenience: clamp a requested page size to the documented bounds. Use
 * this in route loaders / trpc inputs that accept a user-supplied `pageSize`
 * so the cap is enforced in one place.
 */
export function clampPageSize(requested: number | undefined): number {
	if (!requested || requested <= 0) return LIST_PAGE_SIZE;
	if (requested > LIST_PAGE_SIZE_MAX) return LIST_PAGE_SIZE_MAX;
	return Math.floor(requested);
}
