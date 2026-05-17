import { App } from "@slack/bolt";

let cachedSlackClient: App | null = null;

export const getSlackClient = () => {
	if (cachedSlackClient) {
		return cachedSlackClient;
	}

	cachedSlackClient = new App({
		clientSecret: process.env.SLACK_CLIENT_SECRET!,
		signingSecret: process.env.SLACK_SIGNING_SECRET!,
		token: process.env.SLACK_BOT_TOKEN!,
		appToken: process.env.SLACK_BOT_TOKEN!,
		socketMode: false,
	});

	return cachedSlackClient;
};
