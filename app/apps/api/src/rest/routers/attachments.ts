import { fileStorageAdapter } from "@api/lib/storage-factory";
import { OpenAPIHono } from "@hono/zod-openapi";
import type { Context } from "../types";

const app = new OpenAPIHono<Context>();

app.post("/upload", async (c) => {
	const session = c.get("session");
	const teamId = c.get("teamId");
	const formData = await c.req.formData();
	const file = formData.get("file") as File | null;

	if (!file) {
		return c.json({ success: false, message: "No file uploaded" }, 400);
	}

	const nameParts = file.name.split(".");
	const ext = nameParts.pop() ?? "bin";
	const name = nameParts.join(".");
	const result = await fileStorageAdapter.upload(
		"vault",
		`${teamId}/${name}-${Date.now()}.${ext}`,
		file,
		file.type,
	);

	return c.json({
		success: true,
		fullPath: result.fullPath,
		url: result.publicUrl,
	});
});

export { app as attachmentsRouter };
