// Todos — quick checklist captures, distinct from project tasks.
//
// Ordering: numeric column. Unchecked items keep their order; when a todo is
// checked, we slot it after the current max order so it lands at the bottom.

import { protectedProcedure, router } from "@api/trpc/init";
import { db } from "@nexus-app/db/client";
import { documents, projects, todoAttachments, todos } from "@nexus-app/db/schema";
import { TRPCError } from "@trpc/server";
import { and, asc, eq, ilike, inArray, sql } from "drizzle-orm";
import { z } from "zod/v3";

const ORDER_STEP = 1000;

export const todosRouter = router({
	get: protectedProcedure
		.input(
			z
				.object({
					projectId: z.string().nullable().optional(),
					tag: z.string().optional(),
					search: z.string().optional(),
					includeChecked: z.boolean().default(true),
				})
				.optional(),
		)
		.query(async ({ input, ctx }) => {
			const filters = [eq(todos.teamId, ctx.user.teamId!)];
			if (input?.projectId !== undefined && input.projectId !== null) {
				filters.push(eq(todos.projectId, input.projectId));
			}
			if (input?.tag) {
				filters.push(sql`${input.tag} = ANY(${todos.tags})`);
			}
			if (input?.search) {
				const s = `%${input.search.replace(/%/g, "\\%")}%`;
				filters.push(ilike(todos.content, s));
			}
			if (input?.includeChecked === false) {
				filters.push(eq(todos.checked, false));
			}

			const rows = await db
				.select({
					id: todos.id,
					content: todos.content,
					projectId: todos.projectId,
					projectName: projects.name,
					projectPrefix: projects.prefix,
					checked: todos.checked,
					checkedAt: todos.checkedAt,
					tags: todos.tags,
					order: todos.order,
					createdAt: todos.createdAt,
					updatedAt: todos.updatedAt,
					attachmentCount:
						sql<number>`(SELECT count(*)::int FROM ${todoAttachments} WHERE ${todoAttachments.todoId} = ${todos.id})`.as(
							"attachment_count",
						),
				})
				.from(todos)
				.leftJoin(projects, eq(projects.id, todos.projectId))
				.where(and(...filters))
				// Checked items first … false … then by order ASC so unchecked
				// items show on top, checked at the bottom.
				.orderBy(asc(todos.checked), asc(todos.order));
			return rows;
		}),

	getById: protectedProcedure
		.input(z.object({ id: z.string() }))
		.query(async ({ input, ctx }) => {
			const [row] = await db
				.select()
				.from(todos)
				.where(and(eq(todos.id, input.id), eq(todos.teamId, ctx.user.teamId!)))
				.limit(1);
			if (!row) return null;
			const attachments = await db
				.select()
				.from(todoAttachments)
				.where(eq(todoAttachments.todoId, row.id))
				.orderBy(asc(todoAttachments.order), asc(todoAttachments.createdAt));
			return { ...row, attachments };
		}),

	create: protectedProcedure
		.input(
			z.object({
				content: z.string().min(1).max(2000),
				projectId: z.string().optional().nullable(),
				tags: z.array(z.string()).optional(),
			}),
		)
		.mutation(async ({ input, ctx }) => {
			// Insert at top: order = min(order) - ORDER_STEP, or just 0 if empty.
			const [minRow] = await db
				.select({
					min: sql<number>`COALESCE(MIN(${todos.order}), ${ORDER_STEP * 2})`,
				})
				.from(todos)
				.where(
					and(eq(todos.teamId, ctx.user.teamId!), eq(todos.checked, false)),
				);
			const order = (minRow?.min ?? ORDER_STEP * 2) - ORDER_STEP;
			const [row] = await db
				.insert(todos)
				.values({
					teamId: ctx.user.teamId!,
					userId: ctx.user.id,
					content: input.content.trim(),
					projectId: input.projectId ?? null,
					tags: input.tags ?? [],
					order,
				})
				.returning();
			return row;
		}),

	update: protectedProcedure
		.input(
			z.object({
				id: z.string(),
				content: z.string().min(1).max(2000).optional(),
				projectId: z.string().nullable().optional(),
				tags: z.array(z.string()).optional(),
			}),
		)
		.mutation(async ({ input, ctx }) => {
			const patch: Record<string, unknown> = {
				updatedAt: new Date().toISOString(),
			};
			if (input.content !== undefined) patch.content = input.content.trim();
			if (input.projectId !== undefined) patch.projectId = input.projectId;
			if (input.tags !== undefined) patch.tags = input.tags;
			const [row] = await db
				.update(todos)
				// biome-ignore lint/suspicious/noExplicitAny: drizzle set() partial patch requires any cast
				.set(patch as any)
				.where(and(eq(todos.id, input.id), eq(todos.teamId, ctx.user.teamId!)))
				.returning();
			if (!row) throw new TRPCError({ code: "NOT_FOUND" });
			return row;
		}),

	check: protectedProcedure
		.input(z.object({ id: z.string() }))
		.mutation(async ({ input, ctx }) => {
			const [maxRow] = await db
				.select({ max: sql<number>`COALESCE(MAX(${todos.order}), 0)` })
				.from(todos)
				.where(eq(todos.teamId, ctx.user.teamId!));
			const newOrder = (maxRow?.max ?? 0) + ORDER_STEP;
			const [row] = await db
				.update(todos)
				.set({
					checked: true,
					checkedAt: new Date().toISOString(),
					order: newOrder,
					updatedAt: new Date().toISOString(),
					// biome-ignore lint/suspicious/noExplicitAny: drizzle set() literal patch requires any cast
				} as any)
				.where(and(eq(todos.id, input.id), eq(todos.teamId, ctx.user.teamId!)))
				.returning();
			if (!row) throw new TRPCError({ code: "NOT_FOUND" });
			return row;
		}),

	uncheck: protectedProcedure
		.input(z.object({ id: z.string() }))
		.mutation(async ({ input, ctx }) => {
			const [minRow] = await db
				.select({
					min: sql<number>`COALESCE(MIN(${todos.order}), ${ORDER_STEP * 2})`,
				})
				.from(todos)
				.where(
					and(eq(todos.teamId, ctx.user.teamId!), eq(todos.checked, false)),
				);
			const newOrder = (minRow?.min ?? ORDER_STEP * 2) - ORDER_STEP;
			const [row] = await db
				.update(todos)
				.set({
					checked: false,
					checkedAt: null,
					order: newOrder,
					updatedAt: new Date().toISOString(),
					// biome-ignore lint/suspicious/noExplicitAny: drizzle set() literal patch requires any cast
				} as any)
				.where(and(eq(todos.id, input.id), eq(todos.teamId, ctx.user.teamId!)))
				.returning();
			if (!row) throw new TRPCError({ code: "NOT_FOUND" });
			return row;
		}),

	reorder: protectedProcedure
		.input(
			z.object({
				orderedIds: z.array(z.string()).min(1),
			}),
		)
		.mutation(async ({ input, ctx }) => {
			// Reassign monotonic order values for the provided ids in the given
			// order. Skip ids that don't belong to the caller's team.
			const existing = await db
				.select({ id: todos.id })
				.from(todos)
				.where(
					and(
						eq(todos.teamId, ctx.user.teamId!),
						inArray(todos.id, input.orderedIds),
					),
				);
			const allowed = new Set(existing.map((r) => r.id));
			const valid = input.orderedIds.filter((id) => allowed.has(id));
			for (let i = 0; i < valid.length; i++) {
				await db
					.update(todos)
					.set({
						order: (i + 1) * ORDER_STEP,
						updatedAt: new Date().toISOString(),
						// biome-ignore lint/suspicious/noExplicitAny: drizzle set() literal patch requires any cast
					} as any)
					.where(eq(todos.id, valid[i]));
			}
			return { reordered: valid.length };
		}),

	delete: protectedProcedure
		.input(z.object({ id: z.string() }))
		.mutation(async ({ input, ctx }) => {
			await db
				.delete(todos)
				.where(and(eq(todos.id, input.id), eq(todos.teamId, ctx.user.teamId!)));
			return { ok: true };
		}),

	// ── Attachments ─────────────────────────────────────────────────────────

	attach: protectedProcedure
		.input(
			z.object({
				todoId: z.string(),
				kind: z.enum(["note", "doc_link"]),
				title: z.string().min(1).max(200),
				content: z.string().optional(),
				docId: z.string().optional(),
			}),
		)
		.mutation(async ({ input, ctx }) => {
			// Verify the todo belongs to this team.
			const [t] = await db
				.select({ id: todos.id })
				.from(todos)
				.where(
					and(eq(todos.id, input.todoId), eq(todos.teamId, ctx.user.teamId!)),
				)
				.limit(1);
			if (!t) throw new TRPCError({ code: "NOT_FOUND" });

			if (input.kind === "doc_link" && input.docId) {
				const [d] = await db
					.select({ id: documents.id })
					.from(documents)
					.where(
						and(
							eq(documents.id, input.docId),
							eq(documents.teamId, ctx.user.teamId!),
						),
					)
					.limit(1);
				if (!d)
					throw new TRPCError({
						code: "BAD_REQUEST",
						message: "doc not found in team",
					});
			}

			const [maxRow] = await db
				.select({
					max: sql<number>`COALESCE(MAX(${todoAttachments.order}), -1)`,
				})
				.from(todoAttachments)
				.where(eq(todoAttachments.todoId, input.todoId));
			const order = (maxRow?.max ?? -1) + 1;

			const [row] = await db
				.insert(todoAttachments)
				.values({
					todoId: input.todoId,
					kind: input.kind,
					title: input.title.trim(),
					content: input.content ?? null,
					docId: input.kind === "doc_link" ? (input.docId ?? null) : null,
					order,
					// biome-ignore lint/suspicious/noExplicitAny: drizzle values() insert patch requires any cast
				} as any)
				.returning();
			return row;
		}),

	detach: protectedProcedure
		.input(z.object({ attachmentId: z.string() }))
		.mutation(async ({ input, ctx }) => {
			// Verify the attachment's parent todo belongs to this team before deleting.
			const [a] = await db
				.select({ id: todoAttachments.id })
				.from(todoAttachments)
				.innerJoin(todos, eq(todos.id, todoAttachments.todoId))
				.where(
					and(
						eq(todoAttachments.id, input.attachmentId),
						eq(todos.teamId, ctx.user.teamId!),
					),
				)
				.limit(1);
			if (!a) throw new TRPCError({ code: "NOT_FOUND" });
			await db
				.delete(todoAttachments)
				.where(eq(todoAttachments.id, input.attachmentId));
			return { ok: true };
		}),

	updateAttachment: protectedProcedure
		.input(
			z.object({
				attachmentId: z.string(),
				title: z.string().min(1).max(200).optional(),
				content: z.string().optional(),
			}),
		)
		.mutation(async ({ input, ctx }) => {
			// Verify the attachment's parent todo belongs to this team before patching.
			const [a] = await db
				.select({ id: todoAttachments.id })
				.from(todoAttachments)
				.innerJoin(todos, eq(todos.id, todoAttachments.todoId))
				.where(
					and(
						eq(todoAttachments.id, input.attachmentId),
						eq(todos.teamId, ctx.user.teamId!),
					),
				)
				.limit(1);
			if (!a) throw new TRPCError({ code: "NOT_FOUND" });
			const patch: Record<string, unknown> = {
				updatedAt: new Date().toISOString(),
			};
			if (input.title !== undefined) patch.title = input.title;
			if (input.content !== undefined) patch.content = input.content;
			const [row] = await db
				.update(todoAttachments)
				// biome-ignore lint/suspicious/noExplicitAny: drizzle set() partial patch requires any cast
				.set(patch as any)
				.where(eq(todoAttachments.id, input.attachmentId))
				.returning();
			return row;
		}),
});
