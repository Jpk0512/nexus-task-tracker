import {
	createUserSecretSchema,
	deleteUserSecretSchema,
	listUserSecretsSchema,
	migrateUserSecretsSchema,
	updateUserSecretSchema,
} from "@api/schemas/user-secrets";
import {
	protectedProcedure,
	rateLimitedProcedure,
	router,
} from "@api/trpc/init";
import {
	createUserSecret,
	deleteUserSecret,
	isEncryptionKeyConfigError,
	listUserSecrets,
	migrateUserSecrets,
	updateUserSecret,
} from "@nexus-app/db/queries/user-secrets";
import { TRPCError } from "@trpc/server";

/**
 * Maps a query-layer failure to a typed TRPCError. A missing/malformed
 * TOKEN_ENCRYPTION_KEY is a distinct server-config error the UI can key off
 * of — it must never surface as an unhandled crash and must never fall back
 * to a plaintext path (encryptToken/decryptToken never do that; they throw).
 */
function toTrpcError(error: unknown): TRPCError {
	if (isEncryptionKeyConfigError(error)) {
		return new TRPCError({
			code: "INTERNAL_SERVER_ERROR",
			message:
				"Secret storage is not configured on this server. TOKEN_ENCRYPTION_KEY must be set before secrets can be read or written.",
		});
	}
	if (error instanceof Error && error.message === "Secret not found") {
		return new TRPCError({ code: "NOT_FOUND", message: "Secret not found" });
	}
	return new TRPCError({
		code: "INTERNAL_SERVER_ERROR",
		message: "Failed to process secret request",
	});
}

export const userSecretsRouter = router({
	list: protectedProcedure
		.meta({ team: false })
		.input(listUserSecretsSchema)
		.query(async ({ ctx }) => {
			try {
				return await listUserSecrets({ userId: ctx.user.id });
			} catch (error) {
				throw toTrpcError(error);
			}
		}),

	create: rateLimitedProcedure
		.meta({ team: false })
		.input(createUserSecretSchema)
		.mutation(async ({ ctx, input }) => {
			try {
				return await createUserSecret({
					userId: ctx.user.id,
					teamId: ctx.user.teamId,
					kind: input.kind,
					name: input.name,
					value: input.value,
					notes: input.notes,
				});
			} catch (error) {
				throw toTrpcError(error);
			}
		}),

	update: rateLimitedProcedure
		.meta({ team: false })
		.input(updateUserSecretSchema)
		.mutation(async ({ ctx, input }) => {
			try {
				return await updateUserSecret({
					id: input.id,
					userId: ctx.user.id,
					name: input.name,
					value: input.value,
					notes: input.notes,
				});
			} catch (error) {
				throw toTrpcError(error);
			}
		}),

	delete: rateLimitedProcedure
		.meta({ team: false })
		.input(deleteUserSecretSchema)
		.mutation(async ({ ctx, input }) => {
			try {
				return await deleteUserSecret({ id: input.id, userId: ctx.user.id });
			} catch (error) {
				throw toTrpcError(error);
			}
		}),

	migrate: rateLimitedProcedure
		.meta({ team: false })
		.input(migrateUserSecretsSchema)
		.mutation(async ({ ctx, input }) => {
			try {
				return await migrateUserSecrets({
					userId: ctx.user.id,
					teamId: ctx.user.teamId,
					entries: input.entries,
				});
			} catch (error) {
				throw toTrpcError(error);
			}
		}),
});
