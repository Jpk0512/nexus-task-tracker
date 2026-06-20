/// <reference path="./vitest-globals.d.ts" />
/**
 * TASK-013 — Prompt library: FAILING stubs (stubs phase).
 *
 * Four acceptance criteria from docs/features/FEAT-002-next-features.md §TASK-013:
 *
 *  AC(a) — kbuddy prompt_product row is seeded and idempotent (running the seed
 *           twice yields exactly ONE kbuddy row, keyed on slug not PK id)
 *  AC(b) — project picker set/clear persists projectId on reload
 *           (getPromptBySlug must return projectId; edit-view must wire setProject)
 *  AC(c) — list-view shows a project badge when a prompt has projectId set
 *           (getPrompts must select projectId; PromptRow type must carry it)
 *  AC(d) — setProject rejects a projectId that does not belong to the caller's team
 *
 * Strategy: source-code assertions against the relevant files.
 * These tests are intentionally RED now because the implementation is absent.
 * They go GREEN naturally once Forge/Pipeline land the implementation — no test
 * edits required.
 *
 * Run: rtk vitest run app/apps/dashboard/__tests__/feat2-task013-prompt-library.test.ts
 */

import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, test } from "vitest";

// ---------------------------------------------------------------------------
// Path helpers
// ---------------------------------------------------------------------------

// __tests__ → dashboard → apps → app (monorepo root)
const APP_ROOT = join(
	import.meta.dirname ?? __dirname,
	"..", // dashboard
	"..", // apps
	"..", // app
);

const SEED_PATH = join(APP_ROOT, "packages", "db", "src", "seed-local-dev.ts");

const PROMPTS_ROUTER_PATH = join(
	APP_ROOT,
	"apps",
	"api",
	"src",
	"trpc",
	"routers",
	"prompts.ts",
);

const EDIT_VIEW_PATH = join(
	APP_ROOT,
	"apps",
	"dashboard",
	"src",
	"components",
	"prompts",
	"edit-view.tsx",
);

const LIST_VIEW_PATH = join(
	APP_ROOT,
	"apps",
	"dashboard",
	"src",
	"components",
	"prompts",
	"list-view.tsx",
);

function readFile(p: string): string {
	if (!existsSync(p)) throw new Error(`Expected file not found: ${p}`);
	return readFileSync(p, "utf8");
}

// ---------------------------------------------------------------------------
// AC(a) — kbuddy prompt_product seeded and idempotent
// ---------------------------------------------------------------------------

describe("AC(a) — seed-local-dev.ts inserts a kbuddy prompt_product row", () => {
	/**
	 * GIVEN a freshly provisioned team with no manually created prompt_products
	 * WHEN the seed (seed-local-dev.ts) runs
	 * THEN exactly one kbuddy prompt_product row is present — the seed inserts
	 * it and subsequent runs do not create duplicates.
	 *
	 * Source-code checks:
	 *   1. The seed imports or references the promptProducts table variable.
	 *   2. The seed inserts a row mentioning "kbuddy".
	 *   3. The conflict resolution uses onConflictDoNothing targeting
	 *      promptProducts.id (the PK) — the seed hard-codes id "ld-pp-kbuddy",
	 *      so ON CONFLICT (id) DO NOTHING is genuinely idempotent: the same PK
	 *      never inserts twice.
	 *
	 * Why id (not slug): prompt_products has UNIQUE(team_id, slug) but NO
	 * standalone UNIQUE(slug). Targeting slug alone would throw Postgres error
	 * 42P10 ("there is no unique constraint matching the given keys"). The fixed
	 * id "ld-pp-kbuddy" makes the PK conflict target the correct idempotency
	 * key. (DEC-007 / LSN-009)
	 *
	 * Behavioral execution limit: this Vitest suite runs without a live Postgres
	 * connection, so we cannot execute the seed and assert INSERT 0 1 / INSERT 0 0
	 * directly. The source-regex assertions below are the highest-fidelity check
	 * available within this harness. A separate integration/e2e test (outside this
	 * file) would be needed to assert row-count idempotency against a real DB.
	 */
	test("seed-local-dev.ts contains an insert into promptProducts for kbuddy", () => {
		const seed = readFile(SEED_PATH);

		// Must reference the promptProducts table
		expect(seed).toMatch(/promptProducts/);

		// Must insert a row with slug/name "kbuddy"
		expect(seed).toMatch(/kbuddy/i);

		// The insert must target the promptProducts table (not just a comment)
		expect(seed).toMatch(/\.insert\(promptProducts\)/);
	});

	test("kbuddy insert uses onConflictDoNothing targeting promptProducts.id (idempotency keyed on fixed PK)", () => {
		const seed = readFile(SEED_PATH);

		// Locate the first kbuddy mention
		const kbuddyIdx = seed.search(/kbuddy/i);
		expect(kbuddyIdx).toBeGreaterThan(-1);

		// Within 500 chars after the kbuddy mention there must be
		// an onConflictDoNothing call that specifies .id as the conflict target.
		// The seed hard-codes id "ld-pp-kbuddy", so ON CONFLICT (id) DO NOTHING
		// is idempotent. Targeting promptProducts.slug would fail 42P10 because
		// slug has no standalone unique constraint (only UNIQUE(team_id, slug)).
		const region = seed.slice(kbuddyIdx, kbuddyIdx + 500);
		expect(region).toMatch(/onConflictDoNothing/);
		expect(region).toMatch(/promptProducts\.id/);
	});
});

