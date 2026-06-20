// Re-export shim: lets dashboard vitest tests import the api-package parser
// without needing a vitest alias for @api. Relative path resolves correctly
// both for tsc (moduleResolution: Bundler, no rootDir constraint) and vitest
// (which resolves relative imports natively).

export type {
	NoteRef,
	ParsedLink,
	ResolvedLink,
} from "../../api/src/lib/wiki-link-parser";
export {
	extractLinks,
	resolveLinks,
} from "../../api/src/lib/wiki-link-parser";
