import type { StepResult } from "ai";

/**
 * Utility functions for handling streaming tool responses
 */

/**
 * Checks if any tool has completed its full streaming response
 * This is used to force stop the main LLM from generating additional content
 * when a tool has already provided a complete response
 */
export const shouldForceStop = (step: {
	// biome-ignore lint/suspicious/noExplicitAny: StepResult tool type param is unknown at call site
	steps?: StepResult<any>[];
}): boolean => {
	return (
		step.steps?.some((stepResult) => {
			return stepResult.content?.some((contentItem) => {
				if (contentItem.type === "tool-result") {
					// Check if the tool result indicates it wants to force stop the LLM
					return (
						// biome-ignore lint/suspicious/noExplicitAny: tool-result content shape is not narrowed by SDK types
						(contentItem as any).result?.forceStop === true ||
						// biome-ignore lint/suspicious/noExplicitAny: tool-result content shape is not narrowed by SDK types
						(contentItem as any).output?.forceStop === true
					);
				}
				return false;
			});
		}) ?? false
	);
};
