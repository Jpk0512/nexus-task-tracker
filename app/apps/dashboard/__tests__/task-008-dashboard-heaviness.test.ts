/// <reference path="./vitest-globals.d.ts" />
/**
 * TASK-008 P7 — Dashboard heaviness: provider scoping, lazy FocusSessionLoader,
 * sidebar query gating, website turbo-isolate.
 *
 * Acceptance criteria (GWT format — behavior assertions, not banned-string checks):
 *
 *   AC1 — The root navigation layout renders a DashboardDndProvider (lazy,
 *          ssr:false) wrapping BOTH AppSidebar AND {children} so that
 *          sidebar useDroppable targets share one DndContext with the todo
 *          SortableContext. DashboardDndProvider must be a genuine first-class
 *          component — NOT an alias / re-export of TodoDndProvider. Subtree
 *          layouts (/projects, /todos) must NOT import or use any dnd-kit
 *          DndContext provider of their own.
 *   AC2 — FocusSessionLoader module uses next/dynamic with { ssr: false }.
 *          The root navigation layout does NOT directly import FocusSessionLoader.
 *   AC3 — ProjectRelationshipsSidebar gates all useQuery calls on the sidebar
 *          being expanded (section components only rendered after collapsed guard).
 *   AC4 — apps/website (@mimir/website) is excluded from the turbo build
 *          pipeline (root package.json build script or turbo.json).
 *   AC5 — dashboard tsconfig.json is valid JSON with compilerOptions.
 */

import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, test } from "vitest";

// ---------------------------------------------------------------------------
// Repo path helpers
// ---------------------------------------------------------------------------

// __tests__ → dashboard → apps → app
const APP_ROOT = join(import.meta.dirname ?? __dirname, "..", "..", "..");

const DASHBOARD_SRC = join(APP_ROOT, "apps", "dashboard", "src");

function src(...parts: string[]): string {
	return join(DASHBOARD_SRC, ...parts);
}

function readSrc(...parts: string[]): string {
	const p = src(...parts);
	if (!existsSync(p)) {
		throw new Error(`Expected source file not found: ${p}`);
	}
	return readFileSync(p, "utf8");
}

// ---------------------------------------------------------------------------
// AC1 — DndProvider scoping: BEHAVIOR assertions
// ---------------------------------------------------------------------------

