import z from "zod";

export const getRemoteRepositoriesSchema = z.object({
	query: z.string().optional(),
});

export const connectRepositorySchema = z.object({
	repositoryId: z.number(),
	repositoryName: z.string(),
	integrationId: z.string(),
});

export const disconnectRepositorySchema = z.object({
	repositoryId: z.number(),
});

export const getRemoteRepositoryBranchesSchema = z.object({
	repositoryId: z.number(),
	query: z.string().optional(),
});
