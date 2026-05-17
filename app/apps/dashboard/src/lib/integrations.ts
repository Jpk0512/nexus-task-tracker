import { getApiUrl } from "@mimir/utils/envs";

export const getSlackInstallUrl = () => {
	const url = new URL("https://slack.com/oauth/v2/authorize");
	url.searchParams.append(
		"client_id",
		process.env.NEXT_PUBLIC_SLACK_CLIENT_ID!,
	);
	url.searchParams.append(
		"scope",
		"chat:write,channels:read,groups:read,im:read,mpim:read,users:read",
	);
	const redirectUri = new URL(
		process.env.NODE_ENV === "development"
			? "https://b8e124c60ed5.ngrok-free.app/api/slack/oauth/callback"
			: `${getApiUrl()}/api/slack/oauth/callback`,
	);

	url.searchParams.append("redirect_uri", redirectUri.toString());
	return url.toString();
};
