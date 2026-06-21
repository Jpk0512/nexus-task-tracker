import { OpenAPIHono } from "@hono/zod-openapi";
import type { MiddlewareHandler } from "hono";
import type { Context } from "../types";

const app = new OpenAPIHono<Context>();

type AlexaRequestBody = {
	session?: { application?: { applicationId?: string } };
	context?: { System?: { application?: { applicationId?: string } } };
};

// Alexa sends applicationId under session.application.applicationId or
// context.System.application.applicationId. Rejecting requests whose
// applicationId does not match ALEXA_SKILL_ID prevents any foreign skill
// or unauthenticated caller from hitting this endpoint.
const verifyAlexaSkillId: MiddlewareHandler = async (c, next) => {
	const skillId = process.env.ALEXA_SKILL_ID;

	if (!skillId) {
		return c.json({ ok: false, error: "Server misconfiguration" }, 500);
	}

	let body: AlexaRequestBody;

	try {
		body = await c.req.json<AlexaRequestBody>();
	} catch {
		return c.json({ ok: false, error: "Invalid JSON" }, 400);
	}

	const applicationId =
		body?.session?.application?.applicationId ??
		body?.context?.System?.application?.applicationId;

	if (!applicationId || applicationId !== skillId) {
		return c.json({ ok: false, error: "Unauthorized" }, 401);
	}

	await next();
};

app.post(verifyAlexaSkillId, async (c) => {
	// Hono caches the parsed body; re-calling json() here returns the same object.
	const body = await c.req.json<AlexaRequestBody>();
	console.log("Received Alexa webhook:", body);
	return c.json({ message: "Alexa webhook received" });
});

export { app as alexaWebhook };