// ---------------------------------------------------------------------------
// AC(b) — project picker set/clear persists projectId on reload
// ---------------------------------------------------------------------------

describe("AC(b) — project picker persists and clears projectId across reload", () => {
	/**
	 * GIVEN a prompt open in the edit-view and a project picker in the rail
	 * WHEN the owner selects a project, reloads the page, then clears the project
	 * THEN the selected projectId persists across reload (getPromptBySlug returns
	 * it) and clearing it nulls the field.
	 *
	 * Source-code checks:
	 *   1. getPromptBySlug in prompts.ts selects prompts.projectId so the field
	 *      is available after a page reload.
	 *   2. edit-view.tsx calls trpc.prompts.setProject (the mutation).
	 *   3. edit-view.tsx reads prompt.projectId to pre-populate the picker.
	 */
	test("prompts router getPromptBySlug selects projectId so reload can restore it", () => {
		const router = readFile(PROMPTS_ROUTER_PATH);

		const getBySlugIdx = router.indexOf("getPromptBySlug:");
		expect(getBySlugIdx).toBeGreaterThan(-1);

		// Within the getPromptBySlug procedure body (up to 800 chars after its
		// declaration) the select object must include projectId.
		const region = router.slice(getBySlugIdx, getBySlugIdx + 800);
		expect(region).toMatch(/projectId\s*:/);
	});

	test("edit-view.tsx wires the setProject mutation from trpc.prompts", () => {
		const editView = readFile(EDIT_VIEW_PATH);

		// The component must reference the setProject tRPC procedure
		expect(editView).toMatch(/prompts\.setProject/);

		// It must obtain the mutation handle (mutationOptions or mutate)
		expect(editView).toMatch(/setProject\.(mutationOptions|mutate)/);
	});

	test("edit-view.tsx reads prompt.projectId to pre-populate the picker on load", () => {
		const editView = readFile(EDIT_VIEW_PATH);

		// The component must read projectId from the prompt object so the picker
		// reflects the persisted selection after reload
		expect(editView).toMatch(/prompt\.projectId/);
	});
});

// ---------------------------------------------------------------------------
// AC(c) — list-view shows a project badge when prompt has a projectId
// ---------------------------------------------------------------------------

