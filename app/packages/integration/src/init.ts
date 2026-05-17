import type { integrations } from "@mimir/db/schema";
import { initMattermost, initMattermostSingle } from "./mattermost/init";
import { initSlack } from "./slack";

export const initIntegrations = async () => {
	initSlack();
	await Promise.all([initMattermost()]);
};

export const initIntegrationSingle = async (
	integration: typeof integrations.$inferSelect,
) => {
	switch (integration.type) {
		case "mattermost":
			await initMattermostSingle(integration);
	}
};
