import { OpenPanel } from "@openpanel/sdk";

const LOCAL_DEV = process.env.NEXUS_LOCAL_DEV === "1";

function createOpenPanelStub(): OpenPanel {
	const handler = (event?: string, _props?: unknown): undefined => {
		console.log(`[stub:openpanel] track ${event ?? ""}`);
		return undefined;
	};
	return new Proxy(
		{} as OpenPanel,
		{
			get: (_t, prop) => {
				if (prop === "then") return undefined;
				if (prop === "track") return handler;
				return (..._args: unknown[]): undefined => undefined;
			},
		},
	);
}

export const op = LOCAL_DEV
	? (createOpenPanelStub() as OpenPanel)
	: new OpenPanel({
			clientId: process.env.OPENPANEL_CLIENT_ID!,
			clientSecret: process.env.OPENPANEL_CLIENT_SECRET!,
		});

export const trackMessage = async ({
	userId,
	model,
	teamId,
	teamName,
	source,
	input,
	output,
}: {
	userId: string;
	teamId: string;
	teamName?: string;
	model?: string;
	source: string;
	input?: number;
	output?: number;
}) => {
	await op.track("message", {
		profileId: userId,
		source,
		model,
		teamId,
		teamName,
		input,
		output,
	});
};

export const trackTaskCreated = async ({
	userId,
	teamId,
	teamName,
	source,
}: {
	userId: string;
	teamId: string;
	teamName?: string;
	source: string;
}) => {
	await op.track("task_created", {
		profileId: userId,
		source,
		teamId,
		teamName,
	});
};

export const trackFollowUp = async ({
	userId,
	teamId,
	teamName,
}: {
	userId: string;
	teamId: string;
	teamName?: string;
	message?: string;
}) => {
	await op.track("follow_up", {
		profileId: userId,
		teamId,
		teamName,
	});
};
