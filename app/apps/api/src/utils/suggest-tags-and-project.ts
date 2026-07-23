import { createOpenAI } from "@ai-sdk/openai";
import { GEMINI_MODEL_LITE } from "@api/ai/agents/config/gemini";
import { generateText } from "ai";

/**
 * Suggestion-only note auto-tagging + project association.
 *
 * Never mutates the note — callers get back thresholded tag suggestions and
 * an existence-validated project name (or null). The UI applies any
 * accepted tags via the existing `knowledge.update` frontmatter-write path.
 * Mirrors the Gemini OpenAI-compatible endpoint wiring in
 * `rest/routers/project-starter.ts` (GEMINI_URL / GEMINI_API) and the
 * generateText() suggestion pattern of `utils/suggest-description.ts`.
 */

const CONFIDENCE_THRESHOLD = 0.55;
const MAX_TAGS = 5;

export const buildSuggestTagsAndProjectPrompt = ({
	content,
	existingProjectNames,
}: {
	content: string;
	existingProjectNames: string[];
}) => {
	return `You are helping tag and file a note in a knowledge vault.

<note-content>
${content.slice(0, 20_000)}
</note-content>

<existing-projects>
${
	existingProjectNames.length > 0
		? existingProjectNames.map((n) => `- ${n}`).join("\n")
		: "None configured."
}
</existing-projects>

<rules>
- Suggest at most ${MAX_TAGS} short, lowercase, hyphenated tags that describe this note's topic.
- Suggest a project ONLY if the note content clearly relates to one of <existing-projects>; the project name must be copied EXACTLY as listed there. If none fit, or no projects are configured, set project to null.
- Do not invent a project name that isn't in the list.
- Return a "confidence" number from 0 to 1 reflecting how confident you are in these suggestions overall.
- Respond with ONLY a JSON object of the shape {"tags": string[], "project": string | null, "confidence": number} — no markdown, no preamble, no explanation.
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

export type SuggestTagsAndProjectResult = {
	tags: string[];
	project: string | null;
	confidence: number;
	success: boolean;
};

type RawModelSuggestion = {
	tags?: unknown;
	project?: unknown;
	confidence?: unknown;
};

function parseModelJson(text: string): RawModelSuggestion | null {
	const match = text.match(/\{[\s\S]*\}/);
	if (!match) return null;
	try {
		return JSON.parse(match[0]) as RawModelSuggestion;
	} catch {
		return null;
	}
}

export const suggestTagsAndProject = async ({
	content,
	existingProjectNames,
}: {
	content: string;
	existingProjectNames: string[];
}): Promise<SuggestTagsAndProjectResult> => {
	const model = getGeminiLiteModel();
	if (!model) {
		console.error(
			"suggestTagsAndProject: GEMINI_URL/GEMINI_API not configured, skipping suggestion",
		);
		return { tags: [], project: null, confidence: 0, success: false };
	}

	try {
		const response = await generateText({
			model,
			prompt: buildSuggestTagsAndProjectPrompt({
				content,
				existingProjectNames,
			}),
		});

		const parsed = parseModelJson(response.text);
		if (!parsed) {
			return { tags: [], project: null, confidence: 0, success: false };
		}

		const confidence =
			typeof parsed.confidence === "number" &&
			Number.isFinite(parsed.confidence)
				? Math.min(1, Math.max(0, parsed.confidence))
				: 0;

		// Confidence threshold guards tags only — spammy low-confidence tags
		// are the concrete failure mode this feature must avoid. Project
		// association is gated separately, by existence validation below.
		let tags: string[] = [];
		if (confidence >= CONFIDENCE_THRESHOLD) {
			const rawTags = Array.isArray(parsed.tags) ? parsed.tags : [];
			tags = rawTags
				.filter(
					(t): t is string => typeof t === "string" && t.trim().length > 0,
				)
				.map((t) => t.trim().toLowerCase())
				.slice(0, MAX_TAGS);
		}

		const rawProject =
			typeof parsed.project === "string" && parsed.project.trim().length > 0
				? parsed.project.trim()
				: null;
		const project =
			rawProject &&
			existingProjectNames.find(
				(n) => n.toLowerCase() === rawProject.toLowerCase(),
			);

		return {
			tags,
			project: project || null,
			confidence,
			success: true,
		};
	} catch (error) {
		console.error("suggestTagsAndProject: generateText failed", error);
		return { tags: [], project: null, confidence: 0, success: false };
	}
};
