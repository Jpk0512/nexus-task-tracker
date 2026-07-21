import { tool } from "ai";
import z from "zod";

/**
 * Project Starter — finalize tool.
 *
 * Called ONCE by the starter agent when the interview has enough coverage
 * (or the user asked to finalize). The agent passes the COMPLETE Product
 * Requirements Document as a single markdown string in `prd`. The dashboard
 * detects this tool call on the assistant message and lifts `input.prd` into
 * the PRD review step.
 *
 * The tool body is intentionally trivial — its purpose is to be a structured
 * finalization SIGNAL that carries the PRD as its input. Starter-only: NOT
 * registered in the global tool-registry.
 */
export const finalizeStarterPrdTool = tool({
	description:
		"Finalize the Project Starter interview. Call this exactly ONCE, when the user is done answering OR all required product areas are covered. Pass the COMPLETE Product Requirements Document as a single markdown string with every section populated from the conversation.",
	inputSchema: z.object({
		prd: z
			.string()
			.min(50)
			.describe(
				"The complete PRD as a single markdown string, all sections populated from the interview.",
			),
	}),
	execute: async () => {
		return {
			ok: true as const,
			message: "PRD ready for review.",
		};
	},
});
