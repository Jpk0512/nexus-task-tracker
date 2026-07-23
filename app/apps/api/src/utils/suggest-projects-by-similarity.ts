import { createOpenAI } from "@ai-sdk/openai";
import { GEMINI_MODEL_LITE } from "@api/ai/agents/config/gemini";
import { generateText } from "ai";

/**
 * Suggestion-only capture -> project similarity ranking ("smart routing").
 *
 * Never mutates data — callers get back a ranked list of existing projects
 * that best match a piece of free capture text, for the UI to offer as
 * one-tap "file under this project" suggestions. Nothing is written until
 * the user explicitly accepts a suggestion in the UI. Mirrors the Gemini
 * OpenAI-compatible endpoint wiring in `rest/routers/project-starter.ts`
 * (GEMINI_URL / GEMINI_API) and the generateText() suggestion pattern of
 * `utils/suggest-tags-and-project.ts`.
 */

const MAX_SUGGESTIONS = 5;

export type CandidateProject = {
	id: string;
	name: string;
	prefix: string | null;
	description: string | null;
};

export const buildSuggestProjectsBySimilarityPrompt = ({
	captureText,
	candidates,
}: {
	captureText: string;
	candidates: CandidateProject[];
}) => {
	return `You are helping route a freeform capture note to the existing project it best belongs to.

<capture-text>
${captureText.slice(0, 5_000)}
</capture-text>

<existing-projects>
${candidates
	.map((p) => `- ${p.name}${p.description ? ` — ${p.description}` : ""}`)
	.join("\n")}
</existing-projects>

<rules>
- Rank the projects in <existing-projects> by how well they semantically match the capture text.
- Only include projects from <existing-projects>; copy each name EXACTLY as listed there.
- Omit any project that is not a plausible match — an empty list is a valid answer.
- Return at most ${MAX_SUGGESTIONS} projects, most relevant first.
- Return a "score" from 0 to 1 for each, reflecting match confidence.
- Respond with ONLY a JSON object of the shape {"suggestions": [{"name": string, "score": number}]} — no markdown, no preamble, no explanation.
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

export type ProjectSimilaritySuggestion = {
	id: string;
	name: string;
	prefix: string | null;
	score: number;
};

export type SuggestProjectsBySimilarityResult = {
	suggestions: ProjectSimilaritySuggestion[];
	success: boolean;
};

type RawModelSuggestion = {
	suggestions?: unknown;
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

/**
 * Ranks `candidates` against `captureText` via GEMINI_MODEL_LITE.
 *
 * Fail-soft by design: an unconfigured Gemini env, a malformed model
 * response, or a thrown error from the model call all resolve to an empty
 * suggestion list rather than propagating — callers must never see this
 * throw. Suggestions are existence-validated against `candidates` (matched
 * by exact, case-insensitive name) so the model can never invent a project
 * that doesn't exist.
 */
export const suggestProjectsBySimilarity = async ({
	captureText,
	candidates,
}: {
	captureText: string;
	candidates: CandidateProject[];
}): Promise<SuggestProjectsBySimilarityResult> => {
	if (candidates.length === 0) {
		return { suggestions: [], success: true };
	}

	const model = getGeminiLiteModel();
	if (!model) {
		console.error(
			"suggestProjectsBySimilarity: GEMINI_URL/GEMINI_API not configured, skipping suggestion",
		);
		return { suggestions: [], success: false };
	}

	try {
		const response = await generateText({
			model,
			prompt: buildSuggestProjectsBySimilarityPrompt({
				captureText,
				candidates,
			}),
		});

		const parsed = parseModelJson(response.text);
		const rawSuggestions = Array.isArray(parsed?.suggestions)
			? parsed.suggestions
			: [];

		const byNameLower = new Map(
			candidates.map((c) => [c.name.toLowerCase(), c]),
		);

		const seen = new Set<string>();
		const suggestions: ProjectSimilaritySuggestion[] = [];
		for (const raw of rawSuggestions) {
			if (typeof raw !== "object" || raw === null) continue;
			const { name, score } = raw as { name?: unknown; score?: unknown };
			if (typeof name !== "string") continue;
			const match = byNameLower.get(name.trim().toLowerCase());
			if (!match || seen.has(match.id)) continue;
			seen.add(match.id);
			const clampedScore =
				typeof score === "number" && Number.isFinite(score)
					? Math.min(1, Math.max(0, score))
					: 0;
			suggestions.push({
				id: match.id,
				name: match.name,
				prefix: match.prefix,
				score: clampedScore,
			});
		}

		suggestions.sort((a, b) => b.score - a.score);

		return {
			suggestions: suggestions.slice(0, MAX_SUGGESTIONS),
			success: true,
		};
	} catch (error) {
		console.error("suggestProjectsBySimilarity: generateText failed", error);
		return { suggestions: [], success: false };
	}
};
