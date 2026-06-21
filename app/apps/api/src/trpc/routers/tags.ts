// Tags — unified read-only view across the four disjoint tag stores.
//
// Nexus's tag taxonomy is fragmented across:
//   - labels                   (team-scoped, has a color)
//   - todos.tags[]             (free text per row)
//   - library_entry_tags       (separate join table, free text)
//   - prompts.tags[]           (free text per row)
//
// A full migration unifies them under `labels` (see relationships.md:7),
// but that's a multi-table refactor with backfill. This router is the
// cheaper alternative — surface the visibility without changing storage.
// Returns one row per distinct tag name with a count, source list, and
// the canonical color when one exists (labels-only — todos/library/prompts
// don't carry colors).
//
// Joins are done in Postgres so a 10k-tag workspace doesn't pull
// everything to the app server. teamId guard via labels.team_id +
// joins against prompts.product → prompt_products.team_id +
// library_entries.team_id + todos.team_id.

import { protectedProcedure, router } from "@api/trpc/init";
import { db } from "@mimir/db/client";
import { sql } from "drizzle-orm";
import { z } from "zod/v3";

export const tagsRouter = router({
	list: protectedProcedure
		.input(
			z
				.object({
					search: z.string().optional(),
				})
				.optional(),
		)
		.query(async ({ ctx, input }) => {
			const teamId = ctx.user.teamId!;
			const searchPattern = input?.search?.trim()
				? `%${input.search.trim().toLowerCase()}%`
				: null;

			// UNION ALL across the four stores. Each subquery emits
			// (name, color, source). We aggregate in the outer query
			// to produce `{tag, color, count, sources}` per distinct name.
			//
			// LOWER(name) collation: tags are case-insensitive on display
			// since most users type "auth" / "Auth" / "AUTH" interchangeably.
			const rows = await db.execute<{
				tag: string;
				color: string | null;
				count: number;
				sources: string[];
			}>(sql`
				WITH unified AS (
					SELECT LOWER(l.name) AS name, l.color AS color, 'labels'::text AS source
					FROM labels l
					WHERE l.team_id = ${teamId}

					UNION ALL

					SELECT LOWER(tag.value) AS name, NULL::text AS color, 'todos'::text AS source
					FROM todos t,
					     LATERAL unnest(t.tags) AS tag(value)
					WHERE t.team_id = ${teamId}
					  AND COALESCE(array_length(t.tags, 1), 0) > 0

					UNION ALL

					SELECT LOWER(let.tag) AS name, NULL::text AS color, 'library'::text AS source
					FROM library_entry_tags let
					JOIN library_entries le ON le.id = let.entry_id
					JOIN library_sources ls ON ls.id = le.source_id
					WHERE ls.team_id = ${teamId}

					UNION ALL

					SELECT LOWER(tag.value) AS name, NULL::text AS color, 'prompts'::text AS source
					FROM prompts p
					JOIN prompt_products pp ON pp.id = p.product_id,
					     LATERAL unnest(p.tags) AS tag(value)
					WHERE pp.team_id = ${teamId}
					  AND COALESCE(array_length(p.tags, 1), 0) > 0
				)
				SELECT
					name AS tag,
					-- A given tag may exist in labels (with a color) AND in
					-- other stores (no color). Prefer the labels color.
					MAX(color) FILTER (WHERE color IS NOT NULL) AS color,
					COUNT(*)::int AS count,
					ARRAY_AGG(DISTINCT source ORDER BY source) AS sources
				FROM unified
				${searchPattern ? sql`WHERE name ILIKE ${searchPattern}` : sql``}
				GROUP BY name
				ORDER BY name ASC
				LIMIT 1000
			`);

			// drizzle's `execute` returns `{rows: ...}` for pg, plain array for
			// some drivers. Normalise here so the consumer always sees an array.
			// biome-ignore lint/suspicious/noExplicitAny: drizzle execute() returns driver-dependent shape
			const data = (rows as any).rows ?? (rows as any);
			return data as Array<{
				tag: string;
				color: string | null;
				count: number;
				sources: string[];
			}>;
		}),
});
