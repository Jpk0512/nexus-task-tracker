/// <reference path="./vitest-globals.d.ts" />
/**
 * TASK-007 (P6) guard: trim Google integrations, analytics, Sentry, notifications.
 *
 * RED stubs — all fail before implementation; go GREEN automatically once Forge:
 *   (a) deletes rest/routers/gmail.ts + rest/routers/google-calendar.ts
 *   (b) removes googleapis dep from all workspace packages
 *   (c) removes @sentry from all workspace packages + config files
 *   (d) removes posthog-js / posthog-node from all workspace packages
 *   (e) stubs or removes @mimir/notifications (no @mimir/integration external-transport imports)
 *
 * Acceptance criteria pinned:
 *   1. zero googleapis references across the whole app/ tree (excl. node_modules/dist/__tests__)
 *   2. zero @sentry references across the whole app/ tree (catches root app/package.json @sentry/cli)
 *   3. zero posthog references across the whole app/ tree
 *   4. rest/routers/gmail.ts router file DELETED
 *   5. rest/routers/google-calendar.ts router file DELETED
 *   6. @mimir/notifications/src/index.ts does NOT import @mimir/integration/* (no external-transport)
 *
 * Excluded from scans:
 *   - node_modules, .next, dist, build, .turbo, __tests__ directories
 *   - bun.lock / bun.lockb / yarn.lock (lock files)
 *   - This file itself
 */

import { existsSync, readdirSync, readFileSync } from "node:fs";
import { join, relative } from "node:path";
import { describe, expect, test } from "vitest";

// ---------------------------------------------------------------------------
// Root paths
// ---------------------------------------------------------------------------

// __tests__ → dashboard → apps → app
const APP_ROOT = join(
	import.meta.dirname ?? __dirname,
	"..", // dashboard
	"..", // apps
	"..", // app
);

/** Absolute path of this guard test — always excluded from scans */
const THIS_FILE = join(
	import.meta.dirname ?? __dirname,
	"feat-1-integrations-trim.test.ts",
);

// ---------------------------------------------------------------------------
// Filesystem walk utility
// ---------------------------------------------------------------------------

const EXCLUDED_DIRS = new Set([
	"node_modules",
	".next",
	"dist",
	"build",
	".turbo",
	"__tests__",
]);

function isExcludedDir(name: string): boolean {
	return EXCLUDED_DIRS.has(name) || name.startsWith(".pre-nexus");
}

const EXCLUDED_FILE_NAMES = new Set(["bun.lock", "bun.lockb", "yarn.lock"]);

function* walkText(dir: string): Generator<string> {
	let entries: import("node:fs").Dirent<string>[];
	try {
		entries = readdirSync(dir, { withFileTypes: true, encoding: "utf8" });
	} catch {
		return;
	}

	for (const entry of entries) {
		if (entry.isDirectory()) {
			if (isExcludedDir(entry.name)) continue;
			yield* walkText(join(dir, entry.name));
		} else if (entry.isFile()) {
			if (EXCLUDED_FILE_NAMES.has(entry.name)) continue;
			const filePath = join(dir, entry.name);
			if (filePath === THIS_FILE) continue;
			yield filePath;
		}
	}
}

function scanForPattern(
	rootDirs: string[],
	pattern: RegExp,
): Array<{ file: string; lines: number[] }> {
	const hits: Array<{ file: string; lines: number[] }> = [];

	for (const rootDir of rootDirs) {
		for (const filePath of walkText(rootDir)) {
			let content: string;
			try {
				content = readFileSync(filePath, "utf8");
			} catch {
				continue;
			}

			if (!pattern.test(content)) continue;

			const matchedLines: number[] = [];
			content.split("\n").forEach((line, idx) => {
				if (pattern.test(line)) matchedLines.push(idx + 1);
			});

			if (matchedLines.length > 0) {
				hits.push({ file: filePath, lines: matchedLines });
			}
		}
	}

	return hits;
}

function formatHits(hits: Array<{ file: string; lines: number[] }>): string {
	return hits
		.map(
			({ file, lines }) =>
				`  ${relative(APP_ROOT, file)} (line${lines.length > 1 ? "s" : ""} ${lines.join(", ")})`,
		)
		.join("\n");
}

