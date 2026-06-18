// References — universal backlinks aggregator. Given (entityType, entityId),
// return the entities that point AT this one across the join tables we have
// today. Kept deliberately simple: if a join table doesn't apply to a given
// entity type, we just return an empty array for that slot instead of
// throwing — the UI shouldn't 500 when relations don't exist for a given
// surface.

import { protectedProcedure, router } from "@api/trpc/init";
import { db } from "@mimir/db/client";
import {
	documents,
	documentsOnTasks,
	inbox,
	intakes,
	knowledgeNotesOnTasks,
	projects,
	taskSkills,
	tasks as tasksTable,
	todoAttachments,
	todos,
} from "@mimir/db/schema";
import { and, desc, eq, inArray } from "drizzle-orm";
import { z } from "zod";

const entityTypeSchema = z.enum([
	"task",
	"document",
	"knowledge",
	"library",
	"prompt",
	"todo",
]);

// Shape returned to the UI. Each list item is a thin, link-renderable card —
// just enough to show the user "what is pointing at me" and bounce there.
type ReferenceTodo = {
	id: string;
	content: string;
	projectId: string | null;
	projectName: string | null;
	checked: boolean;
};

type ReferenceTask = {
	id: string;
	permalinkId: string;
	title: string;
	projectId: string | null;
	projectName: string | null;
	statusId: string;
};

type ReferenceDocument = {
	id: string;
	name: string;
	icon: string | null;
	projectId: string | null;
	parentId: string | null;
};

type ReferenceInbox = {
	id: string;
	display: string;
	subtitle: string | null;
	source: string;
};

type ReferencesPayload = {
	todos: ReferenceTodo[];
	tasks: ReferenceTask[];
	documents: ReferenceDocument[];
	inbox: ReferenceInbox[];
};

const EMPTY: ReferencesPayload = {
	todos: [],
	tasks: [],
	documents: [],
	inbox: [],
};

async function listForDocument(
	teamId: string,
	docId: string,
): Promise<ReferencesPayload> {
	// 1. Todos that attached to this doc via todoAttachments.docId.
	const attachmentRows = await db
		.select({
			todoId: todoAttachments.todoId,
		})
		.from(todoAttachments)
		.where(eq(todoAttachments.docId, docId));

	const todoIds = Array.from(new Set(attachmentRows.map((r) => r.todoId)));

	const todoRows = todoIds.length
		? await db
				.select({
					id: todos.id,
					content: todos.content,
					projectId: todos.projectId,
					checked: todos.checked,
					projectName: projects.name,
				})
				.from(todos)
				.leftJoin(projects, eq(projects.id, todos.projectId))
				.where(and(inArray(todos.id, todoIds), eq(todos.teamId, teamId)))
		: [];

	return {
		...EMPTY,
		todos: todoRows.map((r) => ({
			id: r.id,
			content: r.content,
			projectId: r.projectId,
			projectName: r.projectName ?? null,
			checked: r.checked,
		})),
	};
}

async function listForTask(
	teamId: string,
	taskId: string,
): Promise<ReferencesPayload> {
	// 1. Inbox notifications whose source pointed at this task.
	const inboxRows = await db
		.select({
			id: inbox.id,
			display: inbox.display,
			subtitle: inbox.subtitle,
			source: inbox.source,
		})
		.from(inbox)
		.where(and(eq(inbox.teamId, teamId), eq(inbox.sourceId, taskId)));

	// 2. Intakes that produced this task.
	const intakeRows = await db
		.select({
			id: intakes.id,
			source: intakes.source,
			sourceId: intakes.sourceId,
			reasoning: intakes.reasoning,
		})
		.from(intakes)
		.where(and(eq(intakes.teamId, teamId), eq(intakes.taskId, taskId)));

	// Render intakes as a flavor of inbox row so the UI can show them under
	// "Inbox / Intake notifications" without a separate column.
	const intakeAsInbox: ReferenceInbox[] = intakeRows.map((r) => ({
		id: r.id,
		display: `Intake from ${r.source}`,
		subtitle: r.reasoning ?? r.sourceId,
		source: `intake:${r.source}`,
	}));

	return {
		...EMPTY,
		inbox: [...inboxRows, ...intakeAsInbox],
	};
}

