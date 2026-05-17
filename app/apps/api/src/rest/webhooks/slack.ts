import crypto from "node:crypto";
import { OpenAPIHono } from "@hono/zod-openapi";
import { handleSlackMessage } from "@mimir/integration/slack";
import type { EventFromType } from "@slack/bolt";
import type { MiddlewareHandler } from "hono";
import type { Context } from "../types";

const app = new OpenAPIHono<Context>();

const verifySignature: MiddlewareHandler = async (c, next) => {
	const rawBody = await c.req.text();
	const slackSigningSecret = process.env.SLACK_SIGNING_SECRET;
	const timestamp = c.req.header("X-Slack-Request-Timestamp") || "";
	const signature = ["v0", timestamp, rawBody];
	const basestring = signature.join(":");

	const hash =
		"v0=" +
		crypto
			.createHmac("sha256", slackSigningSecret)
			.update(basestring)
			.digest("hex");
	const retrievedSignature = c.req.header("X-Slack-Signature") || "";

	const validSignature = (): boolean => {
		const slackSigBuffer = Buffer.from(retrievedSignature);
		const compSigBuffer = Buffer.from(hash);

		return crypto.timingSafeEqual(slackSigBuffer, compSigBuffer);
	};

	if (!validSignature()) {
		console.log("Invalid Slack signature");
		return c.json({ error: "Invalid signature" }, 401);
	}

	await next();
};

app.post(verifySignature, async (c) => {
	const event = await c.req.json();

	if (event.type === "url_verification") {
		const typedEvent = event as { challenge: string };
		return c.json({ challenge: typedEvent.challenge });
	}

	try {
		switch (event.event.type) {
			case "app_mention": {
				const typedEvent = event.event as EventFromType<"app_mention">;

				handleSlackMessage({
					channel: typedEvent.channel,
					externalTeamId: typedEvent.team,
					externalUserId: typedEvent.user,
					message: typedEvent.text,
					messageId: typedEvent.client_msg_id,
					threadTs: typedEvent.ts,
					attachments: typedEvent.files?.map((file: any) => ({
						url: file.thumb_1024,
						mimeType: file.mimetype,
					})),
				});

				return c.json({ status: "App mention received" });
			}
			default: {
				console.log("Received Slack event:", event);
				return c.json({ status: "Event received" });
			}
		}
	} catch (error) {
		console.error("Error handling Slack event:", error);
		return c.json({ error: "Internal server error" }, 500);
	}
});

export { app as slackWebhookApp };
