import {
	createDocumentSchema,
	deleteDocumentSchema,
	getDocumentByIdSchema,
	getDocumentPathSchema,
	getDocumentsSchema,
	reorderDocumentsSchema,
	updateDocumentSchema,
} from "@api/schemas/documents";
import { protectedProcedure, router } from "@api/trpc/init";
import { db } from "@nexus-app/db/client";
import {
	createDocument,
	deleteDocument,
	getDocumentById,
	getDocumentPath,
	getDocuments,
	reorderDocuments,
	updateDocument,
} from "@nexus-app/db/queries/documents";
import { TRPCError } from "@trpc/server";
import { and, asc, eq, sql } from "drizzle-orm";
import { pgTable, text, timestamp } from "drizzle-orm/pg-core";
import { z } from "zod/v3";

// iter-10 Round F: local refs for document subscriptions.
const documentSubscriptions = pgTable("document_subscriptions", {
	userId: text("user_id").notNull(),
	documentId: text("document_id").notNull(),
	subscribedAt: timestamp("subscribed_at", {
		withTimezone: true,
		mode: "string",
	})
		.notNull()
		.defaultNow(),
});

const documentsRef = pgTable("documents", {
	id: text("id").primaryKey(),
	teamId: text("team_id").notNull(),
});

const usersRef = pgTable("user", {
	id: text("id").primaryKey(),
	name: text("name").notNull(),
	email: text("email").notNull(),
	image: text("image"),
});

export const documentsRouter = router({
	get: protectedProcedure
		.input(getDocumentsSchema)
		.query(async ({ ctx, input }) => {
			return getDocuments({
				...input,
				teamId: ctx.user.teamId!,
			});
		}),

	getById: protectedProcedure
		.input(getDocumentByIdSchema)
		.query(async ({ ctx, input }) => {
			return getDocumentById({
				...input,
				teamId: ctx.user.teamId!,
			});
		}),

	getPath: protectedProcedure
		.input(getDocumentPathSchema)
		.query(async ({ ctx, input }) => {
			return getDocumentPath({
				...input,
				teamId: ctx.user.teamId!,
			});
		}),

	create: protectedProcedure
		.input(createDocumentSchema)
		.mutation(async ({ ctx, input }) => {
			return createDocument({
				...input,
				teamId: ctx.user.teamId!,
				createdBy: ctx.user.id,
			});
		}),

	update: protectedProcedure
		.input(updateDocumentSchema)
		.mutation(async ({ ctx, input }) => {
			return updateDocument({
				...input,
				teamId: ctx.user.teamId!,
				updatedBy: ctx.user.id,
			});
		}),

	delete: protectedProcedure
		.input(deleteDocumentSchema)
		.mutation(async ({ ctx, input }) => {
			return deleteDocument({
				...input,
				teamId: ctx.user.teamId!,
			});
		}),

	reorder: protectedProcedure
		.input(reorderDocumentsSchema)
		.mutation(async ({ ctx, input }) => {
			return reorderDocuments({
				items: input.items,
				teamId: ctx.user.teamId!,
			});
		}),

	// ── iter-10 Round F: subscriptions ─────────────────────────────────────

	// Helper: ensure the doc belongs to the caller's team before any
	// subscription mutation. Centralised so the three procs share one
	// authorisation path.
	subscribe: protectedProcedure
		.input(z.object({ documentId: z.string() }))
		.mutation(async ({ ctx, input }) => {
			const [doc] = await db
				.select({ id: documentsRef.id })
				.from(documentsRef)
				.where(
					and(
						eq(documentsRef.id, input.documentId),
						eq(documentsRef.teamId, ctx.user.teamId!),
					),
				)
				.limit(1);
			if (!doc) throw new TRPCError({ code: "NOT_FOUND" });
			await db
				.insert(documentSubscriptions)
				.values({ userId: ctx.user.id, documentId: input.documentId })
				.onConflictDoNothing({
					target: [
						documentSubscriptions.userId,
						documentSubscriptions.documentId,
					],
				});
			return { ok: true };
		}),

	unsubscribe: protectedProcedure
		.input(z.object({ documentId: z.string() }))
		.mutation(async ({ ctx, input }) => {
			await db
				.delete(documentSubscriptions)
				.where(
					and(
						eq(documentSubscriptions.userId, ctx.user.id),
						eq(documentSubscriptions.documentId, input.documentId),
					),
				);
			return { ok: true };
		}),

	// Returns the subscriber list for a document. Used by notification
	// dispatch + the doc-detail subscribers UI.
	listSubscribers: protectedProcedure
		.input(z.object({ documentId: z.string() }))
		.query(async ({ ctx, input }) => {
			// Team-scope guard so a foreign team can't enumerate subscribers
			// of an inaccessible doc.
			const [doc] = await db
				.select({ id: documentsRef.id })
				.from(documentsRef)
				.where(
					and(
						eq(documentsRef.id, input.documentId),
						eq(documentsRef.teamId, ctx.user.teamId!),
					),
				)
				.limit(1);
			if (!doc) return [];

			const rows = await db
				.select({
					userId: usersRef.id,
					name: usersRef.name,
					email: usersRef.email,
					image: usersRef.image,
					subscribedAt: documentSubscriptions.subscribedAt,
				})
				.from(documentSubscriptions)
				.innerJoin(usersRef, eq(documentSubscriptions.userId, usersRef.id))
				.where(eq(documentSubscriptions.documentId, input.documentId))
				.orderBy(asc(documentSubscriptions.subscribedAt));
			return rows;
		}),

	// Whether the current user is subscribed (lightweight check for UI
	// toggle state, avoids fetching the full list).
	isSubscribed: protectedProcedure
		.input(z.object({ documentId: z.string() }))
		.query(async ({ ctx, input }) => {
			const [row] = await db
				.select({ exists: sql<number>`1` })
				.from(documentSubscriptions)
				.where(
					and(
						eq(documentSubscriptions.userId, ctx.user.id),
						eq(documentSubscriptions.documentId, input.documentId),
					),
				)
				.limit(1);
			return Boolean(row);
		}),
});