// ---------------------------------------------------------------------------
// Scan root: the whole app/ tree (catches root-level package.json, scripts/,
// docker-compose files, etc. — not just app/apps + app/packages).
// ---------------------------------------------------------------------------

const SCAN_ROOTS = [APP_ROOT];

// ---------------------------------------------------------------------------
// 1. googleapis — zero references
// ---------------------------------------------------------------------------

describe("TASK-007 P6 integrations-trim guard", () => {
	test("zero googleapis references across the whole app/ tree", () => {
		const GOOGLEAPIS_RE = /googleapis/;
		const hits = scanForPattern(SCAN_ROOTS, GOOGLEAPIS_RE);

		const message =
			hits.length === 0
				? "No matches"
				: `Found ${hits.length} file(s) with googleapis references across the whole app/ tree:\n` +
					formatHits(hits);

		expect(hits, message).toHaveLength(0);
	});

	// ---------------------------------------------------------------------------
	// 2. @sentry — zero references (includes root app/package.json @sentry/cli)
	// ---------------------------------------------------------------------------

	test("zero @sentry references across the whole app/ tree", () => {
		const SENTRY_RE = /@sentry/;
		const hits = scanForPattern(SCAN_ROOTS, SENTRY_RE);

		const message =
			hits.length === 0
				? "No matches"
				: `Found ${hits.length} file(s) with @sentry references across the whole app/ tree:\n` +
					formatHits(hits);

		expect(hits, message).toHaveLength(0);
	});

	// ---------------------------------------------------------------------------
	// 3. posthog — zero references
	// ---------------------------------------------------------------------------

	test("zero posthog references across the whole app/ tree", () => {
		const POSTHOG_RE = /posthog/;
		const hits = scanForPattern(SCAN_ROOTS, POSTHOG_RE);

		const message =
			hits.length === 0
				? "No matches"
				: `Found ${hits.length} file(s) with posthog references across the whole app/ tree:\n` +
					formatHits(hits);

		expect(hits, message).toHaveLength(0);
	});

	// ---------------------------------------------------------------------------
	// 4. Gmail REST router deleted
	// ---------------------------------------------------------------------------

	test("app/apps/api/src/rest/routers/gmail.ts is deleted", () => {
		const gmailRouterPath = join(
			APP_ROOT,
			"apps",
			"api",
			"src",
			"rest",
			"routers",
			"gmail.ts",
		);
		const exists = existsSync(gmailRouterPath);
		expect(
			exists,
			"app/apps/api/src/rest/routers/gmail.ts still exists — delete it and remove from router index",
		).toBe(false);
	});

	// ---------------------------------------------------------------------------
	// 5. Google Calendar REST router deleted
	// ---------------------------------------------------------------------------

	test("app/apps/api/src/rest/routers/google-calendar.ts is deleted", () => {
		const gcalRouterPath = join(
			APP_ROOT,
			"apps",
			"api",
			"src",
			"rest",
			"routers",
			"google-calendar.ts",
		);
		const exists = existsSync(gcalRouterPath);
		expect(
			exists,
			"app/apps/api/src/rest/routers/google-calendar.ts still exists — delete it and remove from router index",
		).toBe(false);
	});

	// ---------------------------------------------------------------------------
	// 6. @mimir/notifications/src/index.ts — no @mimir/integration external-transport imports
	//    After P6, sendNotification must be a pure stub with no Google/chat transport
	//    binding — removing the @mimir/integration import chain (which drags in googleapis).
	// ---------------------------------------------------------------------------

	test("@mimir/notifications/src/index.ts does not import from @mimir/integration", () => {
		const notificationsIndexPath = join(
			APP_ROOT,
			"packages",
			"notifications",
			"src",
			"index.ts",
		);

		let content: string;
		try {
			content = readFileSync(notificationsIndexPath, "utf8");
		} catch {
			// File gone entirely = criterion met (notifications package removed)
			return;
		}

		const INTEGRATION_IMPORT_RE = /@mimir\/integration/;
		const hasIntegrationImport = INTEGRATION_IMPORT_RE.test(content);

		expect(
			hasIntegrationImport,
			"@mimir/notifications/src/index.ts still imports from @mimir/integration — " +
				"sendNotification must be stubbed with no external-transport dependency " +
				"(remove sendMattermostNotification + sendWhatsappNotification imports)",
		).toBe(false);
	});
});
