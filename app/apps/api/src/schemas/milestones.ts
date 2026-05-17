import z from "zod";
import { paginationSchema } from "./base";

export const getMilestonesSchema = z.object({
	...paginationSchema.shape,
	projectId: z.string().optional(),
});

export const createMilestoneSchema = z.object({
	name: z.string().min(1).max(255),
	description: z.string().max(1000).optional().nullable(),
	dueDate: z.string().optional().nullable(),
	color: z.string().optional().nullable(),
	projectId: z.string(),
});

export const updateMilestoneSchema = z.object({
	id: z.string(),
	name: z.string().min(1).max(255).optional().nullable(),
	description: z.string().max(1000).optional().nullable(),
	dueDate: z.string().optional().nullable(),
	color: z.string().optional().nullable(),
});