describe("AC(c) — list-view renders a project badge for prompts with projectId set", () => {
	/**
	 * GIVEN a prompt list where some prompts have projectId set and some do not
	 * WHEN the owner views the list
	 * THEN each row with a projectId shows a visible project badge; rows without
	 * one show no badge.
	 *
	 * Source-code checks:
	 *   1. PromptRow type in list-view.tsx includes projectId.
	 *   2. list-view.tsx conditionally renders a Badge/chip keyed on p.projectId.
	 *   3. getPrompts in prompts.ts selects prompts.projectId so the field
	 *      reaches the list-view component.
	 */
	test("list-view.tsx PromptRow type includes projectId field", () => {
		const listView = readFile(LIST_VIEW_PATH);

		const promptRowTypeIdx = listView.indexOf("type PromptRow");
		expect(promptRowTypeIdx).toBeGreaterThan(-1);

		// Scan the type body (up to 200 chars) for projectId
		const typeRegion = listView.slice(promptRowTypeIdx, promptRowTypeIdx + 200);
		expect(typeRegion).toMatch(/projectId/);
	});

	test("list-view.tsx conditionally renders a project badge when p.projectId is set", () => {
		const listView = readFile(LIST_VIEW_PATH);

		// A guard of the form {p.projectId && ...} or {p.projectId ? ... : null}
		expect(listView).toMatch(/p\.projectId/);

		// Around that guard there must be a Badge or project-related element
		const projectIdIdx = listView.indexOf("p.projectId");
		expect(projectIdIdx).toBeGreaterThan(-1);

		const region = listView.slice(
			Math.max(0, projectIdIdx - 50),
			projectIdIdx + 300,
		);
		expect(region).toMatch(/Badge|badge|project/i);
	});

	test("prompts router getPrompts selects projectId so list-view can render the badge", () => {
		const router = readFile(PROMPTS_ROUTER_PATH);

		const getPromptsIdx = router.indexOf("getPrompts:");
		expect(getPromptsIdx).toBeGreaterThan(-1);

		// Within the getPrompts procedure body (up to 600 chars) projectId
		// must appear in the select list
		const region = router.slice(getPromptsIdx, getPromptsIdx + 600);
		expect(region).toMatch(/projectId\s*:/);
	});
});

// ---------------------------------------------------------------------------
// AC(d) — setProject rejects a projectId from a different team
// ---------------------------------------------------------------------------

describe("AC(d) — setProject rejects a projectId that does not belong to the caller's team", () => {
	/**
	 * GIVEN the setProject tRPC mutation
	 * WHEN called with a projectId that belongs to a different team
	 * THEN the call is rejected (BAD_REQUEST or UNAUTHORIZED) — it must NOT
	 * silently write a cross-team projectId onto the prompt row.
	 *
	 * Source-code check:
	 *   The setProject mutation must verify that the supplied projectId belongs
	 *   to the caller's team before executing the UPDATE — evidence of a projects
	 *   table lookup inside the setProject body that checks teamId against
	 *   ctx.user.teamId.
	 *
	 * Implementation note: null clears the field and is allowed without a team
	 * membership check (clearing is always safe). Only non-null projectId values
	 * require the ownership check.
	 */
	test("setProject mutation in prompts.ts verifies projectId team membership before writing", () => {
		const router = readFile(PROMPTS_ROUTER_PATH);

		// Locate the setProject procedure body
		const setProjectIdx = router.indexOf("setProject:");
		expect(setProjectIdx).toBeGreaterThan(-1);

		// Within 800 chars of the setProject declaration there must be a
		// query that joins or selects from a projects table (or a projects
		// reference variable) and checks teamId — this is the ownership guard.
		// Without it, any projectId can be written regardless of team.
		const region = router.slice(setProjectIdx, setProjectIdx + 800);

		// Must reference a projects table or projects variable (the ownership lookup)
		expect(region).toMatch(/projects/);

		// Must check teamId (the caller's team) in the ownership lookup
		expect(region).toMatch(/teamId/);

		// The guard must throw BAD_REQUEST (or UNAUTHORIZED/FORBIDDEN) when the
		// projectId is from a different team — "BAD_REQUEST" or "FORBIDDEN" must
		// appear in the setProject body
		expect(region).toMatch(/BAD_REQUEST|FORBIDDEN|UNAUTHORIZED/);
	});
});
