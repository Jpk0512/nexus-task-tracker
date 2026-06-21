import { db } from "@nexus-app/db/client";
import { integrationLogs } from "@nexus-app/db/schema";

export const log = ({
	integrationId,
	level,
	key,
	message,
	details,
	inputTokens,
	outputTokens,
}: {
	integrationId: string;
	level: "info" | "error" | "warning";
	key: string;
	userLinkId?: string;
	message: string;
	details?: object;
	inputTokens?: number;
	outputTokens?: number;
}) => {
	console.log(`[${level.toUpperCase()}] ${message}`, details || "");
	db.insert(integrationLogs)
		.values({
			integrationId,
			level,
			key,
			message,
			details,
			inputTokens,
			outputTokens,
		})
		.catch((err) => {
			console.error("Failed to log integration event:", err);
		});
};
