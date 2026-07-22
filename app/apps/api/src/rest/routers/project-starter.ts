import { createOpenAI } from "@ai-sdk/openai";
import { createAgent } from "@api/ai/agents/config/agent";
import { type AppContext, buildAppContext } from "@api/ai/agents/config/shared";
import {
	buildProjectStarterSystemPrompt,
	type StarterSeed,
} from "@api/ai/agents/project-starter";
import { chatResumableStreamContext } from "@api/ai/chat-stream-context";
import { finalizeStarterPrdTool } from "@api/ai/tools/finalize-starter-prd";
import { getUserContext } from "@api/ai/utils/get-user-context";
import type { Context } from "@api/rest/types";
import { chatMessageSchema } from "@api/schemas/chat";
import { OpenAPIHono, z } from "@hono/zod-openapi";
import {
	clearChatActiveStreamId,
	clearChatActiveStreamIdIfMatch,
	setChatActiveStreamId,
} from "@nexus-app/db/queries/chats";
import { createUIMessageStreamResponse, generateId } from "ai";

const app = new OpenAPIHono<Context>();

/**
 * Project Starter request schema.
 * Mirrors the chat message shape and adds the Seed captured in step 1 of the
 * wizard (name / idea / drivers) so the interviewer agent has full context
 * from the first turn.
 */
const projectStarterRequestSchema = z.object({
	id: z.string().openapi({ description: "Chat/session id" }),
	message: chatMessageSchema.openapi({
		description: "The new user message to send to the starter interview",
	}),
	seed: z
		.object({
			name: z.string(),
			idea: z.string(),
			drivers: z.array(z.string()),
		})
		.openapi({ description: "Seed captured in the Project Starter wizard" }),
	timezone: z.string().optional(),
	country: z.string().optional(),
	city: z.string().optional(),
});

/**
 * Model used by the Project Starter interviewer.
 *
 * Gemini via an OpenAI-compatible endpoint (no Anthropic). Configure with
 * GEMINI_URL / GEMINI_API / GEMINI_MODEL in the environment. Falls back to
 * the app's AI Gateway model if the Gemini env is absent.
 */
const STARTER_MODEL = (() => {
	const url = process.env.GEMINI_URL;
	const apiKey = process.env.GEMINI_API;
	if (url && apiKey) {
		const gemini = createOpenAI({ baseURL: url, apiKey, name: "gemini" });
		return gemini.chat(process.env.GEMINI_MODEL ?? "gemini-3.6-flash");
	}
	return undefined;
})();

app.post("/", async (c) => {
	const body = await c.req.json();
	const result = projectStarterRequestSchema.safeParse(body);

	if (!result.success) {
		console.error("Project starter request validation failed:", result.error);
		return c.json({ success: false, error: result.error }, 400);
	}

	const { id, message, seed, timezone, country, city } = result.data;
	const session = c.get("session");
	const teamId = c.get("teamId");
	const userId = session.userId;

	const userContext = await getUserContext({
		userId,
		teamId,
		country,
		city,
		timezone,
	});

	const appContext = buildAppContext(
		{
			...userContext,
			agentId: "project-starter",
			integrationType: "web",
		},
		id,
	);

	await clearChatActiveStreamId({ chatId: id });

	const agent = createAgent({
		name: "project-starter",
		description:
			"Guided Project Starter interviewer — grills an idea into a complete PRD.",
		...(STARTER_MODEL
			? { model: STARTER_MODEL }
			: { model: "openai/gpt-5-mini" }),
		buildInstructions: (ctx: AppContext) =>
			buildProjectStarterSystemPrompt(ctx, seed as StarterSeed),
		tools: {
			finalizeStarterPrd: finalizeStarterPrdTool,
		},
		generateTitle: true,
	});

	const stream = await agent.stream({
		message,
		context: appContext,
	});

	const streamId = generateId();

	return createUIMessageStreamResponse({
		stream,
		consumeSseStream: async ({ stream: sseStream }) => {
			await setChatActiveStreamId({ chatId: id, streamId });
			try {
				await chatResumableStreamContext.createNewResumableStream(
					streamId,
					() => sseStream,
				);
			} catch (error) {
				await clearChatActiveStreamIdIfMatch({ chatId: id, streamId });
				console.error("Failed to create resumable stream:", error);
			}
		},
	});
});

export { app as projectStarterRouter };
