import { promises as fs } from "node:fs";
import path from "node:path";
import type { FileStorageAdapter } from "./adapter";

export class LocalDiskStorageAdapter implements FileStorageAdapter {
	private storageRoot: string;
	private baseUrl: string;

	constructor(
		storageRoot: string = process.env["STORAGE_ROOT"] ?? "./storage",
		baseUrl: string = process.env["STORAGE_BASE_URL"] ??
			"http://localhost:3003/api/storage",
	) {
		this.storageRoot = path.resolve(storageRoot);
		this.baseUrl = baseUrl;
	}

	private safePath(bucket: string, filePath: string): string {
		const resolved = path.resolve(this.storageRoot, bucket, filePath);
		if (
			resolved !== this.storageRoot &&
			!resolved.startsWith(this.storageRoot + path.sep)
		) {
			throw new Error(`Path escapes storage root: ${resolved}`);
		}
		return resolved;
	}

	async upload(
		bucket: string,
		filePath: string,
		body: File | Blob | Buffer | string,
		_contentType?: string,
	): Promise<{ path: string; fullPath: string; publicUrl: string }> {
		const fullDiskPath = this.safePath(bucket, filePath);
		await fs.mkdir(path.dirname(fullDiskPath), { recursive: true });

		let buffer: Buffer;
		if (body instanceof File || body instanceof Blob) {
			buffer = Buffer.from(await body.arrayBuffer());
		} else if (typeof body === "string") {
			buffer = Buffer.from(body, "utf-8");
		} else {
			buffer = body;
		}

		await fs.writeFile(fullDiskPath, buffer);

		return {
			path: filePath,
			fullPath: `${bucket}/${filePath}`,
			publicUrl: this.getPublicUrl(bucket, filePath),
		};
	}

	getPublicUrl(bucket: string, filePath: string): string {
		const segments = [bucket, ...filePath.split("/")]
			.map(encodeURIComponent)
			.join("/");
		return `${this.baseUrl}/${segments}`;
	}

	async remove(bucket: string, filePath: string): Promise<void> {
		const fullDiskPath = this.safePath(bucket, filePath);
		try {
			await fs.unlink(fullDiskPath);
		} catch (err) {
			if ((err as NodeJS.ErrnoException).code !== "ENOENT") {
				throw err;
			}
		}
	}

	async exists(bucket: string, filePath: string): Promise<boolean> {
		const resolved = this.safePath(bucket, filePath);
		try {
			await fs.access(resolved);
			return true;
		} catch (err) {
			if ((err as NodeJS.ErrnoException).code === "ENOENT") return false;
			throw err;
		}
	}

	async download(bucket: string, filePath: string): Promise<Buffer> {
		return fs.readFile(this.safePath(bucket, filePath));
	}
}
