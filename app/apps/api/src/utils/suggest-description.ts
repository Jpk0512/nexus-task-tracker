import { createOpenAI } from "@ai-sdk/openai";
import { GEMINI_MODEL_LITE } from "@api/ai/agents/config/gemini";
import { generateText } from "ai";

/**
 * Suggestion-only task-description generator.
 *
 * Never mutates task data — callers get back a plain string suggestion and
 * apply it (or don't) via the existing `tasks.updateDescription` mutation.
 * Mirrors the Gemini OpenAI-compatible endpoint wiring in
 * `rest/routers/project-starter.ts` (GEMINI_URL / GEMINI_API) and the
 * prompt-building shape of `utils/smart-complete.ts`.
 */

export const buildSuggestDescriptionPrompt = ({
	title,
	description,
	projectName,
}: {
	title: string;
	description?: string | null;
	projectName?: string | null;
}) => {
	return `You are helping a project team write a clear, actionable task description.

<task-title>
${title}
</task-title>

<existing-description>
${description?.trim() || "None provided."}
</existing-description>

<project>
${projectName?.trim() || "None provided."}
</project>

<rules>
- Suggest a single improved or newly-written description for this task.
- If an existing description is provided, enhance and clarify it rather than discarding useful detail.
- Keep it concise and actionable — a short paragraph or a few bullet points.
- Do not invent specific facts, dates, or names that aren't implied by the title, description, or project.
- Return only the description text — no preamble, labels, or markdown headers.
</rules>`;
};

const getGeminiLiteModel = () => {
	const url = process.env.GEMINI_URL;
	const apiKey = process.env.GEMINI_API;
	if (!url || !apiKey) {
		return undefined;
	}
	const gemini = createOpenAI({ baseURL: url, apiKey, name: "gemini" });
	return gemini.chat(GEMINI_MODEL_LITE);
};

export type SuggestDescriptionResult = {
	suggestion: string;
	success: boolean;
};

export const suggestTaskDescription = async ({
	title,
	description,
	projectName,
}: {
	title: string;
	description?: string | null;
	projectName?: string | null;
}): Promise<SuggestDescriptionResult> => {
	const model = getGeminiLiteModel();
	if (!model) {
		console.error(
			"suggestTaskDescription: GEMINI_URL/GEMINI_API not configured, skipping suggestion",
		);
		return { suggestion: "", success: false };
	}

	try {
		const response = await generateText({
			model,
			prompt: buildSuggestDescriptionPrompt({
				title,
				description,
				projectName,
			}),
		});
		return { suggestion: response.text.trim(), success: true };
	} catch (error) {
		console.error("suggestTaskDescription: generateText failed", error);
		return { suggestion: "", success: false };
	}
};
