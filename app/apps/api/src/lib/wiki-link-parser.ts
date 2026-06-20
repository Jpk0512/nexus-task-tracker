// Pure wiki-link parser. No I/O; all inputs are in-memory.
// Tie-break resolution order (DEC-007): full-path match → basename match → unresolved.

export interface ParsedLink {
	linkText: string;
}

export interface ResolvedLink {
	linkText: string;
	toNoteId: string | null;
}

export interface NoteRef {
	id: string;
	relativePath: string;
}

const WIKI_LINK_RE = /\[\[([^\]]+)\]\]/g;

export function extractLinks(content: string): ParsedLink[] {
	const out: ParsedLink[] = [];
	const re = new RegExp(WIKI_LINK_RE.source, "g");
	for (;;) {
		const m = re.exec(content);
		if (m === null) break;
		const raw = m[1]!.trim();
		if (raw) out.push({ linkText: raw });
	}
	return out;
}

export function resolveLinks(
	links: ParsedLink[],
	notes: NoteRef[],
): ResolvedLink[] {
	return links.map(({ linkText }) => ({
		linkText,
		toNoteId: resolveOne(linkText, notes),
	}));
}

function resolveOne(linkText: string, notes: NoteRef[]): string | null {
	const lower = linkText.toLowerCase();

	// 1. Full-path match: linkText equals relativePath (strip .md suffix for comparison)
	for (const n of notes) {
		const pathNoExt = n.relativePath.replace(/\.md$/i, "");
		if (pathNoExt.toLowerCase() === lower) return n.id;
	}

	// 2. Basename match: linkText equals the final path segment (no extension)
	const matches: NoteRef[] = [];
	for (const n of notes) {
		const segs = n.relativePath.split(/[/\\]/);
		const base = (segs[segs.length - 1] ?? "").replace(/\.md$/i, "");
		if (base.toLowerCase() === lower) matches.push(n);
	}
	if (matches.length === 1) return matches[0]!.id;
	if (matches.length > 1) {
		// Multiple basenames match — pick the shortest relative path (shallowest).
		matches.sort((a, b) => a.relativePath.length - b.relativePath.length);
		return matches[0]!.id;
	}

	// 3. Unresolved
	return null;
}
