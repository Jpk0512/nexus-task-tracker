/**
 * Reusable Gemini model-tier constants, shared across AI features that call
 * Gemini via an OpenAI-compatible endpoint (GEMINI_URL / GEMINI_API).
 *
 * Project Starter's interview/PRD flow (the heavier reasoning task) reads
 * its own GEMINI_MODEL default at its call site — see
 * `rest/routers/project-starter.ts`.
 *
 * GEMINI_MODEL_LITE is plumbing only: no caller yet. It exists so future
 * lightweight "AI-organize"-style features have a single named default to
 * import instead of re-deriving their own fallback literal.
 */
export const GEMINI_MODEL_LITE: string =
	process.env.GEMINI_MODEL_LITE ?? "gemini-3.5-flash-lite";
