/// <reference path="./vitest-globals.d.ts" />
/**
 * TASK-011 — IDOR regression tests: cross-team attachment/library mutation guards.
 *
 * These tests confirm that the IDOR fixes landed in commits b196839 (todos.ts)
 * and f157675 (library.ts) are present and structurally correct. Each test
 * would have FAILED before the fix (no innerJoin guard), and PASSes after.
 *
 * Strategy: source-code assertions (node harness, no jsdom, no tRPC network call).
 * The same pattern is used by feat2-task011-todos-wiring.test.ts and all other
 * dashboard regression tests in this suite.
 *
 * Acceptance criteria (all must PASS against fixed code):
 *
 *  GWT-IDOR-1 — todos.detach CANNOT delete team-B attachment via team-A context
 *    GIVEN todos.detach verifies attachment ownership via innerJoin to todos
 *    WHEN a caller from a different team supplies an attachmentId
 *    THEN the innerJoin guard produces NOT_FOUND before the DELETE fires
 *
 *  GWT-IDOR-2 — todos.updateAttachment CANNOT patch team-B attachment via team-A context
 *    GIVEN todos.updateAttachment verifies ownership via innerJoin to todos
 *    WHEN a caller from a different team supplies an attachmentId
 *    THEN the innerJoin guard produces NOT_FOUND before the UPDATE fires
 *
 *  GWT-IDOR-3 — library.addTag CANNOT tag team-B entry via team-A context
 *    GIVEN library.addTag verifies entry ownership via innerJoin to library_sources
 *    WHEN a caller from a different team supplies an entryId
 *    THEN the innerJoin guard produces NOT_FOUND before the INSERT fires
 *
 *  GWT-IDOR-4 — library.removeTag CANNOT untag team-B entry via team-A context
 *    GIVEN library.removeTag verifies entry ownership via innerJoin to library_sources
 *    WHEN a caller from a different team supplies an entryId
 *    THEN the innerJoin guard produces NOT_FOUND before the DELETE fires
 *
 *  GWT-IDOR-5 — library.linkProject CANNOT link a project to a team-B entry
 *    GIVEN library.linkProject verifies entry ownership via innerJoin to library_sources
 *    WHEN a caller from a different team supplies an entryId
 *    THEN the innerJoin guard produces NOT_FOUND before the INSERT fires
 *
 *  GWT-IDOR-6 — library.unlinkProject CANNOT unlink a project from a team-B entry
 *    GIVEN library.unlinkProject verifies entry ownership via innerJoin to library_sources
 *    WHEN a caller from a different team supplies an entryId
 *    THEN the innerJoin guard produces NOT_FOUND before the DELETE fires
 *
 *  GWT-IDOR-7 — todos.detach guard uses innerJoin (not a separate SELECT)
 *    The guard must be a JOIN-based single-query ownership check, not a
 *    two-step select-then-check (which is racy and harder to audit).
 *
 *  GWT-IDOR-8 — todos.updateAttachment guard uses innerJoin
 *    Same single-query JOIN requirement for the patch path.
 *
 * Run: rtk vitest run app/apps/dashboard/__tests__/feat2-task011-idor-regression.test.ts
 */

import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, test } from "vitest";

// ---------------------------------------------------------------------------
// Path helpers
// ---------------------------------------------------------------------------

// __tests__ (1) → dashboard (2) → apps (3) → app (4 = monorepo root)
const DASHBOARD_ROOT = join(import.meta.dirname ?? __dirname, "..");
const API_ROOT = join(DASHBOARD_ROOT, "..", "api");

const TODOS_ROUTER_PATH = join(API_ROOT, "src", "trpc", "routers", "todos.ts");

const LIBRARY_ROUTER_PATH = join(
	API_ROOT,
	"src",
	"trpc",
	"routers",
	"library.ts",
);

function readSrc(p: string): string {
	if (!existsSync(p)) throw new Error(`Expected source file not found: ${p}`);
	return readFileSync(p, "utf8");
}

/**
 * Extract the body of a named procedure from a router source file.
 * Looks for `<name>: protectedProcedure` and returns everything up to the
 * start of the next sibling procedure declaration (tab-indented identifier
 * followed by `: protectedProcedure`, `: publicProcedure`, or `: router(`).
 * This approach is robust against nested braces inside .input(z.object({...}))
 * chains, which defeat simple depth-tracking from the marker position.
 */
