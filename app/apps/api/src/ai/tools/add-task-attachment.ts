import { fileStorageAdapter } from "@api/lib/storage-factory";
import { getTaskById, updateTask } from "@nexus-app/db/queries/tasks";
import { tool } from "ai";
import z from "zod";
import { getToolContext } from "../agents/config/shared";

export const addTaskAttachmentToolSchema = z.object({
	taskId: z.string().describe("ID of the task to attach the file to"),
	fileName: z
		.string()
		.min(1)
		.describe(
			"Name of the file to upload with the extension (e.g., 'document.pdf')",
		),
	fileContent: z.string().describe("File content in plain text"),
});

export const addTaskAttachmentTool = tool({
	description: "Upload a file and attach it to a task",
	inputSchema: addTaskAttachmentToolSchema,
	execute: async function* (input, executionOptions) {
		try {
			const { behalfUserId, teamId } = getToolContext(executionOptions);

			const fileBuffer = Buffer.from(input.fileContent);

			const result = await fileStorageAdapter.upload(
				"vault",
				`${behalfUserId}/${input.taskId}/${input.fileName}`,
				fileBuffer,
			);

			const task = await getTaskById(input.taskId);

			await updateTask({
				id: input.taskId,
				teamId,
				userId: behalfUserId,
				attachments: [...task.attachments, result.publicUrl],
			});

			yield {
				url: result.publicUrl,
				fileName: input.fileName,
				uploadedAt: new Date().toISOString(),
			};
		} catch (error) {
			console.error("Failed to upload file:", error);
			throw new Error(`Failed to upload file: ${error}`);
		}
	},
});
