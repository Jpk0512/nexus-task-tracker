/**
 * Dump store — local brain-dump items (not Inbox / Needs you).
 * Shared between the global DumpModal and the Home surface.
 */

export const DUMP_KEY = "nexus.capture.dump";

export type DumpItem = {
	id: string;
	text: string;
	createdAt: string;
};

export function readDump(): DumpItem[] {
	try {
		const raw = localStorage.getItem(DUMP_KEY);
		if (!raw) return [];
		const parsed = JSON.parse(raw);
		return Array.isArray(parsed) ? (parsed as DumpItem[]) : [];
	} catch {
		return [];
	}
}

export function writeDump(items: DumpItem[]) {
	try {
		localStorage.setItem(DUMP_KEY, JSON.stringify(items));
		// Notify other surfaces (Home card) that the dump changed.
		window.dispatchEvent(new CustomEvent("nexus.dump.changed"));
	} catch {
		/* ignore quota errors */
	}
}

export function addDumpItem(text: string): DumpItem[] {
	const item: DumpItem = {
		id: crypto.randomUUID(),
		text: text.trim(),
		createdAt: new Date().toISOString(),
	};
	const next = [item, ...readDump()];
	writeDump(next);
	return next;
}

export function removeDumpItem(id: string): DumpItem[] {
	const next = readDump().filter((i) => i.id !== id);
	writeDump(next);
	return next;
}
