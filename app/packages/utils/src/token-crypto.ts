/**
 * AES-GCM-256 envelope encryption for OAuth tokens stored at rest.
 *
 * Key derivation: TOKEN_ENCRYPTION_KEY env var must be a 64-hex-char string
 * (32 bytes). Generate with: node -e "console.log(require('crypto').randomBytes(32).toString('hex'))"
 *
 * Wire format (stored in DB as a string):
 *   "v1:<iv-hex>:<ciphertext-hex>"
 *
 * Existing plaintext rows (no "v1:" prefix) are returned as-is so existing
 * rows continue to work — re-encrypt them on the next successful OAuth refresh.
 *
 * Failure shape for missing key (fail-fast, no silent fallback):
 *   throw Error("TOKEN_ENCRYPTION_KEY is not set. Set a 64-hex-char value in env.")
 */

const VERSION_PREFIX = "v1:";

function getRawKey(): Uint8Array {
	const hex = process.env.TOKEN_ENCRYPTION_KEY;
	if (!hex) {
		throw new Error(
			"TOKEN_ENCRYPTION_KEY is not set. Set a 64-hex-char value in env.",
		);
	}
	if (hex.length !== 64 || !/^[0-9a-fA-F]+$/.test(hex)) {
		throw new Error(
			"TOKEN_ENCRYPTION_KEY must be exactly 64 hex characters (32 bytes).",
		);
	}
	const bytes = new Uint8Array(32);
	for (let i = 0; i < 32; i++) {
		bytes[i] = parseInt(hex.slice(i * 2, i * 2 + 2), 16);
	}
	return bytes;
}

async function importKey(): Promise<CryptoKey> {
	const raw = getRawKey();
	// Slice to get a concrete ArrayBuffer (Uint8Array.buffer may be SharedArrayBuffer)
	return crypto.subtle.importKey(
		"raw",
		raw.buffer.slice(raw.byteOffset, raw.byteOffset + raw.byteLength) as ArrayBuffer,
		{ name: "AES-GCM" },
		false,
		["encrypt", "decrypt"],
	);
}

/**
 * Encrypt a plaintext token string. Returns "v1:<iv-hex>:<ciphertext-hex>".
 */
export async function encryptToken(plaintext: string): Promise<string> {
	const key = await importKey();
	const iv = crypto.getRandomValues(new Uint8Array(12)); // 96-bit IV standard for AES-GCM
	const encoded = new TextEncoder().encode(plaintext);
	const cipherBuf = await crypto.subtle.encrypt(
		{ name: "AES-GCM", iv },
		key,
		encoded,
	);
	const ivHex = Buffer.from(iv).toString("hex");
	const ctHex = Buffer.from(cipherBuf).toString("hex");
	return `${VERSION_PREFIX}${ivHex}:${ctHex}`;
}

/**
 * Decrypt a token previously encrypted by encryptToken.
 *
 * Tolerates legacy plaintext rows (no "v1:" prefix) by returning them
 * unchanged — this allows existing DB rows to keep working while they
 * are lazily re-encrypted on the next OAuth refresh.
 */
export async function decryptToken(stored: string): Promise<string> {
	if (!stored.startsWith(VERSION_PREFIX)) {
		// Legacy plaintext row — pass through without failing
		return stored;
	}
	const rest = stored.slice(VERSION_PREFIX.length);
	const colonIdx = rest.indexOf(":");
	if (colonIdx === -1) {
		throw new Error("Malformed encrypted token: missing ciphertext segment.");
	}
	const iv = Buffer.from(rest.slice(0, colonIdx), "hex");
	const ct = Buffer.from(rest.slice(colonIdx + 1), "hex");

	const key = await importKey();
	const plainBuf = await crypto.subtle.decrypt(
		{ name: "AES-GCM", iv },
		key,
		ct,
	);
	return new TextDecoder().decode(plainBuf);
}
