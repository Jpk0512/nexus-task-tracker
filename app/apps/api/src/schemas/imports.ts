import { importStatusEnum } from "@nexus-app/db/schema";
import z from "zod";

export const getImportsSchema = z.object({
	status: z.array(z.literal(importStatusEnum.enumValues)).optional(),
	cursor: z.string().optional(),
	pageSize: z.number().optional(),
});
