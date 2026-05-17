import { randomUUID } from "node:crypto";
import {
	bulkDeleteTaskSchema,
	bulkUpdateTaskSchema,
	cloneTaskSchema,
	commentTaskSchema,
	createTaskSchema,
	deleteTaskCommentSchema,
	deleteTaskSchema,
	getDuplicatedTasksSchema,
	getTaskSubscribersSchema,
	getTasksSchema,
	smartCompleteSchema,
	subscribeTaskSchema,
	unsubscribeTaskSchema,
	updateTaskCommentSchema,
	updateTaskSchema,
} from "@api/schemas/tasks";
import { protectedProcedure, router } from "@api/trpc/init";
import { buildSmartCompletePrompt } from "@api/utils/smart-complete";
import { db } from "@mimir/db/client";
import {
	bulkDeleteTask,
	bulkUpdateTask,
	cloneTask,
	createTask,
	createTaskComment,
	deleteTask,
	deleteTaskComment,
	getTaskById,
	getTaskByPermalinkId,
	getTaskSubscribers,
	getTasks,
	subscribeUserToTask,
	unsubscribeUserFromTask,
	updateTask,
	updateTaskComment,
	updateTaskDescription,
} from "@mimir/db/queries/tasks";
import { getDuplicateTaskEmbedding } from "@mimir/db/queries/tasks-embeddings";
import { getMemberById } from "@mimir/db/queries/teams";
import { trackTaskCreated } from "@mimir/events/server";
import { syncGoogleCalendarTaskEvent } from "@mimir/integration/google-calendar";
import { syncRecurringTaskSchedule } from "@mimir/jobs/tasks/create-recurring-task-job";
import { and, desc, eq } from "drizzle-orm";
import { pgTable, text, timestamp } from "drizzle-orm/pg-core";
import z from "zod";

// Inline pgTable definitions for the new task-link join tables. The raw SQL
// DDL was applied directly via psql; we mirror just the columns we need here
// instead of editing the shared schema.ts (keeps blast radius small per
// iter4-#5 directive).
const documentsOnTasks = pgTable("documents_on_tasks", {
	id: text("id").primaryKey(),
	taskId: text("task_id").notNull(),
	documentId: text("document_id").notNull(),
	createdAt: timestamp("created_at", { withTimezone: true, mode: "string" })
		.notNull()
		.defaultNow(),
	createdBy: text("created_by"),
});

const knowledgeNotesOnTasks = pgTable("knowledge_notes_on_tasks", {
	id: text("id").primaryKey(),
	taskId: text("task_id").notNull(),
	noteId: text("note_id").notNull(),
	createdAt: timestamp("created_at", { withTimezone: true, mode: "string" })
		.notNull()
		.defaultNow(),
	createdBy: text("created_by"),
});

// Mirror columns from documents/knowledge_notes that we need for joins.
const documentsRef = pgTable("documents", {
	id: text("id").primaryKey(),
	name: text("name").notNull(),
	icon: text("icon"),
	projectId: text("project_id"),
	teamId: text("team_id").notNull(),
});

const knowledgeNotesRef = pgTable("knowledge_notes", {
	id: text("id").primaryKey(),
	vaultId: text("vault_id").notNull(),
	relativePath: text("relative_path").notNull(),
	name: text("name").notNull(),
	parentDir: text("parent_dir"),
});

