import { decryptToken, encryptToken } from "@nexus-app/utils/token-crypto";
import { and, eq } from "drizzle-orm";
import { db } from "..";
import { userSecrets } from "../schema";

export type UserSecretKind = "secret" | "mcp";

/**
 * True for the fail-fast config error token-crypto throws when
 * TOKEN_ENCRYPTION_KEY is unset/malformed — distinct from a per-row data
 * problem, so callers can abort a batch instead of accumulating N copies
 * of the same system-level error.
 */
export const isEncryptionKeyConfigError = (error: unknown): boolean =>
	error instanceof Error && error.message.includes("TOKEN_ENCRYPTION_KEY");

export type UserSecretMetadata = {
	id: string;
	kind: UserSecretKind;
	name: string;
	notes: string | null;
	createdAt: string | null;
	updatedAt: string | null;
};

export type UserSecretWithValue = UserSecretMetadata & { value: string };

const metadataColumns = {
	id: userSecrets.id,
	kind: userSecrets.kind,
	name: userSecrets.name,
	notes: userSecrets.notes,
	createdAt: userSecrets.createdAt,
	updatedAt: userSecrets.updatedAt,
};

export const createUserSecret = async ({
	userId,
	teamId,
	kind,
	name,
	value,
	notes,
}: {
	userId: string;
	teamId?: string | null;
	kind: UserSecretKind;
	name: string;
	value: string;
	notes?: string | null;
}): Promise<UserSecretMetadata> => {
	const encryptedValue = await encryptToken(value);

	const [row] = await db
		.insert(userSecrets)
		.values({
			userId,
			teamId: teamId ?? null,
			kind,
			name,
			encryptedValue,
			notes: notes ?? null,
		})
		.returning(metadataColumns);

	if (!row) {
		throw new Error("Failed to create secret");
	}

	return row;
};

/**
 * List every secret owned by `userId`, decrypted for display. Callers MUST
 * pass the authenticated user's own id — this never accepts an arbitrary
 * userId from client input.
 */
export const listUserSecrets = async ({
	userId,
}: {
	userId: string;
}): Promise<UserSecretWithValue[]> => {
	const rows = await db
		.select({ ...metadataColumns, encryptedValue: userSecrets.encryptedValue })
		.from(userSecrets)
		.where(eq(userSecrets.userId, userId))
		.orderBy(userSecrets.name);

	return Promise.all(
		rows.map(async ({ encryptedValue, ...metadata }) => ({
			...metadata,
			value: await decryptToken(encryptedValue),
		})),
	);
};

export const updateUserSecret = async ({
	id,
	userId,
	name,
	value,
	notes,
}: {
	id: string;
	userId: string;
	name?: string;
	value?: string;
	notes?: string | null;
}): Promise<UserSecretMetadata> => {
	const [row] = await db
		.update(userSecrets)
		.set({
			...(name !== undefined ? { name } : {}),
			...(value !== undefined
				? { encryptedValue: await encryptToken(value) }
				: {}),
			...(notes !== undefined ? { notes } : {}),
			updatedAt: new Date().toISOString(),
		})
		.where(and(eq(userSecrets.id, id), eq(userSecrets.userId, userId)))
		.returning(metadataColumns);

	if (!row) {
		throw new Error("Secret not found");
	}

	return row;
};

export const deleteUserSecret = async ({
	id,
	userId,
}: {
	id: string;
	userId: string;
}): Promise<UserSecretMetadata> => {
	const [row] = await db
		.delete(userSecrets)
		.where(and(eq(userSecrets.id, id), eq(userSecrets.userId, userId)))
		.returning(metadataColumns);

	if (!row) {
		throw new Error("Secret not found");
	}

	return row;
};

export type MigrateUserSecretEntry = {
	kind: UserSecretKind;
	name: string;
	value: string;
	notes?: string | null;
};

export type MigrateUserSecretsResult = {
	migrated: number;
	skipped: number;
	errors: Array<{ name: string; message: string }>;
};

/**
 * Batch-import localStorage vault entries into the server-backed vault.
 * Idempotent on (userId, name): re-running the same batch upserts rather
 * than duplicating rows. Never echoes back any secret value.
 */
export const migrateUserSecrets = async ({
	userId,
	teamId,
	entries,
}: {
	userId: string;
	teamId?: string | null;
	entries: MigrateUserSecretEntry[];
}): Promise<MigrateUserSecretsResult> => {
	const result: MigrateUserSecretsResult = {
		migrated: 0,
		skipped: 0,
		errors: [],
	};

	for (const entry of entries) {
		const name = entry.name?.trim();
		if (!name || !entry.value) {
			result.skipped += 1;
			continue;
		}

		try {
			const encryptedValue = await encryptToken(entry.value);
			await db
				.insert(userSecrets)
				.values({
					userId,
					teamId: teamId ?? null,
					kind: entry.kind,
					name,
					encryptedValue,
					notes: entry.notes ?? null,
				})
				.onConflictDoUpdate({
					target: [userSecrets.userId, userSecrets.name],
					set: {
						kind: entry.kind,
						encryptedValue,
						notes: entry.notes ?? null,
						updatedAt: new Date().toISOString(),
					},
				});
			result.migrated += 1;
		} catch (error) {
			// A config-level failure (key unset/malformed) applies to every
			// remaining entry identically — abort the batch and let the
			// caller surface one clear error instead of N duplicates.
			if (isEncryptionKeyConfigError(error)) {
				throw error;
			}
			result.errors.push({
				name,
				message: error instanceof Error ? error.message : "Unknown error",
			});
		}
	}

	return result;
};