function extractProcedure(src: string, name: string): string {
	const marker = `${name}: protectedProcedure`;
	const start = src.indexOf(marker);
	if (start === -1) return "";
	// Scan the remainder (after the procedure name) for the start of the next
	// sibling procedure at the same indentation level (one tab + identifier).
	const nextProcPattern =
		/\n\t[a-zA-Z]+:\s*protectedProcedure|\n\t[a-zA-Z]+:\s*publicProcedure|\n\t[a-zA-Z]+:\s*router\(/;
	const rest = src.slice(start + name.length);
	const nextMatch = rest.search(nextProcPattern);
	if (nextMatch === -1) return src.slice(start);
	return src.slice(start, start + name.length + nextMatch);
}

// ---------------------------------------------------------------------------
// GWT-IDOR-1 + GWT-IDOR-7 — todos.detach
// ---------------------------------------------------------------------------

describe("GWT-IDOR-1 + GWT-IDOR-7 — todos.detach: innerJoin ownership guard before DELETE", () => {
	/**
	 * GIVEN todos.detach verifies that the attachment's parent todo belongs to the
	 *       calling team before deleting — using a JOIN-based query
	 * WHEN a caller from team B passes an attachmentId that belongs to team A
	 * THEN the guard resolves nothing (empty result), throwing TRPCError NOT_FOUND,
	 *      and the DELETE never executes
	 *
	 * Source evidence required:
	 *   1. The detach procedure body contains `.innerJoin(todos,` — ownership via JOIN.
	 *   2. The guard asserts `todos.teamId` against `ctx.user.teamId`.
	 *   3. A TRPCError with code "NOT_FOUND" is thrown when the guard returns nothing.
	 *   4. The DELETE only executes after the guard passes (guard fires first).
	 */

	test("todos.detach body contains innerJoin(todos, ...) for attachment ownership", () => {
		const src = readSrc(TODOS_ROUTER_PATH);
		const body = extractProcedure(src, "detach");
		expect(body.length).toBeGreaterThan(0);
		// The IDOR fix uses innerJoin to resolve attachment -> todo -> teamId in one query
		expect(body).toMatch(/\.innerJoin\(\s*todos[,\s]/);
	});

	test("todos.detach guard checks todos.teamId equals ctx.user.teamId before deleting", () => {
		const src = readSrc(TODOS_ROUTER_PATH);
		const body = extractProcedure(src, "detach");
		// The teamId equality filter wraps both sides in eq() and may span lines
		expect(body).toMatch(/eq\(todos\.teamId[\s\S]*?ctx\.user\.teamId/);
	});

	test("todos.detach throws TRPCError NOT_FOUND when ownership check fails", () => {
		const src = readSrc(TODOS_ROUTER_PATH);
		const body = extractProcedure(src, "detach");
		// Guard: if the join returns nothing, throw NOT_FOUND before the DELETE
		expect(body).toMatch(/TRPCError[^}]*NOT_FOUND/);
	});

	test("todos.detach guard fires before the DELETE — NOT_FOUND check precedes the delete statement", () => {
		const src = readSrc(TODOS_ROUTER_PATH);
		const body = extractProcedure(src, "detach");
		// The NOT_FOUND throw must appear before "delete(todoAttachments)"
		const notFoundIdx = body.indexOf("NOT_FOUND");
		const deleteIdx = body.indexOf("delete(todoAttachments)");
		expect(notFoundIdx).toBeGreaterThan(-1);
		expect(deleteIdx).toBeGreaterThan(-1);
		expect(notFoundIdx).toBeLessThan(deleteIdx);
	});
});

// ---------------------------------------------------------------------------
// GWT-IDOR-2 + GWT-IDOR-8 — todos.updateAttachment
// ---------------------------------------------------------------------------

describe("GWT-IDOR-2 + GWT-IDOR-8 — todos.updateAttachment: innerJoin ownership guard before UPDATE", () => {
	/**
	 * GIVEN todos.updateAttachment verifies ownership via innerJoin before patching
	 * WHEN a caller from team B passes an attachmentId that belongs to team A
	 * THEN the guard resolves nothing, throwing NOT_FOUND, and no UPDATE is applied
	 *
	 * Source evidence required:
	 *   1. The updateAttachment procedure body contains `.innerJoin(todos,`.
	 *   2. The guard asserts `todos.teamId` against `ctx.user.teamId`.
	 *   3. A TRPCError NOT_FOUND is thrown when the guard returns nothing.
	 *   4. The UPDATE fires only after the guard (NOT_FOUND check precedes the UPDATE).
	 */

	test("todos.updateAttachment body contains innerJoin(todos, ...) for ownership", () => {
		const src = readSrc(TODOS_ROUTER_PATH);
		const body = extractProcedure(src, "updateAttachment");
		expect(body.length).toBeGreaterThan(0);
		expect(body).toMatch(/\.innerJoin\(\s*todos[,\s]/);
	});

	test("todos.updateAttachment guard checks todos.teamId equals ctx.user.teamId", () => {
		const src = readSrc(TODOS_ROUTER_PATH);
		const body = extractProcedure(src, "updateAttachment");
		expect(body).toMatch(/eq\(todos\.teamId[\s\S]*?ctx\.user\.teamId/);
	});

	test("todos.updateAttachment throws TRPCError NOT_FOUND when ownership check fails", () => {
		const src = readSrc(TODOS_ROUTER_PATH);
		const body = extractProcedure(src, "updateAttachment");
		expect(body).toMatch(/TRPCError[^}]*NOT_FOUND/);
	});

	test("todos.updateAttachment guard fires before the UPDATE — NOT_FOUND precedes update(todoAttachments)", () => {
		const src = readSrc(TODOS_ROUTER_PATH);
		const body = extractProcedure(src, "updateAttachment");
		const notFoundIdx = body.indexOf("NOT_FOUND");
		const updateIdx = body.indexOf("update(todoAttachments)");
		expect(notFoundIdx).toBeGreaterThan(-1);
		expect(updateIdx).toBeGreaterThan(-1);
		expect(notFoundIdx).toBeLessThan(updateIdx);
	});
});

// ---------------------------------------------------------------------------
// GWT-IDOR-3 — library.addTag
// ---------------------------------------------------------------------------

describe("GWT-IDOR-3 — library.addTag: innerJoin ownership guard before INSERT", () => {
	/**
	 * GIVEN library.addTag verifies entry ownership via innerJoin to library_sources
	 * WHEN a caller from team B passes an entryId owned by team A
	 * THEN the guard returns empty, throwing NOT_FOUND; the INSERT into library_entry_tags
	 *      never fires
	 *
	 * libraryEntries has NO teamId column — ownership flows through
	 * sourceId -> librarySources.teamId (per notepad gotcha from forge-wire).
	 *
	 * Source evidence required:
	 *   1. addTag body contains `.innerJoin(librarySources,`.
	 *   2. The guard asserts librarySources.teamId against ctx.user.teamId.
	 *   3. TRPCError NOT_FOUND is thrown on guard failure.
	 *   4. NOT_FOUND check precedes the INSERT into libraryEntryTags.
	 */

	test("library.addTag body contains innerJoin(librarySources, ...) for entry ownership", () => {
		const src = readSrc(LIBRARY_ROUTER_PATH);
		const body = extractProcedure(src, "addTag");
		expect(body.length).toBeGreaterThan(0);
		expect(body).toMatch(/\.innerJoin\(\s*librarySources[,\s]/);
	});

	test("library.addTag guard checks librarySources.teamId equals ctx.user.teamId", () => {
		const src = readSrc(LIBRARY_ROUTER_PATH);
		const body = extractProcedure(src, "addTag");
		expect(body).toMatch(/eq\(librarySources\.teamId[\s\S]*?ctx\.user\.teamId/);
	});

	test("library.addTag throws TRPCError NOT_FOUND when ownership check fails", () => {
		const src = readSrc(LIBRARY_ROUTER_PATH);
		const body = extractProcedure(src, "addTag");
		expect(body).toMatch(/TRPCError[^}]*NOT_FOUND/);
	});

	test("library.addTag guard fires before the INSERT — NOT_FOUND precedes insert(libraryEntryTags)", () => {
		const src = readSrc(LIBRARY_ROUTER_PATH);
		const body = extractProcedure(src, "addTag");
		const notFoundIdx = body.indexOf("NOT_FOUND");
		const insertIdx = body.indexOf("insert(libraryEntryTags)");
		expect(notFoundIdx).toBeGreaterThan(-1);
		expect(insertIdx).toBeGreaterThan(-1);
		expect(notFoundIdx).toBeLessThan(insertIdx);
	});
});

// ---------------------------------------------------------------------------
// GWT-IDOR-4 — library.removeTag
// ---------------------------------------------------------------------------

describe("GWT-IDOR-4 — library.removeTag: innerJoin ownership guard before DELETE", () => {
	/**
	 * GIVEN library.removeTag verifies entry ownership via innerJoin to library_sources
	 * WHEN a caller from team B passes an entryId owned by team A
	 * THEN the guard returns empty, throwing NOT_FOUND; the DELETE from library_entry_tags
	 *      never fires
	 */

	test("library.removeTag body contains innerJoin(librarySources, ...) for entry ownership", () => {
		const src = readSrc(LIBRARY_ROUTER_PATH);
		const body = extractProcedure(src, "removeTag");
		expect(body.length).toBeGreaterThan(0);
		expect(body).toMatch(/\.innerJoin\(\s*librarySources[,\s]/);
	});

	test("library.removeTag guard checks librarySources.teamId equals ctx.user.teamId", () => {
		const src = readSrc(LIBRARY_ROUTER_PATH);
		const body = extractProcedure(src, "removeTag");
		expect(body).toMatch(/eq\(librarySources\.teamId[\s\S]*?ctx\.user\.teamId/);
	});

	test("library.removeTag throws TRPCError NOT_FOUND when ownership check fails", () => {
		const src = readSrc(LIBRARY_ROUTER_PATH);
		const body = extractProcedure(src, "removeTag");
		expect(body).toMatch(/TRPCError[^}]*NOT_FOUND/);
	});

	test("library.removeTag guard fires before the DELETE — NOT_FOUND precedes delete(libraryEntryTags)", () => {
		const src = readSrc(LIBRARY_ROUTER_PATH);
		const body = extractProcedure(src, "removeTag");
		const notFoundIdx = body.indexOf("NOT_FOUND");
		const deleteIdx = body.indexOf("delete(libraryEntryTags)");
		expect(notFoundIdx).toBeGreaterThan(-1);
		expect(deleteIdx).toBeGreaterThan(-1);
		expect(notFoundIdx).toBeLessThan(deleteIdx);
	});
});

// ---------------------------------------------------------------------------
// GWT-IDOR-5 — library.linkProject
// ---------------------------------------------------------------------------

describe("GWT-IDOR-5 — library.linkProject: innerJoin ownership guard before INSERT", () => {
	/**
	 * GIVEN library.linkProject verifies entry ownership via innerJoin to library_sources
	 * WHEN a caller from team B passes an entryId owned by team A
	 * THEN the guard returns empty, throwing NOT_FOUND; the INSERT into
	 *      library_entry_projects never fires
	 */

	test("library.linkProject body contains innerJoin(librarySources, ...) for entry ownership", () => {
		const src = readSrc(LIBRARY_ROUTER_PATH);
		const body = extractProcedure(src, "linkProject");
		expect(body.length).toBeGreaterThan(0);
		expect(body).toMatch(/\.innerJoin\(\s*librarySources[,\s]/);
	});

	test("library.linkProject guard checks librarySources.teamId equals ctx.user.teamId", () => {
		const src = readSrc(LIBRARY_ROUTER_PATH);
		const body = extractProcedure(src, "linkProject");
		expect(body).toMatch(/eq\(librarySources\.teamId[\s\S]*?ctx\.user\.teamId/);
	});

	test("library.linkProject throws TRPCError NOT_FOUND when ownership check fails", () => {
		const src = readSrc(LIBRARY_ROUTER_PATH);
		const body = extractProcedure(src, "linkProject");
		expect(body).toMatch(/TRPCError[^}]*NOT_FOUND/);
	});

	test("library.linkProject guard fires before the INSERT — NOT_FOUND precedes insert(libraryEntryProjects)", () => {
		const src = readSrc(LIBRARY_ROUTER_PATH);
		const body = extractProcedure(src, "linkProject");
		const notFoundIdx = body.indexOf("NOT_FOUND");
		const insertIdx = body.indexOf("insert(libraryEntryProjects)");
		expect(notFoundIdx).toBeGreaterThan(-1);
		expect(insertIdx).toBeGreaterThan(-1);
		expect(notFoundIdx).toBeLessThan(insertIdx);
	});
});

// ---------------------------------------------------------------------------
// GWT-IDOR-6 — library.unlinkProject
// ---------------------------------------------------------------------------

describe("GWT-IDOR-6 — library.unlinkProject: innerJoin ownership guard before DELETE", () => {
	/**
	 * GIVEN library.unlinkProject verifies entry ownership via innerJoin to library_sources
	 * WHEN a caller from team B passes an entryId owned by team A
	 * THEN the guard returns empty, throwing NOT_FOUND; the DELETE from
	 *      library_entry_projects never fires
	 */

	test("library.unlinkProject body contains innerJoin(librarySources, ...) for entry ownership", () => {
		const src = readSrc(LIBRARY_ROUTER_PATH);
		const body = extractProcedure(src, "unlinkProject");
		expect(body.length).toBeGreaterThan(0);
		expect(body).toMatch(/\.innerJoin\(\s*librarySources[,\s]/);
	});

	test("library.unlinkProject guard checks librarySources.teamId equals ctx.user.teamId", () => {
		const src = readSrc(LIBRARY_ROUTER_PATH);
		const body = extractProcedure(src, "unlinkProject");
		expect(body).toMatch(/eq\(librarySources\.teamId[\s\S]*?ctx\.user\.teamId/);
	});

	test("library.unlinkProject throws TRPCError NOT_FOUND when ownership check fails", () => {
		const src = readSrc(LIBRARY_ROUTER_PATH);
		const body = extractProcedure(src, "unlinkProject");
		expect(body).toMatch(/TRPCError[^}]*NOT_FOUND/);
	});

	test("library.unlinkProject guard fires before the DELETE — NOT_FOUND precedes delete(libraryEntryProjects)", () => {
		const src = readSrc(LIBRARY_ROUTER_PATH);
		const body = extractProcedure(src, "unlinkProject");
		const notFoundIdx = body.indexOf("NOT_FOUND");
		const deleteIdx = body.indexOf("delete(libraryEntryProjects)");
		expect(notFoundIdx).toBeGreaterThan(-1);
		expect(deleteIdx).toBeGreaterThan(-1);
		expect(notFoundIdx).toBeLessThan(deleteIdx);
	});
});

// ---------------------------------------------------------------------------
// Structural: libraryEntries has no teamId column (ownership via sourceId)
// ---------------------------------------------------------------------------

describe("Structural: libraryEntries teamId ownership flows through librarySources (not direct column)", () => {
	/**
	 * Confirms the invariant documented in the notepad (forge-wire gotcha):
	 * libraryEntries has NO teamId column. Every IDOR guard for library procedures
	 * must join through librarySources to get teamId. A guard that checked
	 * libraryEntries.teamId directly would be silently wrong (column doesn't exist
	 * on that table).
	 */

	test("library.ts: addTag/removeTag/linkProject/unlinkProject do NOT reference libraryEntries.teamId", () => {
		const src = readSrc(LIBRARY_ROUTER_PATH);
		// These four procedures must NOT attempt to filter on libraryEntries.teamId
		// (that column does not exist — it would silently compile but reference undefined)
		for (const proc of [
			"addTag",
			"removeTag",
			"linkProject",
			"unlinkProject",
		]) {
			const body = extractProcedure(src, proc);
			expect(body.length).toBeGreaterThan(0);
			// libraryEntries.teamId is never a valid expression — must not appear
			expect(body).not.toMatch(/libraryEntries\.teamId/);
		}
	});

	test("todos.ts: detach and updateAttachment do NOT attempt to check todoAttachments.teamId (no such column)", () => {
		const src = readSrc(TODOS_ROUTER_PATH);
		// todoAttachments table has no teamId — only todos does.
		// Guards that checked todoAttachments.teamId directly would be wrong.
		for (const proc of ["detach", "updateAttachment"]) {
			const body = extractProcedure(src, proc);
			expect(body.length).toBeGreaterThan(0);
			expect(body).not.toMatch(/todoAttachments\.teamId/);
		}
	});
});