export const tasksRouter = router({
	get: protectedProcedure
		.input(getTasksSchema.optional())
		.query(({ ctx, input }) => {
			return getTasks({
				pageSize: 100,
				...input,
				teamId: ctx.user.teamId!,
				userId: ctx.user.id,
			});
		}),
	create: protectedProcedure
		.input(createTaskSchema)
		.mutation(async ({ ctx, input }) => {
			const task = await createTask({
				...input,
				userId: ctx.user.id,
				teamId: ctx.user.teamId!,
			});

			trackTaskCreated({
				userId: ctx.user.id,
				teamId: ctx.team.id,
				teamName: ctx.team.name,
				source: "api",
			});

			// If recurring is set, schedule the first occurrence
			if (task.recurring) {
				await syncRecurringTaskSchedule({
					taskId: task.id,
					recurringCron: task.recurring,
				});
			}

			if (task.dueDate && !task.isTemplate) {
				// Sync the calendar event
				syncGoogleCalendarTaskEvent({
					taskId: task.id,
					teamId: ctx.user.teamId!,
				});
			}

			return task;
		}),

	clone: protectedProcedure
		.input(cloneTaskSchema)
		.mutation(async ({ ctx, input }) => {
			return cloneTask({
				taskId: input.taskId,
				userId: ctx.user.id,
				teamId: ctx.user.teamId!,
			});
		}),

	update: protectedProcedure
		.input(updateTaskSchema)
		.mutation(async ({ ctx, input }) => {
			const oldTask = await getTaskById(input.id, ctx.user.id);
			const task = await updateTask({
				...input,
				userId: ctx.user.id,
				teamId: ctx.user.teamId!,
			});

			const recurringChanged = oldTask.recurring !== task.recurring;
			const missingRecurringJob =
				Boolean(task.recurring) && !oldTask.recurringJobId;

			if (recurringChanged || missingRecurringJob) {
				await syncRecurringTaskSchedule({
					taskId: task.id,
					recurringCron: task.recurring ?? null,
					previousJobId: oldTask.recurringJobId,
				});
			}

			if (
				!task.isTemplate &&
				(oldTask.dueDate !== task.dueDate ||
					oldTask.subscribers !== task.subscribers)
			) {
				// Due date or subscribers changed, sync the calendar event
				syncGoogleCalendarTaskEvent({
					taskId: task.id,
					teamId: ctx.user.teamId!,
					oldSubscribers: oldTask.subscribers,
				});
			}

			return task;
		}),
	updateDescription: protectedProcedure
		.input(
			updateTaskSchema.pick({
				id: true,
				description: true,
			}),
		)
		.mutation(async ({ ctx, input }) => {
			return updateTaskDescription({
				...input,
				userId: ctx.user.id,
				teamId: ctx.user.teamId!,
			});
		}),

	bulkUpdate: protectedProcedure
		.input(bulkUpdateTaskSchema)
		.mutation(async ({ ctx, input }) => {
			return bulkUpdateTask({
				...input,
				userId: ctx.user.id,
				teamId: ctx.user.teamId!,
			});
		}),
	bulkDelete: protectedProcedure
		.input(bulkDeleteTaskSchema)
		.mutation(async ({ ctx, input }) => {
			return bulkDeleteTask({
				...input,
				teamId: ctx.user.teamId!,
			});
		}),
	delete: protectedProcedure
		.input(deleteTaskSchema.omit({ teamId: true }))
		.mutation(async ({ ctx, input }) => {
			return deleteTask({
				...input,
				teamId: ctx.user.teamId!,
			});
		}),
	getById: protectedProcedure
		.input(updateTaskSchema.pick({ id: true }))
		.query(async ({ ctx, input }) => {
			return getTaskById(input.id, ctx.user.id);
		}),
	getByPermalinkId: protectedProcedure
		.input(z.object({ permalinkId: z.string() }))
		.query(async ({ ctx, input }) => {
			return getTaskByPermalinkId(input.permalinkId, ctx.user.id);
		}),

	comment: protectedProcedure
		.input(commentTaskSchema)
		.mutation(async ({ ctx, input }) => {
			const comment = await createTaskComment({
				taskId: input.id,
				comment: input.comment,
				replyTo: input.replyTo,
				userId: ctx.user.id,
				teamId: ctx.user.teamId!,
				mentions: input.mentions,
				metadata: input.metadata,
			});

			// Try to handle the comment with AI integration
			// handleTaskComment({
			// 	taskId: input.id,
			// 	teamId: ctx.user.teamId!,
			// 	userId: ctx.user.id,
			// 	commentId: comment.id,
			// 	comment: input.comment,
			// });

			return comment;
		}),
	deleteComment: protectedProcedure
		.input(deleteTaskCommentSchema)
		.mutation(async ({ ctx, input }) => {
			return deleteTaskComment({
				commentId: input.id,
				teamId: ctx.user.teamId!,
			});
		}),
	updateComment: protectedProcedure
		.input(updateTaskCommentSchema)
		.mutation(async ({ ctx, input }) => {
			return updateTaskComment({
				...input,
				commentId: input.id,
				taskId: input.taskId,
				userId: ctx.user.id,
				teamId: ctx.user.teamId!,
			});
		}),

	getDuplicates: protectedProcedure
		.input(getDuplicatedTasksSchema)
		.query(async ({ ctx, input }) => {
			return getDuplicateTaskEmbedding({
				task: input,
				teamId: ctx.user.teamId!,
			});
		}),

	smartComplete: protectedProcedure
		.input(smartCompleteSchema)
		.mutation(async ({ input, ctx }) => {
			const systemPrompt = await buildSmartCompletePrompt({
				userPrompt: input.prompt,
				userId: ctx.user.id,
				teamId: ctx.user.teamId!,
			});

			return {};

			// const response = await generateText({
			// 	system: systemPrompt,
			// 	model: openai("gpt-4o-mini"),
			// 	output: Output.object({ schema: smartCompleteResponseSchema }),
			// 	prompt: `Create a task based on the user's prompt: "${input.prompt}"`,
			// });

			// const meter = createTokenMeter(ctx.team.customerId!);
			// meter({
			// 	model: "openai/gpt-4o-mini",
			// 	usage: response.usage,
			// });

			// trackMessage({
			// 	userId: ctx.user.id,
			// 	teamId: ctx.user.teamId!,
			// 	source: "smart-complete",
			// });

			// return response.output;
		}),

	getSubscribers: protectedProcedure
		.input(getTaskSubscribersSchema)
		.query(async ({ ctx, input }) => {
			return await getTaskSubscribers({
				taskId: input.id,
				teamId: ctx.user.teamId!,
			});
		}),

	unsubscribe: protectedProcedure
		.input(unsubscribeTaskSchema)
		.mutation(async ({ ctx, input }) => {
			return await unsubscribeUserFromTask({
				taskId: input.id,
				userId: ctx.user.id,
				teamId: ctx.user.teamId!,
			});
		}),

	subscribe: protectedProcedure
		.input(subscribeTaskSchema)
		.mutation(async ({ ctx, input }) => {
			const userOnTeam = await getMemberById({
				userId: input.userId,
				teamId: ctx.user.teamId!,
			});

			if (!userOnTeam) {
				throw new Error("User not found on team");
			}

			return await subscribeUserToTask({
				taskId: input.id,
				userId: input.userId,
				teamId: ctx.user.teamId!,
			});
		}),

	// --- Task ↔ Document join (iter4 fix #5) ---
	attachDocument: protectedProcedure
		.input(z.object({ taskId: z.string(), documentId: z.string() }))
		.mutation(async ({ ctx, input }) => {
			const [row] = await db
				.insert(documentsOnTasks)
				.values({
					id: randomUUID(),
					taskId: input.taskId,
					documentId: input.documentId,
					createdBy: ctx.user.id,
				})
				.onConflictDoNothing({
					target: [documentsOnTasks.taskId, documentsOnTasks.documentId],
				})
				.returning();
			return row ?? null;
		}),

	detachDocument: protectedProcedure
		.input(z.object({ taskId: z.string(), documentId: z.string() }))
		.mutation(async ({ input }) => {
			await db
				.delete(documentsOnTasks)
				.where(
					and(
						eq(documentsOnTasks.taskId, input.taskId),
						eq(documentsOnTasks.documentId, input.documentId),
					),
				);
			return { ok: true };
		}),

	getLinkedDocuments: protectedProcedure
		.input(z.object({ taskId: z.string() }))
		.query(async ({ input }) => {
			const rows = await db
				.select({
					id: documentsRef.id,
					name: documentsRef.name,
					icon: documentsRef.icon,
					projectId: documentsRef.projectId,
					linkId: documentsOnTasks.id,
					linkedAt: documentsOnTasks.createdAt,
				})
				.from(documentsOnTasks)
				.innerJoin(
					documentsRef,
					eq(documentsOnTasks.documentId, documentsRef.id),
				)
				.where(eq(documentsOnTasks.taskId, input.taskId))
				.orderBy(desc(documentsOnTasks.createdAt));
			return rows;
		}),

	// --- Task ↔ Knowledge note join (iter4 fix #5) ---
	attachKnowledgeNote: protectedProcedure
		.input(z.object({ taskId: z.string(), noteId: z.string() }))
		.mutation(async ({ ctx, input }) => {
			const [row] = await db
				.insert(knowledgeNotesOnTasks)
				.values({
					id: randomUUID(),
					taskId: input.taskId,
					noteId: input.noteId,
					createdBy: ctx.user.id,
				})
				.onConflictDoNothing({
					target: [knowledgeNotesOnTasks.taskId, knowledgeNotesOnTasks.noteId],
				})
				.returning();
			return row ?? null;
		}),

	detachKnowledgeNote: protectedProcedure
		.input(z.object({ taskId: z.string(), noteId: z.string() }))
		.mutation(async ({ input }) => {
			await db
				.delete(knowledgeNotesOnTasks)
				.where(
					and(
						eq(knowledgeNotesOnTasks.taskId, input.taskId),
						eq(knowledgeNotesOnTasks.noteId, input.noteId),
					),
				);
			return { ok: true };
		}),

	getLinkedKnowledgeNotes: protectedProcedure
		.input(z.object({ taskId: z.string() }))
		.query(async ({ input }) => {
			const rows = await db
				.select({
					id: knowledgeNotesRef.id,
					name: knowledgeNotesRef.name,
					relativePath: knowledgeNotesRef.relativePath,
					parentDir: knowledgeNotesRef.parentDir,
					vaultId: knowledgeNotesRef.vaultId,
					linkId: knowledgeNotesOnTasks.id,
					linkedAt: knowledgeNotesOnTasks.createdAt,
				})
				.from(knowledgeNotesOnTasks)
				.innerJoin(
					knowledgeNotesRef,
					eq(knowledgeNotesOnTasks.noteId, knowledgeNotesRef.id),
				)
				.where(eq(knowledgeNotesOnTasks.taskId, input.taskId))
				.orderBy(desc(knowledgeNotesOnTasks.createdAt));
			return rows;
		}),
});
