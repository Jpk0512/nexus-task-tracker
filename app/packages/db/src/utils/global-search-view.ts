import { sql } from "drizzle-orm";
import { pgTable, pgView, QueryBuilder, text } from "drizzle-orm/pg-core";
import {
	documents,
	knowledgeNotes,
	knowledgeVaults,
	libraryEntries,
	librarySources,
	milestones,
	projects,
	tasks,
	todos,
} from "../schema";

const prompts = pgTable("prompts", {
	id: text("id").primaryKey(),
	productId: text("product_id").notNull(),
	name: text("name").notNull(),
	slug: text("slug").notNull(),
});

const promptProducts = pgTable("prompt_products", {
	id: text("id").primaryKey(),
	teamId: text("team_id").notNull(),
	slug: text("slug").notNull(),
});

export const buildGlobalSearchView = () => {
	const qb = new QueryBuilder();

	const queries = [
		// Tasks
		qb
			.select({
				id: tasks.id,
				type: sql<string>`'task'`.as("type"),
				title: tasks.title,
				color: sql<string>`NULL`.as("color"),
				parent_id: sql<string>`NULL`.as("parent_id"),
				team_id: tasks.teamId,
			})
			.from(tasks),

		// Projects
		qb
			.select({
				id: projects.id,
				type: sql<string>`'project'`.as("type"),
				title: projects.name,
				color: projects.color,
				parent_id: sql<string>`NULL`.as("parent_id"),
				team_id: projects.teamId,
			})
			.from(projects),

		// Milestones
		qb
			.select({
				id: milestones.id,
				type: sql<string>`'milestone'`.as("type"),
				title: milestones.name,
				color: milestones.color,
				parent_id: milestones.projectId,
				team_id: milestones.teamId,
			})
			.from(milestones),

		// Documents — parent_id carries projectId (nullable, "in project" hint).
		qb
			.select({
				id: documents.id,
				type: sql<string>`'document'`.as("type"),
				title: documents.name,
				color: sql<string>`NULL`.as("color"),
				parent_id: documents.projectId,
				team_id: documents.teamId,
			})
			.from(documents),

		// Todos — title from content; parent_id = projectId.
		qb
			.select({
				id: todos.id,
				type: sql<string>`'todo'`.as("type"),
				title: todos.content,
				color: sql<string>`NULL`.as("color"),
				parent_id: todos.projectId,
				team_id: todos.teamId,
			})
			.from(todos),

		// Knowledge notes — team_id pulled through the parent vault.
		qb
			.select({
				id: knowledgeNotes.id,
				type: sql<string>`'knowledge'`.as("type"),
				title: knowledgeNotes.name,
				color: sql<string>`NULL`.as("color"),
				parent_id: knowledgeNotes.relativePath,
				team_id: knowledgeVaults.teamId,
			})
			.from(knowledgeNotes)
			.innerJoin(
				knowledgeVaults,
				sql`${knowledgeNotes.vaultId} = ${knowledgeVaults.id}`,
			),

		// Library entries — team_id pulled through library_sources.
		qb
			.select({
				id: libraryEntries.id,
				type: sql<string>`'library'`.as("type"),
				title: libraryEntries.name,
				color: sql<string>`NULL`.as("color"),
				parent_id: sql<string>`${libraryEntries.kind}::text`.as("parent_id"),
				team_id: librarySources.teamId,
			})
			.from(libraryEntries)
			.innerJoin(
				librarySources,
				sql`${libraryEntries.sourceId} = ${librarySources.id}`,
			),

		// Prompts — parent_id encodes "productSlug:promptSlug" so the result
		// item can navigate to /prompts/[productSlug]/[promptSlug] without an
		// extra round trip.
		qb
			.select({
				id: prompts.id,
				type: sql<string>`'prompt'`.as("type"),
				title: prompts.name,
				color: sql<string>`NULL`.as("color"),
				parent_id:
					sql<string>`${promptProducts.slug} || ':' || ${prompts.slug}`.as(
						"parent_id",
					),
				team_id: promptProducts.teamId,
			})
			.from(prompts)
			.innerJoin(
				promptProducts,
				sql`${prompts.productId} = ${promptProducts.id}`,
			),
	];

	let union: any = queries[0]!;
	for (let i = 1; i < queries.length; i++) {
		union = union.unionAll(queries[i]!);
	}

	return pgView("global_search_view_v7").as(union);
};
