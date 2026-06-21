import { getTaskExecutionSchema } from "@api/schemas/task-executions";
import { protectedProcedure, router } from "@api/trpc/init";
import {
	getTaskExecutionByTaskId,
	getTaskExecutionLogs,
} from "@nexus-app/db/queries/task-executions";

export const taskExecutionsRouter = router({
	getByTaskId: protectedProcedure
		.input(getTaskExecutionSchema)
		.query(async ({ ctx, input }) => {
			return getTaskExecutionByTaskId(input.taskId, ctx.team.id);
		}),

	getLogsByTaskId: protectedProcedure
		.input(getTaskExecutionSchema)
		.query(async ({ input, ctx }) => {
			return getTaskExecutionLogs({
				taskId: input.taskId,
				teamId: ctx.team.id,
			});
		}),
});