async function listForTodo(
	teamId: string,
	todoId: string,
): Promise<ReferencesPayload> {
	// A todo's own attachments point at documents. While the "backlinks" framing
	// is reverse-direction, surfacing forward-direction doc links here is the
	// natural complement and the user expects the same panel on every detail
	// page to be useful, not blank.
	const attachmentRows = await db
		.select({
			docId: todoAttachments.docId,
		})
		.from(todoAttachments)
		.where(eq(todoAttachments.todoId, todoId));

	const docIds = Array.from(
		new Set(
			attachmentRows.map((r) => r.docId).filter((x): x is string => Boolean(x)),
		),
	);

	const docRows = docIds.length
		? await db
				.select({
					id: documents.id,
					name: documents.name,
					icon: documents.icon,
					projectId: documents.projectId,
					parentId: documents.parentId,
				})
				.from(documents)
				.where(and(inArray(documents.id, docIds), eq(documents.teamId, teamId)))
		: [];

	return {
		...EMPTY,
		documents: docRows,
	};
}

// Knowledge notes and library skills now have join-table adjacency via
// knowledge_notes_on_tasks + task_skills (iter-10 Round F). Surface the
// tasks pointing at them so detail pages show real backlinks instead of an
// empty state.
async function listForKnowledge(
	teamId: string,
	noteId: string,
): Promise<ReferencesPayload> {
	const rows = await db
		.select({
			id: tasksTable.id,
			permalinkId: tasksTable.permalinkId,
			title: tasksTable.title,
			projectId: tasksTable.projectId,
			statusId: tasksTable.statusId,
			projectName: projects.name,
			linkedAt: knowledgeNotesOnTasks.createdAt,
		})
		.from(knowledgeNotesOnTasks)
		.innerJoin(tasksTable, eq(knowledgeNotesOnTasks.taskId, tasksTable.id))
		.leftJoin(projects, eq(projects.id, tasksTable.projectId))
		.where(
			and(
				eq(knowledgeNotesOnTasks.noteId, noteId),
				eq(tasksTable.teamId, teamId),
			),
		)
		.orderBy(desc(knowledgeNotesOnTasks.createdAt))
		.limit(50);

	return {
		...EMPTY,
		tasks: rows.map((r) => ({
			id: r.id,
			permalinkId: r.permalinkId,
			title: r.title,
			projectId: r.projectId,
			projectName: r.projectName ?? null,
			statusId: r.statusId,
		})),
	};
}

async function listForLibrary(
	teamId: string,
	skillId: string,
): Promise<ReferencesPayload> {
	const rows = await db
		.select({
			id: tasksTable.id,
			permalinkId: tasksTable.permalinkId,
			title: tasksTable.title,
			projectId: tasksTable.projectId,
			statusId: tasksTable.statusId,
			projectName: projects.name,
		})
		.from(taskSkills)
		.innerJoin(tasksTable, eq(taskSkills.taskId, tasksTable.id))
		.leftJoin(projects, eq(projects.id, tasksTable.projectId))
		.where(and(eq(taskSkills.skillId, skillId), eq(tasksTable.teamId, teamId)))
		.orderBy(desc(taskSkills.createdAt))
		.limit(50);

	return {
		...EMPTY,
		tasks: rows.map((r) => ({
			id: r.id,
			permalinkId: r.permalinkId,
			title: r.title,
			projectId: r.projectId,
			projectName: r.projectName ?? null,
			statusId: r.statusId,
		})),
	};
}

// Prompts have a forward FK to projects (set at write-time) but no
// reverse-direction join from another entity today. Empty payload keeps
// the UI happy until a use case emerges.
async function listForPrompt(): Promise<ReferencesPayload> {
	return EMPTY;
}

export const referencesRouter = router({
	list: protectedProcedure
		.input(
			z.object({
				entityType: entityTypeSchema,
				entityId: z.string().min(1),
			}),
		)
		.query(async ({ ctx, input }): Promise<ReferencesPayload> => {
			const teamId = ctx.user.teamId;
			if (!teamId) return EMPTY;

			try {
				switch (input.entityType) {
					case "document":
						return await listForDocument(teamId, input.entityId);
					case "task":
						return await listForTask(teamId, input.entityId);
					case "todo":
						return await listForTodo(teamId, input.entityId);
					case "knowledge":
						return await listForKnowledge(teamId, input.entityId);
					case "library":
						return await listForLibrary(teamId, input.entityId);
					case "prompt":
						return await listForPrompt();
					default:
						return EMPTY;
				}
			} catch (err) {
				// Defensive: returning empty is strictly better than 500ing the
				// backlinks panel on every detail page if a relation goes sideways.
				console.error("[references.list] failed", err);
				return EMPTY;
			}
		}),
});
