// Prompt Library — AI-product → prompts. Saved prompt content with optional
// notes, variable extraction, and version bump-on-save semantics.

import { protectedProcedure, router } from "@api/trpc/init";
import { db } from "@nexus-app/db/client";
import { TRPCError } from "@trpc/server";
import { and, asc, desc, eq, sql } from "drizzle-orm";
import {
	boolean,
	integer,
	jsonb,
	pgTable,
	text,
	timestamp,
} from "drizzle-orm/pg-core";
import { z } from "zod/v3";

// Minimal projects table reference for team-ownership checks on setProject,
// and for projectName resolution in getPrompts.
const projects = pgTable("projects", {
	id: text("id").primaryKey(),
	teamId: text("team_id").notNull(),
	name: text("name").notNull(),
});

const promptProducts = pgTable("prompt_products", {
	id: text("id").primaryKey(),
	teamId: text("team_id").notNull(),
	name: text("name").notNull(),
	slug: text("slug").notNull(),
	description: text("description"),
	icon: text("icon"),
	color: text("color"),
	archived: boolean("archived").notNull().default(false),
	createdAt: timestamp("created_at", {
		withTimezone: true,
		mode: "string",
	}).notNull(),
	updatedAt: timestamp("updated_at", {
		withTimezone: true,
		mode: "string",
	}).notNull(),
});

const prompts = pgTable("prompts", {
	id: text("id").primaryKey(),
	productId: text("product_id").notNull(),
	// iter-10 Round F: optional FK to projects (codex amendment #2 — picks FK
	// over JSONB array on projects). SET NULL on project delete so the prompt
	// survives an aggressive cleanup.
	projectId: text("project_id"),
	name: text("name").notNull(),
	slug: text("slug").notNull(),
	content: text("content").notNull(),
	notes: text("notes"),
	variables: jsonb("variables"),
	tags: text("tags").array().notNull().default([]),
	version: integer("version").notNull().default(1),
	createdAt: timestamp("created_at", {
		withTimezone: true,
		mode: "string",
	}).notNull(),
	updatedAt: timestamp("updated_at", {
		withTimezone: true,
		mode: "string",
	}).notNull(),
});

// Version history snapshot — populated by updatePrompt when bumpVersion is
// true. The current row stays on `prompts`; this table accumulates the
// previous content + notes immediately before each bump so users can
// retrieve any prior revision.
//
// Schema applied via psql migration (see /tmp/linear-redesign progress
// log). Keeping the Drizzle definition inline mirrors the existing
// pattern in this file.
const promptVersions = pgTable("prompt_versions", {
	id: text("id").primaryKey(),
	promptId: text("prompt_id").notNull(),
	version: integer("version").notNull(),
	content: text("content").notNull(),
	notes: text("notes"),
	createdAt: timestamp("created_at", {
		withTimezone: true,
		mode: "string",
	})
		.notNull()
		.defaultNow(),
	createdBy: text("created_by"),
});

function slugify(s: string): string {
	return s
		.toLowerCase()
		.replace(/[^a-z0-9]+/g, "-")
		.replace(/^-+|-+$/g, "")
		.slice(0, 80);
}

function extractVariables(content: string): string[] {
	const out = new Set<string>();
	const re = /\{\{\s*([A-Za-z0-9_]+)\s*\}\}/g;
	let m: RegExpExecArray | null;
	// biome-ignore lint/suspicious/noAssignInExpressions: standard regex loop
	while ((m = re.exec(content)) !== null) {
		out.add(m[1]);
	}
	return Array.from(out);
}

