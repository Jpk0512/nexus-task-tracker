// Typed re-export wrapper for alexa-verifier@4 (no @types package published).
// The package is untyped JS-ESM; we import via any-cast and re-export with the correct signature.
//
// alexaVerifier(certUrl, signature, rawBody): Promise<void>
//   Resolves on success; rejects with a string reason on failure.
//   It validates (per Amazon's spec):
//     1. SignatureCertChainUrl: https, s3.amazonaws.com host, port 443, path /echo.api/*
//     2. PEM cert chain: CA trust, CN=echo-api.amazon.com, not expired
//     3. RSA-SHA256 Signature header over the raw request body
//     4. request.timestamp within 150 seconds of now (replay protection)

// alexa-verifier has no @types package and .d.ts files are gitignored under src/.
// The import is untyped JS-ESM; we re-export it with the correct signature below.
// @ts-expect-error — TS7016: untyped module, no declaration file available
const mod = (await import("alexa-verifier")) as any; // eslint-disable-line @typescript-eslint/no-explicit-any
// The package exports a default function; ESM interop may surface it as .default or directly.
const verifyAlexaSignature: (
	certUrl: string,
	signature: string,
	rawBody: string,
) => Promise<void> =
	typeof mod.default === "function"
		? mod.default
		: (mod as unknown as typeof verifyAlexaSignature);

export { verifyAlexaSignature };
