import {
	OpenPanelComponent,
	type PostEventPayload,
	useOpenPanel,
} from "@openpanel/nextjs";

const isProd = process.env.NODE_ENV === "production";
const LOCAL_DEV = process.env.NEXT_PUBLIC_NEXUS_LOCAL_DEV === "1";

const Provider = ({ profileId }: { profileId: string }) => {
	if (LOCAL_DEV) {
		return null;
	}
	return (
		<OpenPanelComponent
			clientId={process.env.NEXT_PUBLIC_OPENPANEL_CLIENT_ID!}
			trackAttributes={true}
			trackScreenViews={isProd}
			trackOutgoingLinks={isProd}
			profileId={profileId}
			waitForProfile
		/>
	);
};

const track = (options: { event: string } & PostEventPayload["properties"]) => {
	const { track: openTrack } = useOpenPanel();

	if (LOCAL_DEV) {
		console.log("[stub:openpanel] track", options?.event);
		return;
	}

	if (!isProd) {
		console.log("Track", options);
		return;
	}

	const { event, ...rest } = options;

	openTrack(event, rest);
};

export const hook = useOpenPanel;

export { Provider, track, useOpenPanel };
