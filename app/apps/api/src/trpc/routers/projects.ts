import {
	addProjectMemberSchema,
	createProjectSchema,
	getProjectMembersSchema,
	getProjectsSchema,
	removeProjectMemberSchema,
	updateProjectSchema,
} from "@api/schemas/projects";
import { protectedProcedure, router } from "@api/trpc/init";
import { db } from "@mimir/db/client";
import {
	addProjectMember,
	cloneProject,
	createProject,
	deleteProject,
	getProjectById,
	getProjectMembers,
	getProjectProgress,
	getProjects,
	getProjectsForTimeline,
	removeProjectMember,
	updateProject,
} from "@mimir/db/queries/projects";
import { TRPCError } from "@trpc/server";
import { and, desc, eq, inArray, sql } from "drizzle-orm";
import { boolean, pgTable, text, timestamp } from "drizzle-orm/pg-core";
import z from "zod";

// iter-10 Round F: local refs for backlink + pinning queries.
// We mirror only the columns we read so the blast radius stays small.
const projectsRef = pgTable("projects", {
	id: text("id").primaryKey(),
	teamId: text("team_id").notNull(),
	pinned: boolean("pinned").notNull().default(false),
});

const promptsRef = pgTable("prompts", {
	id: text("id").primaryKey(),
	productId: text("product_id").notNull(),
	projectId: text("project_id"),
	name: text("name").notNull(),
	slug: text("slug").notNull(),
	updatedAt: timestamp("updated_at", { withTimezone: true, mode: "string" })
		.notNull()
		.defaultNow(),
});

const promptProductsRef = pgTable("prompt_products", {
	id: text("id").primaryKey(),
	teamId: text("team_id").notNull(),
	slug: text("slug").notNull(),
});

const tasksRef = pgTable("tasks", {
	id: text("id").primaryKey(),
	projectId: text("project_id"),
	teamId: text("team_id").notNull(),
});

const knowledgeNotesOnTasksRef = pgTable("knowledge_notes_on_tasks", {
	id: text("id").primaryKey(),
	taskId: text("task_id").notNull(),
	noteId: text("note_id").notNull(),
	createdAt: timestamp("created_at", { withTimezone: true, mode: "string" })
		.notNull()
		.defaultNow(),
});

const knowledgeNotesRef = pgTable("knowledge_notes", {
	id: text("id").primaryKey(),
	vaultId: text("vault_id").notNull(),
	name: text("name").notNull(),
	parentDir: text("parent_dir"),
	relativePath: text("relative_path").notNull(),
	lastEditedAt: timestamp("last_edited_at", {
		withTimezone: true,
		mode: "string",
	}),
});