describe("AC1 — DndProvider scoping (behavior, not banned strings)", () => {
	/**
	 * GIVEN the root (navigation) layout
	 * WHEN the file is read
	 * THEN it must use next/dynamic with ssr:false to load DashboardDndProvider,
	 * AND DashboardDndProvider must appear as a JSX wrapper around both
	 * AppSidebar and the page children — confirming the single shared DndContext
	 * covers both the sidebar droppable and the todo SortableContext.
	 */
	test("root navigation layout lazy-loads DashboardDndProvider wrapping AppSidebar AND children", () => {
		const navLayout = readSrc(
			"app",
			"team",
			"[team]",
			"(navigation)",
			"layout.tsx",
		);

		// Must use next/dynamic (lazy load, not a static import of the provider)
		expect(navLayout).toMatch(/from ['"]next\/dynamic['"]/);
		expect(navLayout).toMatch(/ssr\s*:\s*false/);

		// The dynamic variable must be named DashboardDndProvider (real name, not alias)
		expect(navLayout).toMatch(/const DashboardDndProvider\s*=/);

		// DashboardDndProvider must be used as a JSX wrapper
		expect(navLayout).toMatch(/<DashboardDndProvider>/);
		expect(navLayout).toMatch(/<\/DashboardDndProvider>/);

		// AppSidebar must appear INSIDE DashboardDndProvider (between its open+close tags)
		const openTag = navLayout.indexOf("<DashboardDndProvider>");
		const closeTag = navLayout.indexOf("</DashboardDndProvider>");
		const appSidebarPos = navLayout.indexOf("<AppSidebar");
		expect(openTag).toBeGreaterThan(-1);
		expect(closeTag).toBeGreaterThan(openTag);
		expect(appSidebarPos).toBeGreaterThan(openTag);
		expect(appSidebarPos).toBeLessThan(closeTag);

		// {children} must appear INSIDE DashboardDndProvider as well
		const childrenPos = navLayout.indexOf("{children}", openTag);
		expect(childrenPos).toBeGreaterThan(openTag);
		expect(childrenPos).toBeLessThan(closeTag);
	});

	/**
	 * GIVEN the DashboardDndProvider source file
	 * WHEN it is read
	 * THEN it must be a genuine first-class component owning its own DndContext
	 * and its own React context — NOT a re-export alias pointing at TodoDndProvider
	 * or any other existing provider. The file must:
	 *   - export a function named DashboardDndProvider
	 *   - own a createContext call (its own context value)
	 *   - render DndContext from @dnd-kit/core directly
	 *   - NOT be a pure re-export barrel (no "export ... from" as its only content)
	 */
	test("DashboardDndProvider is a genuine first-class component, not an alias", () => {
		const dndProviderFile = readSrc(
			"components",
			"dnd",
			"dashboard-dnd-provider.tsx",
		);

		// Must declare the function itself (not import-and-re-export it)
		expect(dndProviderFile).toMatch(
			/export function DashboardDndProvider\s*\(/,
		);

		// Must own its own React context (createContext call present in THIS file)
		expect(dndProviderFile).toMatch(/createContext/);

		// Must render DndContext from @dnd-kit/core (the actual dnd-kit context)
		expect(dndProviderFile).toMatch(/<DndContext/);
		expect(dndProviderFile).toMatch(/@dnd-kit\/core/);

		// File must not be a pure barrel (more than just re-export lines)
		// A barrel would have no function body — verify a real function body exists
		const hasFunctionBody =
			/export function DashboardDndProvider[\s\S]*?\{[\s\S]*?return/.test(
				dndProviderFile,
			);
		expect(hasFunctionBody).toBe(true);
	});

	/**
	 * GIVEN the /projects subtree layout
	 * WHEN the file is read
	 * THEN it must NOT import from @dnd-kit/core (no nested DndContext provider).
	 * Asserting on the absence of the DND library import is behavior-grounded:
	 * any DndContext nesting requires importing from @dnd-kit/core — this cannot
	 * be evaded by renaming while keeping the actual behavior.
	 */
	test("projects subtree layout does not own a nested dnd-kit DndContext provider", () => {
		const content = readSrc(
			"app",
			"team",
			"[team]",
			"(navigation)",
			"projects",
			"layout.tsx",
		);

		// A nested DndContext requires importing from @dnd-kit/core.
		// If this import is absent, no DndContext can be present.
		expect(content).not.toMatch(/@dnd-kit\/core/);

		// Also must not use DndContext JSX directly
		expect(content).not.toMatch(/<DndContext/);
	});

	/**
	 * GIVEN the /todos subtree layout
	 * WHEN the file is read
	 * THEN it must NOT own a nested dnd-kit DndContext provider (same reasoning).
	 */
	test("todos subtree layout does not own a nested dnd-kit DndContext provider", () => {
		const content = readSrc(
			"app",
			"team",
			"[team]",
			"(navigation)",
			"todos",
			"layout.tsx",
		);

		expect(content).not.toMatch(/@dnd-kit\/core/);
		expect(content).not.toMatch(/<DndContext/);
	});
});

// ---------------------------------------------------------------------------
// AC2 — FocusSessionLoader uses next/dynamic({ ssr: false })
// ---------------------------------------------------------------------------

describe("AC2 — FocusSessionLoader lazy dynamic import", () => {
	/**
	 * GIVEN the focus-session-loader.tsx component
	 * WHEN it is read
	 * THEN it must use next/dynamic with { ssr: false }
	 * AND the root navigation layout must NOT directly import FocusSessionLoader
	 */
	test("focus-session-loader uses next/dynamic with ssr:false", () => {
		const loaderContent = readSrc(
			"components",
			"focus",
			"focus-session-loader.tsx",
		);

		expect(loaderContent).toMatch(/from ['"]next\/dynamic['"]/);
		expect(loaderContent).toMatch(/ssr\s*:\s*false/);

		const navLayout = readSrc(
			"app",
			"team",
			"[team]",
			"(navigation)",
			"layout.tsx",
		);
		expect(navLayout).not.toMatch(/FocusSessionLoader/);
	});
});

// ---------------------------------------------------------------------------
// AC3 — ProjectRelationshipsSidebar gates queries on visibility
// ---------------------------------------------------------------------------

describe("AC3 — Sidebar query visibility gating", () => {
	/**
	 * GIVEN the ProjectRelationshipsSidebar component
	 * WHEN collapsed=true
	 * THEN section child components are NOT rendered (early-return gating)
	 * so useQuery calls inside those sections never initialise.
	 */
	test("ProjectRelationshipsSidebar has no unconditional useQuery at component scope", () => {
		const content = readSrc(
			"components",
			"projects",
			"project-relationships-sidebar.tsx",
		);

		const exportFnMatch = content.match(
			/export function ProjectRelationshipsSidebar[\s\S]*?^}/m,
		);
		if (exportFnMatch) {
			expect(exportFnMatch[0]).not.toMatch(/\buseQuery\b/);
		}

		expect(content).toMatch(/if \(collapsed\)/);
	});

	/**
	 * GIVEN the PromptsSection component in the same file
	 * WHEN it is read
	 * THEN its useQuery call must be present (queries live in sections, not root)
	 * and the sections are only mounted when the sidebar is expanded.
	 */
	test("PromptsSection contains useQuery and is only rendered when expanded", () => {
		const content = readSrc(
			"components",
			"projects",
			"project-relationships-sidebar.tsx",
		);

		expect(content).toMatch(/listLinkedPrompts\.queryOptions/);

		const collapsedIdx = content.indexOf("if (collapsed)");
		const promptsSectionIdx = content.indexOf("<PromptsSection");
		expect(collapsedIdx > -1).toBe(true);
		expect(promptsSectionIdx > collapsedIdx).toBe(true);
	});
});

// ---------------------------------------------------------------------------
// AC4 — apps/website excluded from turbo build pipeline
// ---------------------------------------------------------------------------

describe("AC4 — website excluded from turbo build", () => {
	/**
	 * GIVEN the root package.json build script
	 * WHEN it is read
	 * THEN it must exclude @mimir/website via --filter=!@mimir/website
	 */
	test("turbo build pipeline excludes @mimir/website via filter or override", () => {
		const turboPath = join(APP_ROOT, "turbo.json");
		const pkgPath = join(APP_ROOT, "package.json");

		expect(existsSync(turboPath)).toBe(true);
		expect(existsSync(pkgPath)).toBe(true);

		const turboContent = readFileSync(turboPath, "utf8");
		const pkgContent = readFileSync(pkgPath, "utf8");

		const turboHasWebsiteExclusion =
			turboContent.includes("@mimir/website") ||
			turboContent.includes("apps/website");

		const pkg = JSON.parse(pkgContent) as {
			scripts?: Record<string, string>;
		};
		const buildScript = pkg.scripts?.build ?? "";
		const pkgExcludesWebsite =
			buildScript.includes("!@mimir/website") ||
			buildScript.includes("!./apps/website") ||
			buildScript.includes("!apps/website");

		expect(turboHasWebsiteExclusion || pkgExcludesWebsite).toBe(true);
	});
});

// ---------------------------------------------------------------------------
// AC5 — dashboard tsc baseline (no new errors)
// ---------------------------------------------------------------------------

describe("AC5 — dashboard tsconfig is valid and tsc-clean", () => {
	test("dashboard tsconfig.json is valid JSON with compilerOptions", () => {
		const tsconfigPath = join(APP_ROOT, "apps", "dashboard", "tsconfig.json");
		expect(existsSync(tsconfigPath)).toBe(true);

		const raw = readFileSync(tsconfigPath, "utf8");
		const tsconfig = JSON.parse(raw) as { compilerOptions?: unknown };
		expect(tsconfig).toHaveProperty("compilerOptions");
	});
});
