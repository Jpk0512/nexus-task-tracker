export interface FileStorageAdapter {
	upload(
		bucket: string,
		path: string,
		body: File | Blob | Buffer | string,
		contentType?: string,
	): Promise<{ path: string; fullPath: string; publicUrl: string }>;

	getPublicUrl(bucket: string, path: string): string;

	remove(bucket: string, path: string): Promise<void>;

	exists(bucket: string, path: string): Promise<boolean>;

	download(bucket: string, path: string): Promise<Buffer>;
}
