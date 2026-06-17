import { fileStorageAdapter } from "@api/lib/storage-factory";
import { OpenAPIHono } from "@hono/zod-openapi";
import type { Context } from "../types";

const MIME_TYPES: Record<string, string> = {
	png: "image/png",
	jpg: "image/jpeg",
	jpeg: "image/jpeg",
	gif: "image/gif",
	webp: "image/webp",
	svg: "image/svg+xml",
	pdf: "application/pdf",
	csv: "text/csv",
	txt: "text/plain",
	json: "application/json",
	mp3: "audio/mpeg",
	mp4: "video/mp4",
};

const ALLOWED_BUCKETS = new Set(["vault", "imports"]);

const app = new OpenAPIHono<Context>();

app.get("/storage/*", async (c) => {
	const rawPath = c.req.path.replace(/^\/api\/storage\//, "");
	const segments = rawPath.split("/").map(decodeURIComponent);

	if (segments.length < 2 || !segments[0]) {
		return c.json({ error: "Invalid path" }, 400);
	}

	const bucket = segments[0];
	const filePath = segments.slice(1).join("/");

	if (
		!bucket ||
		bucket.includes("..") ||
		bucket.includes("/") ||
		!ALLOWED_BUCKETS.has(bucket) ||
		!filePath ||
		filePath.includes("..")
	) {
		return c.json({ error: "Invalid path" }, 400);
	}

	const found = await fileStorageAdapter.exists(bucket, filePath);
	if (!found) {
		return c.notFound();
	}

	const buffer = await fileStorageAdapter.download(bucket, filePath);
	const ext = filePath.split(".").pop()?.toLowerCase() ?? "";
	const contentType = MIME_TYPES[ext] ?? "application/octet-stream";

	return new Response(buffer.buffer as ArrayBuffer, {
		headers: {
			"Content-Type": contentType,
			"Cache-Control": "public, max-age=3600",
		},
	});
});

export { app as storageRouter };
