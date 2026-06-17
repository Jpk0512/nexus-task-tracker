import { fileStorageAdapter } from "@api/lib/storage-factory";
import { OpenAPIHono } from "@hono/zod-openapi";
import { createImport, updateImportStatus } from "@mimir/db/queries/imports";
import { tasksImportJob } from "@mimir/jobs/imports/tasks-import-job";
import type { Context } from "../types";

const app = new OpenAPIHono<Context>();

app.post("/tasks/upload", async (c) => {
	const session = c.get("session");
	const teamId = c.get("teamId");
	const userId = session.userId;
	const formData = await c.req.formData();
	const file = formData.get("file") as File | null;

	if (!file) {
		return c.json({ success: false, message: "No file uploaded" }, 400);
	}

	const result = await fileStorageAdapter.upload("imports", `${userId}/${file.name}`, file, "text/csv");

	let taskImport = await createImport({
		userId,
		type: "tasks_csv",
		fileName: file.name,
		filePath: result.path,
		teamId,
	});

	const job = await tasksImportJob.trigger({
		importId: taskImport.id,
	});

	taskImport = await updateImportStatus({
		status: "pending",
		id: taskImport.id,
		teamId,
		jobId: job.id,
	});

	return c.json({ success: true, taskImport });
});

export { app as importsRouter };
