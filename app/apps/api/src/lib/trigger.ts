import { configure } from "@trigger.dev/sdk";

export const trigger =
	process.env.NEXUS_LOCAL_DEV !== "1"
		? configure({
				accessToken: process.env.TRIGGER_SECRET_KEY,
			})
		: undefined;