export const projectsRouter = router({
	get: protectedProcedure
		.input(getProjectsSchema.optional())
		.query(async ({ ctx, input }) => {
			return getProjects({
				...input,
				teamId: ctx.user.teamId,
				userId: ctx.user.id,
			});
		}),

	getForTimeline: protectedProcedure.query(async ({ ctx }) => {
		return getProjectsForTimeline({
			teamId: ctx.user.teamId,
			userId: ctx.user.id,
		});
	}),

	create: protectedProcedure
		.input(createProjectSchema)
		.mutation(async ({ ctx, input }) => {
			return createProject({
				...input,
				teamId: ctx.user.teamId,
				userId: ctx.user.id,
			});
		}),

	getById: protectedProcedure
		.input(z.object({ id: z.string() }))
		.query(async ({ ctx, input }) => {
			return getProjectById({
				projectId: input.id,
				teamId: ctx.user.teamId,
				userId: ctx.user.id,
			});
		}),

	update: protectedProcedure
		.input(updateProjectSchema)
		.mutation(async ({ ctx, input }) => {
			return updateProject({
				...input,
				teamId: ctx.user.teamId,
			});
		}),

	delete: protectedProcedure
		.input(z.object({ id: z.string() }))
		.mutation(async ({ ctx, input }) => {
			return deleteProject({
				projectId: input.id,
				teamId: ctx.user.teamId,
			});
		}),

	getProgress: protectedProcedure
		.input(z.object({ id: z.string() }))
		.query(async ({ ctx, input }) => {
			return getProjectProgress({
				projectId: input.id,
				teamId: ctx.user.teamId,
			});
		}),

	clone: protectedProcedure
		.input(z.object({ id: z.string() }))
		.mutation(async ({ ctx, input }) => {
			return cloneProject({
				projectId: input.id,
				teamId: ctx.user.teamId,
				userId: ctx.user.id,
			});
		}),

	// Project members management
	addMember: protectedProcedure
		.input(addProjectMemberSchema)
		.mutation(async ({ ctx, input }) => {
			return addProjectMember({
				projectId: input.projectId,
				userId: input.userId,
				teamId: ctx.user.teamId,
			});
		}),

	removeMember: protectedProcedure
		.input(removeProjectMemberSchema)
		.mutation(async ({ ctx, input }) => {
			return removeProjectMember({
				projectId: input.projectId,
				userId: input.userId,
				teamId: ctx.user.teamId,
			});
		}),

	getMembers: protectedProcedure
		.input(getProjectMembersSchema)
		.query(async ({ ctx, input }) => {
			return getProjectMembers({
				projectId: input.projectId,
				teamId: ctx.user.teamId,
			});
		}),

	// ── iter-10 Round F: backlinks + pinning ────────────────────────────────

	// Toggle the pinned flag. Replaces the iter-7 localStorage hook. The
	// localStorage path is kept on the client as an offline fallback.
	setPinned: protectedProcedure
		.input(z.object({ projectId: z.string(), pinned: z.boolean() }))
		.mutation(async ({ ctx, input }) => {
			const [authz] = await db
				.select({ id: projectsRef.id })
				.from(projectsRef)
				.where(
					and(
						eq(projectsRef.id, input.projectId),
						eq(projectsRef.teamId, ctx.user.teamId!),
					),
				)
				.limit(1);
			if (!authz) throw new TRPCError({ code: "NOT_FOUND" });

			await db
				.update(projectsRef)
				.set({ pinned: input.pinned })
				.where(eq(projectsRef.id, input.projectId));
			return { ok: true, pinned: input.pinned };
		}),

	// All pinned projects for the current team. Used by client to hydrate
	// the localStorage fallback on login (server is source of truth).
	listPinned: protectedProcedure.query(async ({ ctx }) => {
		const rows = await db
			.select({ id: projectsRef.id })
			.from(projectsRef)
			.where(
				and(
					eq(projectsRef.teamId, ctx.user.teamId!),
					eq(projectsRef.pinned, true),
				),
			);
		return rows.map((r) => r.id);
	}),

	// Prompts linked to a given project (codex amendment #2 forward direction).
	// Relevance: most-recently updated first. Cap at 50 per perf budget.
	listLinkedPrompts: protectedProcedure
		.input(
			z.object({
				projectId: z.string(),
				limit: z.number().int().min(1).max(50).default(50),
			}),
		)
		.query(async ({ input, ctx }) => {
			const rows = await db
				.select({
					id: promptsRef.id,
					name: promptsRef.name,
					slug: promptsRef.slug,
					productSlug: promptProductsRef.slug,
					updatedAt: promptsRef.updatedAt,
				})
				.from(promptsRef)
				.innerJoin(
					promptProductsRef,
					eq(promptProductsRef.id, promptsRef.productId),
				)
				.where(
					and(
						eq(promptsRef.projectId, input.projectId),
						eq(promptProductsRef.teamId, ctx.user.teamId!),
					),
				)
				.orderBy(desc(promptsRef.updatedAt))
				.limit(input.limit);
			return rows;
		}),

	// Knowledge notes connected to a project via the task chain
	// (a project's tasks' linked notes). De-duped at the SQL layer.
	listLinkedKnowledge: protectedProcedure
		.input(
			z.object({
				projectId: z.string(),
				limit: z.number().int().min(1).max(50).default(50),
			}),
		)
		.query(async ({ input, ctx }) => {
			const taskRows = await db
				.select({ id: tasksRef.id })
				.from(tasksRef)
				.where(
					and(
						eq(tasksRef.projectId, input.projectId),
						eq(tasksRef.teamId, ctx.user.teamId!),
					),
				);
			if (taskRows.length === 0) return [];

			const taskIds = taskRows.map((t) => t.id);
			// Pick the most-recent link per note (DISTINCT ON) for ranking,
			// then order by link-recency, fall back to note last-edited.
			const rows = await db
				.select({
					id: knowledgeNotesRef.id,
					name: knowledgeNotesRef.name,
					parentDir: knowledgeNotesRef.parentDir,
					vaultId: knowledgeNotesRef.vaultId,
					relativePath: knowledgeNotesRef.relativePath,
					lastEditedAt: knowledgeNotesRef.lastEditedAt,
					latestLinkedAt:
						sql<string>`max(${knowledgeNotesOnTasksRef.createdAt})`.as(
							"latest_linked_at",
						),
				})
				.from(knowledgeNotesOnTasksRef)
				.innerJoin(
					knowledgeNotesRef,
					eq(knowledgeNotesOnTasksRef.noteId, knowledgeNotesRef.id),
				)
				.where(inArray(knowledgeNotesOnTasksRef.taskId, taskIds))
				.groupBy(
					knowledgeNotesRef.id,
					knowledgeNotesRef.name,
					knowledgeNotesRef.parentDir,
					knowledgeNotesRef.vaultId,
					knowledgeNotesRef.relativePath,
					knowledgeNotesRef.lastEditedAt,
				)
				.orderBy(
					sql`max(${knowledgeNotesOnTasksRef.createdAt}) DESC`,
					desc(knowledgeNotesRef.lastEditedAt),
				)
				.limit(input.limit);
			return rows;
		}),
});
