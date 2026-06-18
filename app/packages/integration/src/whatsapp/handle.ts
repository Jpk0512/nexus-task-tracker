import { openai } from "@ai-sdk/openai";
import { buildAppContext } from "@api/ai/agents/config/shared";
import { messagingAgent } from "@api/ai/agents/messaging";
import type { UIChatMessage } from "@api/ai/types";
import { getUserContext } from "@api/ai/utils/get-user-context";
import { getLinkedUserByExternalId } from "@mimir/db/queries/integrations";
import {
	getAvailableTeams,
	getUserById,
	switchTeam,
} from "@mimir/db/queries/users";
import { trackMessage } from "@mimir/events/server";
import { LocalDiskStorageAdapter } from "@mimir/storage";
import { getApiUrl } from "@mimir/utils/envs";
import { experimental_transcribe, type UIMessage } from "ai";
import mime from "mime-types";
import { Twilio, twiml } from "twilio";

export const handleWhatsappMessage = async ({
	id,
	message,
	fromNumber,
	fromName,
	attachments,
}: {
	id: string;
	message: string;
	fromNumber: string;
	fromName: string;
	attachments?: {
		url: string;
		contentType: string;
	}[];
}) => {
	const response = new twiml.MessagingResponse();

	const associetedUser = await getLinkedUserByExternalId({
		externalUserId: fromNumber,
	});
	if (!associetedUser) {
		const url = `${getApiUrl()}/api/integrations/associate?externalUserId=${encodeURIComponent(
			fromNumber,
		)}&externalUserName=${encodeURIComponent(
			fromName,
		)}&integrationType=whatsapp`;
		response.message(
			`Please associate your WhatsApp number with your account by clicking the following ${url}`,
		);
		return response.toString();
	}

	const user = await getUserById(associetedUser.userId);
	if (!user) throw new Error("Associated user not found");

	// Auto assign teamId if missing
	if (!user.teamId) {
		const availableTeams = await getAvailableTeams(associetedUser.userId);
		if (availableTeams.length > 0) {
			await switchTeam(associetedUser.userId, {
				teamId: availableTeams[0]!.id,
			});
			user.teamId = availableTeams[0]!.id;
		}
	}

	const userContext = await getUserContext({
		userId: associetedUser.userId,
		teamId: user.teamId!,
	});

	const userMessage: UIChatMessage = {
		id,
		role: "user",
		parts: [{ type: "text", text: message }],
	};

	// Download and include attachments as separate message parts
	const storage = new LocalDiskStorageAdapter();
	let fileIndex = 0;
	for (const { url, contentType } of attachments || []) {
		const downloadResponse = await fetch(url, {
			headers: {
				Authorization: `Basic ${Buffer.from(`${process.env.TWILIO_ACCOUNT_SID}:${process.env.TWILIO_AUTH_TOKEN}`).toString("base64")}`,
			},
		});
		const fileBlob = await downloadResponse.blob();
		const fileExtension = mime.extension(contentType);
		const fileId =
			new URL(url).pathname.split("/").pop() || `file-${fileIndex++}`;

		if (contentType.startsWith("audio/")) {
			const arrayBuffer = await fileBlob.arrayBuffer();
			const audioBuffer = Buffer.from(arrayBuffer);

			const result = await experimental_transcribe({
				model: openai.transcription("gpt-4o-mini-transcribe"),
				audio: audioBuffer,
			});

			userMessage.parts.push({
				type: "text",
				text: `${result.text}`,
			});
		} else {
			const storageFile = await storage.upload(
				"vault",
				`${associetedUser.userId}/${fileId}.${fileExtension}`,
				fileBlob,
			);
			userMessage.parts.push({
				type: "text",
				text: `Attachment: ${storageFile.publicUrl}`,
			});
		}
	}

	const appContext = buildAppContext(
		{
			...userContext,
			integrationType: "whatsapp",
		},
		fromNumber,
	);

	const client = new Twilio(
		process.env.TWILIO_ACCOUNT_SID!,
		process.env.TWILIO_AUTH_TOKEN!,
	);

	const text: UIChatMessage = await messagingAgent.generate({
		message: userMessage,
		context: appContext,
	});

	const body =
		text.parts[text.parts.length - 1]?.type === "text"
			? (
					text.parts[text.parts.length - 1] as UIMessage["parts"][number] & {
						type: "text";
					}
				).text
			: "Sorry, I could not process your message.";

	trackMessage({
		userId: user.id,
		teamId: userContext.teamId,
		teamName: userContext.teamName ?? "",
		source: "whatsapp",
	});

	await client.messages.create({
		body,
		from: `whatsapp:${process.env.TWILIO_WHATSAPP_NUMBER}`,
		to: `whatsapp:${fromNumber}`,
	});

	return response.toString();
};
