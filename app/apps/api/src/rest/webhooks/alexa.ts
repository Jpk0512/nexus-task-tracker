import { OpenAPIHono } from "@hono/zod-openapi";
import type { MiddlewareHandler } from "hono";
import type { Context } from "../types";

// alexa-verifier (v4, ESM) — types in src/alexa-verifier.d.ts (no @types package).
// It validates per Amazon's spec:
//   1. SignatureCertChainUrl: https, host=s3.amazonaws.com, port=443, path=/echo.api/*
//   2. Fetches+validates PEM cert chain (CA trust, CN=echo-api.amazon.com, not expired)
//   3. Verifies RSA-SHA256 of Signature header over raw request body
//   4. request.timestamp within 150 seconds of now (replay-attack guard)
//
// Auth-error response shape (what this middleware returns on rejection):
//   HTTP 401 { "ok": false, "error": "<reason string from alexa-verifier>" }
//   HTTP 400 { "ok": false, "error": "missing Signature or SignatureCertChainUrl header" }
//   HTTP 500 { "ok": false, "error": "Server misconfiguration" }
import { verifyAlexaSignature } from "../../lib/alexa-verifier";

const app = new OpenAPIHono<Context>();

type AlexaRequestBody = {
	request?: { timestamp?: string };
	session?: { application?: { applicationId?: string } };
	context?: { System?: { application?: { applicationId?: string } } };
};

// PRIMARY auth: cryptographic request-signature verification per Amazon's spec.
// SECONDARY auth: skill-id check to ensure the request targets this skill (defense-in-depth).
// Both gates must pass. Order matters: crypto first so a forged body never reaches skill-id check.
const verifyAlexaRequest: MiddlewareHandler = async (c, next) => {
	const skillId = process.env.ALEXA_SKILL_ID;

	if (!skillId) {
		return c.json({ ok: false, error: "Server misconfiguration" }, 500);
	}

	const certUrl = c.req.header("SignatureCertChainUrl");
	const signature = c.req.header("Signature");

	if (!certUrl || !signature) {
		return c.json(
			{ ok: false, error: "missing Signature or SignatureCertChainUrl header" },
			400,
		);
	}

	// Read the raw body once; Hono caches it so downstream c.req.json() still works.
	const rawBody = await c.req.text();

	// PRIMARY gate — cryptographic verification.
	// Rejects if: cert URL invalid, cert chain untrusted/expired, signature mismatch,
	// or timestamp > 150s old (replay protection).
	try {
		await verifyAlexaSignature(certUrl, signature, rawBody);
	} catch (err) {
		const reason = typeof err === "string" ? err : "signature verification failed";
		return c.json({ ok: false, error: reason }, 401);
	}

	// SECONDARY gate — skill-id binding (prevents requests from other Alexa skills
	// whose certs would otherwise pass the Amazon-trust check above).
	let body: AlexaRequestBody;
	try {
		body = JSON.parse(rawBody) as AlexaRequestBody;
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

app.post(verifyAlexaRequest, async (c) => {
	// Hono bodyCache: text() was consumed in middleware; json() re-parses from cache.
	const body = await c.req.json<AlexaRequestBody>();
	console.log("Received Alexa webhook:", body);
	return c.json({ message: "Alexa webhook received" });
});

export { app as alexaWebhook };
