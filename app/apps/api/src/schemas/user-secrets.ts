import z from "zod";

const userSecretKindSchema = z.enum(["secret", "mcp"]);

export const listUserSecretsSchema = z.object({});

const secretValueSchema = z
	.string()
	.min(1, "Value is required")
	.max(10_000, "Value is too long");

export const createUserSecretSchema = z.object({
	kind: userSecretKindSchema.default("secret"),
	name: z.string().min(1, "Name is required").max(200),
	value: secretValueSchema,
	notes: z.string().max(2000).optional(),
});

export const updateUserSecretSchema = z.object({
	id: z.string().min(1),
	name: z.string().min(1, "Name is required").max(200).optional(),
	value: secretValueSchema.optional(),
	notes: z.string().max(2000).optional(),
});

export const deleteUserSecretSchema = z.object({
	id: z.string().min(1),
});

const migrateUserSecretEntrySchema = z.object({
	kind: userSecretKindSchema,
	name: z.string().min(1).max(200),
	value: secretValueSchema,
	notes: z.string().max(2000).optional(),
});

export const migrateUserSecretsSchema = z.object({
	entries: z.array(migrateUserSecretEntrySchema).max(500),
});
