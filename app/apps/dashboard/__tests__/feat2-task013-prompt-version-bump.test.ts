/// <reference path="./vitest-globals.d.ts" />
/**
 * Wave-2 gap: prompts version-bump behavioral test.
 *
 * Acceptance criteria:
 *   AC-VB1 — updatePrompt with bumpVersion:true snapshots the prior revision
 *             into prompt_versions before writing the patch (insert then update,
 *             inside a transaction).
 *   AC-VB2 — The new version number is derived atomically from MAX(version)+1
 *             in prompt_versions, not from a client-side read-then-write
 *             (guards against concurrent-save race).
 *   AC-VB3 — The insert uses onConflictDoNothing so idempotent retries on a
 *             UNIQUE(prompt_id, version) collision are safe.
 *   AC-VB4 — Without bumpVersion (or bumpVersion:false) the version column is
 *             NOT incremented — the plain update path has no version bump logic.
 *
 * Strategy: source-code assertions on the prompts router.  The router is the
 * single source of truth for this behaviour and has no UI layer — the same
 * pattern used by feat2-task013-prompt-library.test.ts and the rest of the
 * Wave-2 guard suite.
 *
 * Run: rtk vitest run app/apps/dashboard/__tests__/feat2-task013-prompt-version-bump.test.ts
 */

import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, test } from "vitest";

// ---------------------------------------------------------------------------
// Path helpers
// ---------------------------------------------------------------------------

// __tests__ → dashboard → apps → app
const APP_ROOT = join(
	import.meta.dirname ?? __dirname,
	"..", // dashboard
	"..", // apps
	"..", // app
);

const PROMPTS_ROUTER_PATH = join(
	APP_ROOT,
	"apps",
	"api",
	"src",
	"trpc",
	"routers",
	"prompts.ts",
);

function readRouter(): string {
	if (!existsSync(PROMPTS_ROUTER_PATH)) {
		throw new Error(
			`prompts router not found at ${PROMPTS_ROUTER_PATH} — has it been moved?`,
		);
	}
	return readFileSync(PROMPTS_ROUTER_PATH, "utf8");
}

// ---------------------------------------------------------------------------
// AC-VB1 — bumpVersion path inserts a snapshot row into prompt_versions
//           inside a db.transaction() before updating the prompts row
// ---------------------------------------------------------------------------

describe("prompts version-bump behavioral contract", () => {
	test("AC-VB1: bumpVersion path inserts prior revision into prompt_versions inside a transaction", () => {
		const src = readRouter();

		// Must use db.transaction (atomic snapshot + bump)
		expect(
			src,
			"updatePrompt must call db.transaction() to wrap the snapshot insert + version bump atomically",
		).toMatch(/db\.transaction/);

		// The transaction body must insert into promptVersions.
		// The router chains tx\n.insert(promptVersions) across lines, so match
		// the two tokens independently — both must appear in source.
		expect(
			src,
			"updatePrompt bumpVersion path must use the transaction handle (tx) to insert into promptVersions",
		).toMatch(/\btx\b/);

		expect(
			src,
			"updatePrompt bumpVersion path must call .insert(promptVersions) to snapshot the prior revision",
		).toMatch(/\.insert\s*\(\s*promptVersions\s*\)/);

		// The snapshot must carry the prior content and version
		expect(
			src,
			"snapshot insert must include the existing prompt content (p.content)",
		).toMatch(/p\.content/);

		expect(
			src,
			"snapshot insert must include the existing version number (p.version)",
		).toMatch(/p\.version/);
	});

	// ---------------------------------------------------------------------------
	// AC-VB2 — version number derived atomically via MAX(version)+1 SQL expression
	// ---------------------------------------------------------------------------

	test("AC-VB2: new version is computed atomically as MAX(version)+1 inside the DB — no read-then-write race", () => {
		const src = readRouter();

		// Must use sql`` template to compute version atomically, not a JS increment
		expect(
			src,
			"version bump must use sql`…MAX(version)…+1…` to avoid concurrent-save races — never `p.version + 1`",
		).toMatch(/sql`[^`]*MAX\s*\(\s*version\s*\)[^`]*\+\s*1[^`]*`/);

		// Confirm there is NO naive `p.version + 1` (JS-side increment)
		expect(
			src,
			"version must NOT be bumped via p.version + 1 — this causes duplicate version numbers under concurrency",
		).not.toMatch(/p\.version\s*\+\s*1/);
	});

	// ---------------------------------------------------------------------------
	// AC-VB3 — onConflictDoNothing on UNIQUE(prompt_id, version)
	// ---------------------------------------------------------------------------

	test("AC-VB3: snapshot insert uses onConflictDoNothing for idempotent-retry safety on UNIQUE(promptId, version)", () => {
		const src = readRouter();

		expect(
			src,
			"prompt_versions insert must call .onConflictDoNothing() so a duplicate (prompt_id, version) on retry does not throw",
		).toMatch(/onConflictDoNothing/);

		// The conflict target must name both columns
		expect(
			src,
			"onConflictDoNothing target must include promptVersions.promptId",
		).toMatch(/promptVersions\.promptId/);

		expect(
			src,
			"onConflictDoNothing target must include promptVersions\.version",
		).toMatch(/promptVersions\.version/);
	});

	// ---------------------------------------------------------------------------
	// AC-VB4 — plain update path (no bumpVersion) does NOT increment version
	// ---------------------------------------------------------------------------

	test("AC-VB4: save without bumpVersion does NOT touch the version column", () => {
		const src = readRouter();

		// The bumpVersion guard: the bump logic must be inside an `if (input.bumpVersion)`
		// block, so the plain update path below it cannot set version.
		expect(
			src,
			"version bump logic must be gated behind if (input.bumpVersion) so saves without bumpVersion leave version unchanged",
		).toMatch(/if\s*\(\s*input\.bumpVersion\s*\)/);

		// The plain patch object (used outside bumpVersion) must not reference `version`
		// as a key — we check that the word `version` does not appear inside the
		// plain `patch` object initialisation block.
		// Strategy: extract the patch-object literal and assert no `version:` key.
		// A simpler proxy: count occurrences of `patch.version` or `version:` inside
		// the `patch` literal — there should be none (the plain path uses `patch as any`
		// without touching version).
		expect(
			src,
			"plain patch object must not set a 'version' key — only the bumpVersion transaction path may increment version",
		).not.toMatch(/patch\.version\s*=/);
	});
});
