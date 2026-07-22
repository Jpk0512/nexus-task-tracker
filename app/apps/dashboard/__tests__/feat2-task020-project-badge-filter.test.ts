/// <reference path="./vitest-globals.d.ts" />
/**
 * TASK-020 — Prompt list: project badge is a clickable project-filter.
 *
 * Acceptance criteria (from docs/design/PROMPT-PROJECT-PICKER-SPEC.md §3,
 * docs/design/TASK-013-project-picker-spec.md §Component B):
 *
 *   AC-1  The project badge in each prompt row is rendered as a <button>
 *         (not a <span>) so that it is keyboard-accessible and click-targetable.
 *
 *   AC-2  PromptListView holds a `projectFilter` (or `activeProjectId` /
 *         `projectFilterId`) state variable that can be set to a projectId string.
 *
 *   AC-3  The `filtered` memo (or equivalent derived list) filters prompts by
 *         `projectFilter` when it is set — i.e., it references `projectId` as a
 *         filter axis in addition to the existing search/sort.
 *
 *   AC-4  When `projectFilter` is active, a filter-active pill is rendered in the
 *         list header (the pill must include a clear affordance — ✕ or "clear").
 *
 * Strategy: source-code assertions against list-view.tsx.
 * These tests are RED now because the implementation is absent.
 * They go GREEN automatically once Forge lands the implementation.
 *
 * Run:
 *   cd app && bun vitest run apps/dashboard/__tests__/feat2-task020-project-badge-filter.test.ts
 */

import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, test } from "vitest";

// ---------------------------------------------------------------------------
// Path helpers (mirrors feat2-task013-prompt-library.test.ts convention)
// ---------------------------------------------------------------------------

// __tests__ → dashboard → apps → app (monorepo root)
const APP_ROOT = join(
	import.meta.dirname ?? __dirname,
	"..", // dashboard
	"..", // apps
	"..", // app
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

function readListView(): string {
	if (!existsSync(LIST_VIEW_PATH)) {
		throw new Error(`list-view.tsx not found at: ${LIST_VIEW_PATH}`);
	}
	return readFileSync(LIST_VIEW_PATH, "utf8");
}

// ---------------------------------------------------------------------------
// AC-1 — Project badge is a <button> (clickable, not just a <span>)
// ---------------------------------------------------------------------------

describe("AC-1 — project badge is rendered as a <button> element", () => {
	/**
	 * GIVEN a prompt row that has p.projectId set
	 * WHEN the list renders
	 * THEN the project badge is a <button> (not a <span>) so that clicking
	 *      it triggers the project filter.
	 *
	 * The spec (PROMPT-PROJECT-PICKER-SPEC.md §3 Forge note) says:
	 *   "The ProjectBadge wraps a <button> (for click-to-filter) around Badge markup."
	 * Currently list-view.tsx wraps the badge in a <span> — this test will FAIL
	 * until Forge replaces that <span> with a <button>.
	 */
	test("project badge element in list-view.tsx is a <button>, not a bare <span>", () => {
		const src = readListView();

		// Find the region around p.projectId conditional render
		const projectIdIdx = src.indexOf("p.projectId &&");
		expect(projectIdIdx).toBeGreaterThan(-1);

		// Look at the next 400 chars — should contain <button, not just <span
		const region = src.slice(projectIdIdx, projectIdIdx + 400);

		// A <span> wrapping the badge is the OLD (non-clickable) shape.
		// After TASK-020 this must become <button.
		expect(region).toMatch(/<button/);
	});
});

// ---------------------------------------------------------------------------
// AC-2 — projectFilter state variable exists in PromptListView
// ---------------------------------------------------------------------------

describe("AC-2 — PromptListView holds a projectFilter state variable", () => {
	/**
	 * GIVEN the PromptListView component
	 * WHEN the source is read
	 * THEN it declares a useState for projectFilter / activeProjectId /
	 *      projectFilterId so that clicking a badge can update it.
	 *
	 * Currently no such state exists — this test is RED.
	 */
	test("list-view.tsx declares a projectFilter (or equivalent) useState", () => {
		const src = readListView();

		// Match any destructure that captures a variable whose name contains
		// "projectFilter" or "activeProject" or "projectFilterId".
		expect(src).toMatch(
			/\[(?:projectFilter|activeProjectId|projectFilterId)[^\]]*\]\s*=\s*useState/,
		);
	});

	test("projectFilter state variable is used as a setter target — setProjectFilter or equivalent", () => {
		const src = readListView();
		// A setter named after the state: setProjectFilter, setActiveProject*, etc.
		expect(src).toMatch(
			/set(?:ProjectFilter|ActiveProject(?:Id)?|ProjectFilterId)\s*\(/,
		);
	});
});

// ---------------------------------------------------------------------------
// AC-3 — filtered memo applies projectFilter as a filter axis
// ---------------------------------------------------------------------------

describe("AC-3 — filtered memo filters by projectFilter when set", () => {
	/**
	 * GIVEN a non-null projectFilter value
	 * WHEN the filtered memo is computed
	 * THEN only prompts whose p.projectId matches projectFilter are included.
	 *
	 * Currently the filtered memo only filters by search text and sorts by
	 * updatedAt/name — no projectId axis. This test is RED.
	 */
	test("filtered memo in list-view.tsx includes a projectId filter branch", () => {
		const src = readListView();

		// The memo must reference both the filter state AND p.projectId
		// to perform the actual per-row filter.
		// We look for something like: if (projectFilter && p.projectId !== projectFilter)
		// or: .filter(p => !projectFilter || p.projectId === projectFilter)
		expect(src).toMatch(
			/projectFilter|activeProjectId|projectFilterId[^;]*p\.projectId|p\.projectId[^;]*projectFilter|activeProjectId|projectFilterId/,
		);
	});

	test("the projectFilter state variable is listed in the filtered useMemo dependency array", () => {
		const src = readListView();

		// useMemo deps array must include the projectFilter state.
		// We look for the closing dep array of the filtered memo which
		// currently only contains [prompts, search, sortBy].
		expect(src).toMatch(
			/\[prompts[^[\]]*(?:projectFilter|activeProjectId|projectFilterId)[^[\]]*\]/,
		);
	});
});

// ---------------------------------------------------------------------------
// AC-4 — active-filter pill renders when projectFilter is set
// ---------------------------------------------------------------------------

describe("AC-4 — active-filter pill is rendered in the header when projectFilter is active", () => {
	/**
	 * GIVEN a non-null projectFilter
	 * WHEN the list header renders
	 * THEN a pill showing the active project name appears, with a clear (✕)
	 *      affordance that sets projectFilter back to null.
	 *
	 * The spec (PROMPT-PROJECT-PICKER-SPEC.md §3 Filter indicator) says the pill
	 * appears "in the list header (alongside existing search/sort controls)".
	 * No such pill exists today — this test is RED.
	 */
	test("list-view.tsx conditionally renders a filter-active pill (projectFilter && ...)", () => {
		const src = readListView();

		// There must be a JSX conditional that renders only when the
		// projectFilter/activeProjectId state is truthy.
		expect(src).toMatch(
			/\{(?:projectFilter|activeProjectId|projectFilterId)\s*&&/,
		);
	});

	test("the filter-active pill includes a clear affordance (onClick sets filter to null)", () => {
		const src = readListView();

		// The clear button must call setProjectFilter(null) or equivalent.
		expect(src).toMatch(
			/set(?:ProjectFilter|ActiveProject(?:Id)?|ProjectFilterId)\(null\)/,
		);
	});
});
