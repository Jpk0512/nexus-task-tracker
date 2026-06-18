import { fileStorageAdapter } from "@api/lib/storage-factory";
import { OpenAPIHono } from "@hono/zod-openapi";
import type { Context } from "../types";

const app = new OpenAPIHono<Context>();

app.post("/avatar", async (c) => {
	const session = c.get("session");
	const userId = session.userId;
	const formData = await c.req.formData();
	const file = formData.get("file") as File | null;

	if (!file) {
		return c.json({ success: false, message: "No file uploaded" }, 400);
	}

	const ext = file.name.split(".").pop() ?? "bin";
	const result = await fileStorageAdapter.upload(
		"vault",
		`${userId}/avatar.${ext}`,
		file,
		file.type,
	);

	return c.json({
		success: true,
		fullPath: result.fullPath,
		url: result.publicUrl,
	});
});

export { app as usersRouter };
