import z from "zod";
import { paginationSchema } from "./base";

export const getTaskLabelsSchema = z.object({
	...paginationSchema.shape,
	search: z.string().optional(),
});

export const createTaskLabelSchema = z.object({
	name: z.string().min(1, "Name is required"),
	color: z.string().min(1, "Color is required").optional(),
	description: z.string().optional(),
});

export const updateTaskLabelSchema = z.object({
	id: z.string(),
	name: z.string().min(1, "Name is required").optional(),
	color: z.string().min(1, "Color is required").optional(),
	description: z.string().optional(),
});

export const getTaskLabelByIdSchema = z.object({
	id: z.string(),
});

export const deleteTaskLabelSchema = z.object({
	id: z.string(),
});
