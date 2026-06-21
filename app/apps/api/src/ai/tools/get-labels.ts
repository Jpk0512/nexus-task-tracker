import { db } from "@nexus-app/db/client";
import { labels } from "@nexus-app/db/schema";
import { tool } from "ai";
import { and, eq } from "drizzle-orm";
import z from "zod";
import { getToolContext } from "../agents/config/shared";

export const getLabelsToolSchema = z.object({});

export const getLabelsTool = tool({
	description: "Get labels for your tasks",
	inputSchema: getLabelsToolSchema,
	execute: async function* (_input, executionOptions) {
		const { userId: _userId, teamId } = getToolContext(executionOptions);

		yield { text: "Retrieving labels..." };

		const data = await db
			.select({
				id: labels.id,
				name: labels.name,
				description: labels.description,
			})
			.from(labels)
			.where(and(eq(labels.teamId, teamId)));

		yield {
			text: `Found ${data.length} labels.`,
			data,
		};
	},
});