export const promptsRouter = router({
	// ── Products ───────────────────────────────────────────────────────────
	getProducts: protectedProcedure.query(async ({ ctx }) => {
		return db
			.select({
				id: promptProducts.id,
				name: promptProducts.name,
				slug: promptProducts.slug,
				description: promptProducts.description,
				icon: promptProducts.icon,
				color: promptProducts.color,
				archived: promptProducts.archived,
				promptCount:
					sql<number>`(SELECT count(*)::int FROM prompts WHERE prompts.product_id = prompt_products.id)`.as(
						"prompt_count",
					),
				updatedAt: promptProducts.updatedAt,
			})
			.from(promptProducts)
			.where(eq(promptProducts.teamId, ctx.user.teamId!))
			.orderBy(asc(promptProducts.name));
	}),

	getProductBySlug: protectedProcedure
		.input(z.object({ slug: z.string() }))
		.query(async ({ input, ctx }) => {
			const [row] = await db
				.select()
				.from(promptProducts)
				.where(
					and(
						eq(promptProducts.slug, input.slug),
						eq(promptProducts.teamId, ctx.user.teamId!),
					),
				)
				.limit(1);
			return row ?? null;
		}),

	createProduct: protectedProcedure
		.input(
			z.object({
				name: z.string().min(1).max(120),
				description: z.string().optional(),
				icon: z.string().optional(),
				color: z.string().optional(),
			}),
		)
		.mutation(async ({ input, ctx }) => {
			const slug = slugify(input.name);
			const now = new Date().toISOString();
			const [row] = await db
				.insert(promptProducts)
				.values({
					id: `pp-${slug}-${Math.random().toString(36).slice(2, 7)}`,
					teamId: ctx.user.teamId!,
					name: input.name.trim(),
					slug,
					description: input.description ?? null,
					icon: input.icon ?? null,
					color: input.color ?? null,
					createdAt: now,
					updatedAt: now,
				})
				.returning();
			return row;
		}),

	deleteProduct: protectedProcedure
		.input(z.object({ id: z.string() }))
		.mutation(async ({ input, ctx }) => {
			await db
				.delete(promptProducts)
				.where(
					and(
						eq(promptProducts.id, input.id),
						eq(promptProducts.teamId, ctx.user.teamId!),
					),
				);
			return { ok: true };
		}),

	// ── Prompts ────────────────────────────────────────────────────────────

	getPrompts: protectedProcedure
		.input(z.object({ productSlug: z.string() }))
		.query(async ({ input, ctx }) => {
			const [product] = await db
				.select({ id: promptProducts.id })
				.from(promptProducts)
				.where(
					and(
						eq(promptProducts.slug, input.productSlug),
						eq(promptProducts.teamId, ctx.user.teamId!),
					),
				)
				.limit(1);
			if (!product) return { product: null, prompts: [] };
			const rows = await db
				.select({
					id: prompts.id,
					name: prompts.name,
					slug: prompts.slug,
					projectId: prompts.projectId,
					projectName: projects.name,
					tags: prompts.tags,
					version: prompts.version,
					updatedAt: prompts.updatedAt,
				})
				.from(prompts)
				.leftJoin(projects, eq(projects.id, prompts.projectId))
				.where(eq(prompts.productId, product.id))
				.orderBy(asc(prompts.name));
			return { product, prompts: rows };
		}),

	getPromptBySlug: protectedProcedure
		.input(z.object({ productSlug: z.string(), promptSlug: z.string() }))
		.query(async ({ input, ctx }) => {
			const [row] = await db
				.select({
					id: prompts.id,
					productId: prompts.productId,
					projectId: prompts.projectId,
					name: prompts.name,
					slug: prompts.slug,
					content: prompts.content,
					notes: prompts.notes,
					variables: prompts.variables,
					tags: prompts.tags,
					version: prompts.version,
					updatedAt: prompts.updatedAt,
					productSlug: promptProducts.slug,
					productName: promptProducts.name,
				})
				.from(prompts)
				.innerJoin(promptProducts, eq(promptProducts.id, prompts.productId))
				.where(
					and(
						eq(prompts.slug, input.promptSlug),
						eq(promptProducts.slug, input.productSlug),
						eq(promptProducts.teamId, ctx.user.teamId!),
					),
				)
				.limit(1);
			return row ?? null;
		}),

	createPrompt: protectedProcedure
		.input(
			z.object({
				productId: z.string(),
				name: z.string().min(1).max(160),
				content: z.string().default(""),
			}),
		)
		.mutation(async ({ input, ctx }) => {
			// Sanity-check product belongs to team
			const [p] = await db
				.select({ id: promptProducts.id })
				.from(promptProducts)
				.where(
					and(
						eq(promptProducts.id, input.productId),
						eq(promptProducts.teamId, ctx.user.teamId!),
					),
				)
				.limit(1);
			if (!p) throw new TRPCError({ code: "NOT_FOUND" });
			const slug = slugify(input.name);
			const now = new Date().toISOString();
			const variables = extractVariables(input.content);
			const [row] = await db
				.insert(prompts)
				.values({
					id: `pr-${slug}-${Math.random().toString(36).slice(2, 7)}`,
					productId: input.productId,
					name: input.name.trim(),
					slug,
					content: input.content,
					notes: null,
					variables: variables.length > 0 ? variables : null,
					tags: [],
					version: 1,
					createdAt: now,
					updatedAt: now,
					// biome-ignore lint/suspicious/noExplicitAny: drizzle values() insert requires any cast
				} as any)
				.returning();
			return row;
		}),

	updatePrompt: protectedProcedure
		.input(
			z.object({
				id: z.string(),
				name: z.string().optional(),
				content: z.string().optional(),
				notes: z.string().nullable().optional(),
				tags: z.array(z.string()).optional(),
				bumpVersion: z.boolean().optional(),
			}),
		)
		.mutation(async ({ input, ctx }) => {
			const [existing] = await db
				.select()
				.from(prompts)
				.innerJoin(promptProducts, eq(promptProducts.id, prompts.productId))
				.where(
					and(
						eq(prompts.id, input.id),
						eq(promptProducts.teamId, ctx.user.teamId!),
					),
				)
				.limit(1);
			if (!existing) throw new TRPCError({ code: "NOT_FOUND" });
			const p = existing.prompts;

			// Snapshot the CURRENT row into prompt_versions BEFORE updating
			// when the caller wants to bump the version counter. The bump-only
			// path (no other patch fields) is rare in practice but still
			// preserves the prior state cleanly.
			if (input.bumpVersion) {
				try {
					await db
						.insert(promptVersions)
						.values({
							id: `pv-${p.id}-${p.version}-${Math.random()
								.toString(36)
								.slice(2, 7)}`,
							promptId: p.id,
							version: p.version,
							content: p.content,
							notes: p.notes,
							createdBy: ctx.user.id,
							// biome-ignore lint/suspicious/noExplicitAny: drizzle values() insert requires any cast
						} as any)
						.onConflictDoNothing({
							target: [promptVersions.promptId, promptVersions.version],
						});
				} catch (err) {
					// A duplicate (promptId, version) collision means we've already
					// snapshotted this revision — safe to ignore. Anything else
					// shouldn't block the update.
					console.warn("prompt_versions snapshot failed", err);
				}
			}

			const patch: Record<string, unknown> = {
				updatedAt: new Date().toISOString(),
			};
			if (input.name !== undefined) {
				patch.name = input.name;
				patch.slug = slugify(input.name);
			}
			if (input.content !== undefined) {
				patch.content = input.content;
				const vars = extractVariables(input.content);
				patch.variables = vars.length > 0 ? vars : null;
			}
			if (input.notes !== undefined) patch.notes = input.notes;
			if (input.tags !== undefined) patch.tags = input.tags;
			if (input.bumpVersion) patch.version = p.version + 1;
			const [row] = await db
				.update(prompts)
				// biome-ignore lint/suspicious/noExplicitAny: drizzle set() partial patch requires any cast
				.set(patch as any)
				.where(eq(prompts.id, p.id))
				.returning();
			return row;
		}),

	/**
	 * Returns every prior version of the prompt, newest first. Excludes the
	 * current revision (still on `prompts`) — the UI renders it separately.
	 */
	getVersions: protectedProcedure
		.input(z.object({ promptId: z.string() }))
		.query(async ({ input, ctx }) => {
			// Authorise via team scope: a version is reachable only if the
			// caller can read the parent prompt.
			const [parent] = await db
				.select({ id: prompts.id })
				.from(prompts)
				.innerJoin(promptProducts, eq(promptProducts.id, prompts.productId))
				.where(
					and(
						eq(prompts.id, input.promptId),
						eq(promptProducts.teamId, ctx.user.teamId!),
					),
				)
				.limit(1);
			if (!parent) return [];

			const rows = await db
				.select({
					id: promptVersions.id,
					version: promptVersions.version,
					content: promptVersions.content,
					notes: promptVersions.notes,
					createdAt: promptVersions.createdAt,
					createdBy: promptVersions.createdBy,
				})
				.from(promptVersions)
				.where(eq(promptVersions.promptId, input.promptId))
				.orderBy(desc(promptVersions.version));
			return rows;
		}),

	deletePrompt: protectedProcedure
		.input(z.object({ id: z.string() }))
		.mutation(async ({ input, ctx }) => {
			const [existing] = await db
				.select({ id: prompts.id })
				.from(prompts)
				.innerJoin(promptProducts, eq(promptProducts.id, prompts.productId))
				.where(
					and(
						eq(prompts.id, input.id),
						eq(promptProducts.teamId, ctx.user.teamId!),
					),
				)
				.limit(1);
			if (!existing) throw new TRPCError({ code: "NOT_FOUND" });
			await db.delete(prompts).where(eq(prompts.id, input.id));
			return { ok: true };
		}),

	// ── iter-10 Round F: prompt <-> project linkage ─────────────────────────
	// Canonical relationship per codex amendment #2: FK on prompts (set null
	// on project delete).

	setProject: protectedProcedure
		.input(
			z.object({
				promptId: z.string(),
				projectId: z.string().nullable(),
			}),
		)
		.mutation(async ({ input, ctx }) => {
			if (input.projectId !== null) {
				const [proj] = await db
					.select({ id: projects.id })
					.from(projects)
					.where(
						and(
							eq(projects.id, input.projectId),
							eq(projects.teamId, ctx.user.teamId!),
						),
					)
					.limit(1);
				if (!proj)
					throw new TRPCError({
						code: "BAD_REQUEST",
						message: "Project does not belong to your team",
					});
			}

			const [authz] = await db
				.select({ id: prompts.id })
				.from(prompts)
				.innerJoin(promptProducts, eq(promptProducts.id, prompts.productId))
				.where(
					and(
						eq(prompts.id, input.promptId),
						eq(promptProducts.teamId, ctx.user.teamId!),
					),
				)
				.limit(1);
			if (!authz) throw new TRPCError({ code: "NOT_FOUND" });

			await db
				.update(prompts)
				.set({
					projectId: input.projectId,
					updatedAt: new Date().toISOString(),
					// biome-ignore lint/suspicious/noExplicitAny: drizzle set() literal patch requires any cast
				} as any)
				.where(eq(prompts.id, input.promptId));
			return { ok: true };
		}),
});
