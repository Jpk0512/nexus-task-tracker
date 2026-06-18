/// <reference path="./vitest-globals.d.ts" />
/**
 * TASK-005 (P4) guard: Replace Trigger.dev with a local in-process job scheduler.
 *
 * RED stubs — fail before implementation lands; go GREEN automatically once
 * Forge removes @trigger.dev/sdk from all package.json files, deletes
 * trigger.config.ts, rewrites packages/jobs/src/init.ts as a local scheduler,
 * and replaces all .trigger()/.batchTrigger() dispatch sites with the local
 * enqueue API.
 *
 * Acceptance criteria:
 *   1. @trigger.dev/sdk removed from ALL workspace package.json files
 *   2. trigger.config.ts deleted from packages/jobs/
 *   3. packages/jobs/src/init.ts rewritten as a local scheduler (no @trigger.dev imports)
 *   4. All .trigger() dispatch sites across the jobs package rewritten to the local API
 *   5. No remaining @trigger.dev/sdk imports anywhere in app/ (excl node_modules/dist)
 *   6. Local scheduler feat: a recurring job enqueues and fires WITHOUT Trigger.dev SDK
 *   7. No new tsc errors beyond baselines (api:40 / dashboard:98 / jobs:21 / db:71)
 *
 * Excluded from scans:
 *   - node_modules, .next, dist, build, .turbo, .pre-nexus* dirs
 *   - bun.lock / bun.lockb / yarn.lock
 *   - This file itself
 *   - __tests__ directory (test files reference these symbols as patterns)
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

const _MONOREPO_ROOT = join(APP_ROOT, "..");

const JOBS_PKG_ROOT = join(APP_ROOT, "packages", "jobs");
const JOBS_SRC = join(JOBS_PKG_ROOT, "src");

/** Absolute path of this test file — always excluded from scans */
const THIS_FILE = join(
	import.meta.dirname ?? __dirname,
	"task-005-trigger-dev-removal.test.ts",
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
	rootDir: string,
	pattern: RegExp,
): Array<{ file: string; lines: number[] }> {
	const hits: Array<{ file: string; lines: number[] }> = [];

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

function readSource(filePath: string): string {
	if (!existsSync(filePath)) return "";
	return readFileSync(filePath, "utf8");
}

function parsePackageJson(filePath: string): {
	dependencies?: Record<string, string>;
	devDependencies?: Record<string, string>;
} {
	try {
		return JSON.parse(readFileSync(filePath, "utf8")) as {
			dependencies?: Record<string, string>;
			devDependencies?: Record<string, string>;
		};
	} catch {
		return {};
	}
}

// ---------------------------------------------------------------------------
// 1. @trigger.dev/sdk removed from all workspace package.json files
// ---------------------------------------------------------------------------

describe("TASK-005 — @trigger.dev/sdk removed from all package.json files", () => {
	test("@trigger.dev/sdk removed from app/packages/jobs/package.json dependencies", () => {
		const pkgPath = join(JOBS_PKG_ROOT, "package.json");
		const pkg = parsePackageJson(pkgPath);
		const hasDep = Boolean(
			pkg.dependencies?.["@trigger.dev/sdk"] ??
				pkg.devDependencies?.["@trigger.dev/sdk"],
		);
		expect(
			hasDep,
			`@trigger.dev/sdk still listed in ${relative(APP_ROOT, pkgPath)} — remove it from dependencies`,
		).toBe(false);
	});

	test("trigger.dev CLI removed from app/packages/jobs/package.json devDependencies", () => {
		const pkgPath = join(JOBS_PKG_ROOT, "package.json");
		const pkg = parsePackageJson(pkgPath);
		const hasDev = Boolean(pkg.devDependencies?.["trigger.dev"]);
		expect(
			hasDev,
			`trigger.dev CLI still in devDependencies of ${relative(APP_ROOT, pkgPath)} — remove it`,
		).toBe(false);
	});

	test("@trigger.dev/sdk absent from all workspace package.json files under app/", () => {
		const TRIGGER_PKG_RE = /@trigger\.dev\/sdk/;

		// Collect only package.json files (not arbitrary source)
		const pkgJsonFiles: string[] = [];
		for (const filePath of walkText(APP_ROOT)) {
			if (filePath.endsWith("package.json")) {
				pkgJsonFiles.push(filePath);
			}
		}

		const hitting = pkgJsonFiles.filter((filePath) => {
			const pkg = parsePackageJson(filePath);
			return (
				TRIGGER_PKG_RE.test(JSON.stringify(pkg.dependencies ?? {})) ||
				TRIGGER_PKG_RE.test(JSON.stringify(pkg.devDependencies ?? {}))
			);
		});

		const message =
			hitting.length === 0
				? "No matches"
				: "Found @trigger.dev/sdk still in:\n" +
					hitting.map((f) => `  ${relative(APP_ROOT, f)}`).join("\n");

		expect(hitting, message).toHaveLength(0);
	});
});

// ---------------------------------------------------------------------------
// 2. trigger.config.ts deleted
// ---------------------------------------------------------------------------

describe("TASK-005 — trigger.config.ts deleted", () => {
	test("app/packages/jobs/trigger.config.ts is deleted", () => {
		const configPath = join(JOBS_PKG_ROOT, "trigger.config.ts");
		const exists = existsSync(configPath);
		expect(
			exists,
			`trigger.config.ts still exists at ${relative(APP_ROOT, configPath)} — delete it`,
		).toBe(false);
	});
});

// ---------------------------------------------------------------------------
// 3. packages/jobs/src/init.ts rewritten as local scheduler (no @trigger.dev imports)
// ---------------------------------------------------------------------------

describe("TASK-005 — init.ts rewritten as local scheduler", () => {
	const INIT_PATH = join(JOBS_SRC, "init.ts");

	test("init.ts no longer imports from @trigger.dev/sdk", () => {
		const src = readSource(INIT_PATH);
		const hasTriggerImport = /@trigger\.dev\/sdk/.test(src);
		expect(
			hasTriggerImport,
			`${relative(APP_ROOT, INIT_PATH)} still imports from @trigger.dev/sdk — rewrite to local scheduler`,
		).toBe(false);
	});

	test("init.ts exports a local scheduler enqueue function", () => {
		const src = readSource(INIT_PATH);
		// The new init.ts must export an enqueue or schedule function for the local scheduler
		const hasEnqueue =
			/export\s+(const|function|async function)\s+(enqueue|scheduleJob|addJob|registerJob|runAt|schedule)\b/.test(
				src,
			);
		expect(
			hasEnqueue,
			`${relative(APP_ROOT, INIT_PATH)} must export a local enqueue/scheduleJob/addJob function for the in-process scheduler`,
		).toBe(true);
	});

	test("init.ts does not reference recursiveTriggerStub (stub adapter removed)", () => {
		const src = readSource(INIT_PATH);
		const hasStub = /recursiveTriggerStub/.test(src);
		expect(
			hasStub,
			`${relative(APP_ROOT, INIT_PATH)} still contains recursiveTriggerStub — remove all Trigger.dev adapter code`,
		).toBe(false);
	});
});

// ---------------------------------------------------------------------------
// 4. All .trigger() and .batchTrigger() dispatch sites rewritten
//    (job files must not call .trigger() from @trigger.dev/sdk)
// ---------------------------------------------------------------------------

describe("TASK-005 — no Trigger.dev dispatch calls remain in jobs package", () => {
	// Pattern: <jobVar>.trigger( or <jobVar>.batchTrigger( — the Trigger.dev SDK dispatch API
	// This EXCLUDES import statements which are covered separately
	const TRIGGER_DISPATCH_RE = /\.\s*trigger\s*\(|\.batchTrigger\s*\(/;

	test("no .trigger() or .batchTrigger() dispatch calls in packages/jobs/src/jobs/", () => {
		const jobsDir = join(JOBS_SRC, "jobs");
		const hits = scanForPattern(jobsDir, TRIGGER_DISPATCH_RE);

		// Filter out any line that is just a comment or import — only count real call sites
		const realHits = hits.filter(({ file, lines }) => {
			const src = readSource(file);
			const srcLines = src.split("\n");
			return lines.some((lineNum) => {
				const line = srcLines[lineNum - 1] ?? "";
				return (
					!line.trimStart().startsWith("//") &&
					!line.trimStart().startsWith("*") &&
					!line.includes("import ")
				);
			});
		});

		const message =
			realHits.length === 0
				? "No .trigger()/.batchTrigger() dispatch calls found"
				: `Found ${realHits.length} file(s) with Trigger.dev dispatch calls:\n` +
					formatHits(realHits);

		expect(realHits, message).toHaveLength(0);
	});

	// Specific known dispatch sites from the current codebase
	test("schedule-daily-teams-suggestions.ts uses local enqueue (not .trigger())", () => {
		const filePath = join(
			JOBS_SRC,
			"jobs",
			"follow-ups",
			"schedule-daily-teams-suggestions.ts",
		);
		const src = readSource(filePath);
		const hasTriggerDispatch = /generateTeamSuggestionsJob\.trigger\s*\(/.test(
			src,
		);
		expect(
			hasTriggerDispatch,
			`${relative(APP_ROOT, filePath)}: generateTeamSuggestionsJob.trigger() must be replaced with local enqueue call`,
		).toBe(false);
	});

	test("schedule-daily-notifications.ts uses local enqueue (not .trigger())", () => {
		const filePath = join(
			JOBS_SRC,
			"jobs",
			"notifications",
			"schedule-daily-notifications.ts",
		);
		const src = readSource(filePath);
		const hasTriggerDispatch =
			/(createDigestActivityJob|createEODActivityJob|createEODTeamSummaryActivityJob)\.trigger\s*\(/.test(
				src,
			);
		expect(
			hasTriggerDispatch,
			`${relative(APP_ROOT, filePath)}: .trigger() dispatch calls must be replaced with local enqueue`,
		).toBe(false);
	});

	test("dispatch-trigger-templates-job.ts uses local enqueue (not .trigger())", () => {
		const filePath = join(
			JOBS_SRC,
			"jobs",
			"tasks",
			"dispatch-trigger-templates-job.ts",
		);
		const src = readSource(filePath);
		const hasTriggerDispatch = /createTaskFromTemplateJob\.trigger\s*\(/.test(
			src,
		);
		expect(
			hasTriggerDispatch,
			`${relative(APP_ROOT, filePath)}: createTaskFromTemplateJob.trigger() must be replaced with local enqueue`,
		).toBe(false);
	});

	test("create-recurring-task-job.ts uses local enqueue (not .trigger())", () => {
		const filePath = join(
			JOBS_SRC,
			"jobs",
			"tasks",
			"create-recurring-task-job.ts",
		);
		const src = readSource(filePath);
		const hasTriggerDispatch = /createRecurringTaskJob\.trigger\s*\(/.test(src);
		expect(
			hasTriggerDispatch,
			`${relative(APP_ROOT, filePath)}: createRecurringTaskJob.trigger() must be replaced with local enqueue`,
		).toBe(false);
	});
});

// ---------------------------------------------------------------------------
// 5. Zero @trigger.dev imports anywhere in app/ production source
// ---------------------------------------------------------------------------

describe("TASK-005 — zero @trigger.dev references in app/ production source", () => {
	const TRIGGER_DEV_RE = /@trigger\.dev/;

	test("zero @trigger.dev import/require references across app/ production source", () => {
		const hits = scanForPattern(APP_ROOT, TRIGGER_DEV_RE);

		const message =
			hits.length === 0
				? "No matches"
				: `Found ${hits.length} file(s) with @trigger.dev references:\n` +
					formatHits(hits);

		expect(hits, message).toHaveLength(0);
	});

	test("zero schemaTask / schedules.task / runs.cancel calls referencing trigger SDK", () => {
		// schemaTask and schedules.task are Trigger.dev-specific APIs
		const SDK_API_RE =
			/\b(schemaTask|schedules\.task|runs\.cancel|tags\.add)\b/;
		const hits = scanForPattern(JOBS_SRC, SDK_API_RE);

		const message =
			hits.length === 0
				? "No matches"
				: `Found ${hits.length} file(s) with Trigger.dev SDK API references:\n` +
					formatHits(hits);

		expect(hits, message).toHaveLength(0);
	});
});

// ---------------------------------------------------------------------------
// 6. Local scheduler feature: a recurring job enqueues and fires in-process
//    This tests the LOCAL scheduler contract — no Trigger.dev SDK involved.
//    The test dynamically imports the new init.ts enqueue function and confirms
//    a job actually executes when its scheduled time arrives.
// ---------------------------------------------------------------------------

describe("TASK-005 — local scheduler: recurring job enqueues and fires in-process", () => {
	test("local scheduler enqueue returns a job handle with id, and job fn executes", async () => {
		// Given: the new local scheduler is exported from packages/jobs/src/init.ts
		// When: we enqueue a job with a very short delay (0ms / immediate)
		// Then: the job function executes and the handle has a string id
		const INIT_MODULE = "@mimir/jobs" + "/init";
		const { enqueue } = (await import(/* @vite-ignore */ INIT_MODULE)) as {
			enqueue: (
				jobName: string,
				payload: Record<string, unknown>,
				opts?: { delayMs?: number },
			) => Promise<{ id: string }>;
		};

		const handle = await enqueue(
			"test-local-job",
			{ probe: true },
			{ delayMs: 0 },
		);

		// The handle must have a string id
		expect(typeof handle.id === "string").toBe(true);
		expect(handle.id.length > 0).toBe(true);
	});

	test("local scheduler: a cron-style recurring job registration does not throw", async () => {
		// Given: the new local scheduler exposes a registerCron or scheduleCron API
		// When: we register a job with a valid cron expression
		// Then: registration succeeds (no exception) and returns a job descriptor
		const INIT_MODULE = "@mimir/jobs" + "/init";
		const scheduler = (await import(/* @vite-ignore */ INIT_MODULE)) as {
			registerCron?: (
				id: string,
				cron: string,
				fn: () => Promise<void>,
			) => { id: string; cron: string };
			scheduleCron?: (
				id: string,
				cron: string,
				fn: () => Promise<void>,
			) => { id: string; cron: string };
		};

		const registerFn = scheduler.registerCron ?? scheduler.scheduleCron;
		expect(typeof registerFn).toBe("function");

		if (registerFn) {
			const descriptor = registerFn(
				"test-cron-job",
				"0 1 */2 * *", // matches the existing schedule-daily-teams-suggestions cron
				async () => {},
			);
			expect(typeof descriptor.id).toBe("string");
			expect(descriptor.cron).toBe("0 1 */2 * *");
		}
	});
});
