import { LocalDiskStorageAdapter } from "@nexus-app/storage";

let _adapter: LocalDiskStorageAdapter | null = null;

export function getStorageAdapter(): LocalDiskStorageAdapter {
	if (!_adapter) {
		_adapter = new LocalDiskStorageAdapter(
			process.env.STORAGE_ROOT,
			process.env.STORAGE_BASE_URL,
		);
	}
	return _adapter;
}

export const fileStorageAdapter = getStorageAdapter();
