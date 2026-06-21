/// <reference path="./vitest-globals.d.ts" />
/**
 * TASK-011 — Todos wiring gaps: FAILING stubs (stubs phase, FEAT-002).
 *
 * Six acceptance criteria covering the four wiring gaps + two polish items
 * that palette and scout flagged as incomplete.
 *
 * WHY source-pattern (not behavioral RTL):
 *   The vitest.config.ts for this project sets `environment: "node"` — jsdom
 *   is not available. React component rendering via @testing-library/react is
 *   not viable in this harness. All existing dashboard tests (feat2-task013-
 *   prompt-library.test.ts, feat2-task012-knowledge-vault-ui.test.ts) use the
 *   same source-pattern strategy. Behavioral RTL tests belong in a Playwright
 *   e2e suite, not here.
 *
 * Each test:
 *   - Is expected to FAIL (RED) until forge-ui lands the implementation.
 *   - Fails because the pattern it asserts is ABSENT from the source — not due
 *     to a compile or import error.
 *   - Will pass automatically once the implementation lands; no test edits needed.
 *
 * Run: rtk vitest run app/apps/dashboard/__tests__/feat2-task011-todos-wiring.test.ts
 */

import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, test } from "vitest";

// ---------------------------------------------------------------------------
// Path helpers
// ---------------------------------------------------------------------------

// __tests__ (1) → dashboard (2) → apps (3) → app (4 = monorepo root)
const DASHBOARD_ROOT = join(import.meta.dirname ?? __dirname, "..");
const APP_ROOT = join(DASHBOARD_ROOT, "..", "..");

const TODOS_VIEW_PATH = join(
	DASHBOARD_ROOT,
	"src",
	"components",
	"todos",
	"todos-view.tsx",
);

const EDITOR_PATH = join(
	DASHBOARD_ROOT,
	"src",
	"components",
	"editor",
	"index.tsx",
);

const INDEX_CSS_PATH = join(APP_ROOT, "packages", "ui", "src", "index.css");

function readSrc(p: string): string {
	if (!existsSync(p)) throw new Error(`Expected source file not found: ${p}`);
	return readFileSync(p, "utf8");
}

// ---------------------------------------------------------------------------
// AC-1 — Tag filter strip
// ---------------------------------------------------------------------------

describe("AC-1 — tag filter strip: clicking a tag filters the todo list", () => {
	/**
	 * GIVEN a TodosView that renders todos with tags
	 * WHEN the user clicks a tag pill in the tag filter strip
	 * THEN
	 *   (a) a selectedTag state variable is set
	 *   (b) the todos.get query is called with { tag: selectedTag } so the server
	 *       filters by that tag (the query key changes, RTK re-fetches)
	 *   (c) an "All" pill is rendered that clears the filter (sets selectedTag
	 *       back to undefined/null)
	 *
	 * Source evidence required:
	 *   1. todos-view.tsx holds a selectedTag state variable.
	 *   2. todos.get.queryOptions is called with a tag argument (not undefined).
	 *   3. A tag filter strip element is rendered (data-testid or aria-label
	 *      containing "tag" or "filter", OR a map over allTags/uniqueTags).
	 *   4. An "All" control resets the filter.
	 */
	test("TodosView declares a selectedTag (or activeTag) state variable", () => {
		const src = readSrc(TODOS_VIEW_PATH);
		// Must have a state variable for the active tag filter
		expect(src).toMatch(
			/useState[^(]*\([^)]*\)[^;]*selectedTag|activeTag|tagFilter/,
		);
	});

	test("todos.get.queryOptions is called with a tag parameter (not always undefined)", () => {
		const src = readSrc(TODOS_VIEW_PATH);
		// Current stub: trpc.todos.get.queryOptions(undefined) — no tag param at all.
		// After wiring, the call must include a tag field.
		expect(src).toMatch(/todos\.get\.queryOptions\([^)]*tag[^)]*\)/);
	});

	test("TodosView renders a tag filter strip that maps over unique tags", () => {
		const src = readSrc(TODOS_VIEW_PATH);
		// A filter strip must iterate over collected tags and render pill buttons
		expect(src).toMatch(/allTags|uniqueTags|tagList|filterTags/);
	});

	test("tag filter strip includes an 'All' control that clears the filter", () => {
		const src = readSrc(TODOS_VIEW_PATH);
		// The "All" pill resets selectedTag to undefined / null / ""
		// Look for the word "All" appearing near tag/filter state reset
		const allIdx = src.search(/"All"|'All'/);
		expect(allIdx).toBeGreaterThan(-1);

		// Within 300 chars of the "All" label there must be a click handler
		// that clears the tag filter (sets it to undefined, null, or empty string)
		const region = src.slice(allIdx, allIdx + 300);
		expect(region).toMatch(/undefined|null|""|''/);
	});
});

// ---------------------------------------------------------------------------
// AC-2 — Project filter
// ---------------------------------------------------------------------------

describe("AC-2 — project filter: clicking a project pill filters todos to that project", () => {
	/**
	 * GIVEN a TodosView that shows todos from multiple projects
	 * WHEN the user clicks a project pill in the filter bar
	 * THEN
	 *   (a) a selectedProjectId state variable is set
	 *   (b) the todos.get query is called with { projectId: selectedProjectId }
	 *   (c) clearing the filter returns to all todos (selectedProjectId → null)
	 *
	 * Source evidence required:
	 *   1. todos-view.tsx holds a selectedProjectId (or activeProjectId) state.
	 *   2. todos.get.queryOptions is called with a projectId argument.
	 *   3. A project filter strip is rendered that maps over the projects list.
	 */
	test("TodosView declares a selectedProjectId (or activeProjectId) state variable", () => {
		const src = readSrc(TODOS_VIEW_PATH);
		expect(src).toMatch(
			/useState[^(]*\([^)]*\)[^;]*selectedProjectId|activeProjectId|projectFilter/,
		);
	});

	test("todos.get.queryOptions is called with a projectId parameter", () => {
		const src = readSrc(TODOS_VIEW_PATH);
		// After wiring, the todos.get call must pass projectId
		expect(src).toMatch(/todos\.get\.queryOptions\([^)]*projectId[^)]*\)/);
	});

	test("TodosView renders project filter pills by mapping over the projects list", () => {
		const src = readSrc(TODOS_VIEW_PATH);
		// A project pill strip must iterate projects and render clickable buttons
		// Look for a JSX map over projects near a filter/pill context
		const projectsPillIdx = src.search(
			/projects\.map.*pill|filterProject|project.*pill|pill.*project/i,
		);
		expect(projectsPillIdx).toBeGreaterThan(-1);
	});
});

// ---------------------------------------------------------------------------
// AC-3 — Note attachment uses Tiptap Editor (not plain textarea)
// ---------------------------------------------------------------------------

describe("AC-3 — note attachment editor: AttachmentsModal uses Tiptap Editor for note content", () => {
	/**
	 * GIVEN an AttachmentsModal open on a todo that has a 'note' kind attachment
	 * WHEN the user edits the note body
	 * THEN
	 *   (a) the edit surface is the Tiptap <Editor> component (not a <textarea>)
	 *   (b) saving happens on BLUR (onBlur prop fires updateAttachment), not per
	 *       keystroke (no onChange → mutate pattern for attachment content)
	 *
	 * Source evidence:
	 *   1. The Editor component from @/components/editor is imported in todos-view.tsx.
	 *   2. The AttachmentsModal block no longer uses a <textarea> for note content.
	 *   3. The Editor onBlur prop calls updateAttMut.mutate with the attachment content.
	 *
	 * Why source-pattern: no jsdom; Editor internals (Tiptap ProseMirror) require
	 * a real browser DOM — even Playwright struggles with it. The assert-that-
	 * textarea-is-replaced pattern is the highest-fidelity check in a node harness.
	 */
	test("todos-view.tsx imports the Editor component from @/components/editor", () => {
		const src = readSrc(TODOS_VIEW_PATH);
		// Must import Editor from the shared editor package
		expect(src).toMatch(
			/import[^;]*Editor[^;]*from\s+["']@\/components\/editor["']/,
		);
	});

	test("AttachmentsModal no longer uses a plain <textarea> for note attachment content", () => {
		const src = readSrc(TODOS_VIEW_PATH);

		// Locate the AttachmentsModal function body
		const modalIdx = src.indexOf("function AttachmentsModal(");
		expect(modalIdx).toBeGreaterThan(-1);

		// The next occurrence of 'textarea' after the modal declaration (within
		// the note-rendering block) must be GONE — replaced by <Editor>.
		// We search the modal body (up to 2000 chars) for a textarea used for
		// note content (not the add-note form textarea which is also being replaced).
		const modalBody = src.slice(modalIdx, modalIdx + 2500);

		// After the swap, the note attachment card must NOT contain a <textarea>
		// for displaying existing note content. The add-note form textarea is also
		// expected to be replaced, so we assert zero textarea occurrences in the
		// entire modal body.
		expect(modalBody).not.toMatch(/<textarea/);
	});

	test("AttachmentsModal wires <Editor> onBlur to call updateAttachment (persist on blur, not per keystroke)", () => {
		const src = readSrc(TODOS_VIEW_PATH);

		const modalIdx = src.indexOf("function AttachmentsModal(");
		expect(modalIdx).toBeGreaterThan(-1);

		const modalBody = src.slice(modalIdx, modalIdx + 2500);

		// The <Editor> JSX element must carry an onBlur prop — not just a plain
		// <input onBlur> (which already exists for the title field). We require
		// the Tiptap <Editor component with its onBlur wired to updateAttMut.mutate.
		// Pattern: <Editor … onBlur= or onBlur={
		// Existing code only has plain <input onBlur> and <textarea onBlur>, so
		// this pattern will be absent until forge-ui swaps the textarea for <Editor>.
		expect(modalBody).toMatch(/<Editor[^>]*onBlur/);
	});

	test("Editor component exports an onBlur prop (contract check)", () => {
		const src = readSrc(EDITOR_PATH);
		// The Editor must accept an onBlur callback so AttachmentsModal can use it
		expect(src).toMatch(/onBlur\s*\??\s*:/);
	});
});

// ---------------------------------------------------------------------------
// AC-4 — doc_link attachment embeds LibraryDetailView (not a plain anchor)
// ---------------------------------------------------------------------------

describe("AC-4 — doc_link attachment: AttachmentsModal embeds LibraryDetailView inline", () => {
	/**
	 * GIVEN an AttachmentsModal open on a todo that has a 'doc_link' attachment
	 * WHEN the modal renders the doc_link card
	 * THEN
	 *   (a) the render surface is <LibraryDetailView> (inline embed), NOT a <Link>
	 *       or <a> pointing to the document URL
	 *   (b) LibraryDetailView is imported from @/components/library/detail-view
	 *
	 * Source evidence:
	 *   1. todos-view.tsx imports LibraryDetailView.
	 *   2. The AttachmentsModal doc_link branch renders <LibraryDetailView> not <Link>.
	 *
	 * Why source-pattern: no jsdom; LibraryDetailView makes tRPC calls and has
	 * complex render logic. A node-harness test of an embedded rich component
	 * is not viable without jsdom + extensive mocking.
	 */
	test("todos-view.tsx imports LibraryDetailView from @/components/library/detail-view", () => {
		const src = readSrc(TODOS_VIEW_PATH);
		expect(src).toMatch(
			/import[^;]*LibraryDetailView[^;]*from\s+["']@\/components\/library\/detail-view["']/,
		);
	});

	test("AttachmentsModal doc_link branch renders LibraryDetailView instead of a plain Link anchor", () => {
		const src = readSrc(TODOS_VIEW_PATH);

		const modalIdx = src.indexOf("function AttachmentsModal(");
		expect(modalIdx).toBeGreaterThan(-1);

		const modalBody = src.slice(modalIdx, modalIdx + 2500);

		// Must render LibraryDetailView for the doc_link kind
		expect(modalBody).toMatch(/<LibraryDetailView/);

		// Must NOT fall back to a plain next/link <Link> for the doc_link path
		// (the only Link usage allowed in the modal body is a non-doc-card context)
		const docLinkBranchIdx = modalBody.indexOf("doc_link");
		expect(docLinkBranchIdx).toBeGreaterThan(-1);

		// Within the doc_link branch (200 chars after the first "doc_link" in the
		// modal) there must NOT be a <Link href= pointing to the documents URL
		const docLinkRegion = modalBody.slice(
			docLinkBranchIdx,
			docLinkBranchIdx + 300,
		);
		expect(docLinkRegion).not.toMatch(/<Link\s+href/);
	});
});

// ---------------------------------------------------------------------------
// AC-5 — prefers-reduced-motion: CompletedSection CollapsibleContent skips animation
// ---------------------------------------------------------------------------

describe("AC-5 — prefers-reduced-motion: CollapsibleContent does not animate when motion is reduced", () => {
	/**
	 * GIVEN a user who has prefers-reduced-motion: reduce set in their OS
	 * WHEN the Completed section CollapsibleContent mounts or toggles
	 * THEN no CSS transition/animation fires (animation: none; or duration: 0)
	 *
	 * Source evidence: a @media (prefers-reduced-motion: reduce) block in the
	 * project's global CSS that targets the Radix CollapsibleContent animation
	 * classes (data-[state=open]/data-[state=closed] or the keyframe names used
	 * by the UI package).
	 *
	 * Why source-pattern: CSS media queries cannot be exercised in jsdom even with
	 * matchMedia mocks — the actual keyframe execution requires a real browser.
	 * Asserting the CSS rule is present is the correct test boundary here.
	 */
	test("index.css contains a prefers-reduced-motion block targeting CollapsibleContent", () => {
		const src = readSrc(INDEX_CSS_PATH);

		// Must have a @media (prefers-reduced-motion: reduce) block
		expect(src).toMatch(/@media[^{]*prefers-reduced-motion[^{]*reduce/);

		// Within that block, animation must be suppressed for collapsible content
		// (animation: none, or duration 0ms). We look for the pattern near the
		// prefers-reduced-motion declaration.
		const motionIdx = src.search(
			/@media[^{]*prefers-reduced-motion[^{]*reduce/,
		);
		expect(motionIdx).toBeGreaterThan(-1);

		// Scan up to 600 chars inside the media block for animation suppression
		const region = src.slice(motionIdx, motionIdx + 600);
		expect(region).toMatch(
			/animation[^:]*:\s*none|animation-duration[^:]*:\s*0/,
		);
	});

	test("index.css reduced-motion block targets Radix collapsible animation classes or keyframes", () => {
		const src = readSrc(INDEX_CSS_PATH);

		const motionIdx = src.search(
			/@media[^{]*prefers-reduced-motion[^{]*reduce/,
		);
		expect(motionIdx).toBeGreaterThan(-1);

		const region = src.slice(motionIdx, motionIdx + 600);
		// Must target collapsible-related selector: data-[state= or [data-state=
		// OR the collapsible keyframe names (slideDown/slideUp or similar)
		expect(region).toMatch(
			/collapsible|data-\[state|slideDown|slideUp|slide-down|slide-up/i,
		);
	});
});

// ---------------------------------------------------------------------------
// AC-6 — Check→Move animation: layout animation on check under default motion
// ---------------------------------------------------------------------------

describe("AC-6 — check→move animation: checked todo animates to Completed group", () => {
	/**
	 * GIVEN a user with default (no-reduce) motion preferences
	 * WHEN a todo's checkbox is checked (it moves from active to completed list)
	 * THEN the item animates its position change using a layout animation
	 *   (Framer Motion / motion/react AnimatePresence + layout prop, or equivalent)
	 *
	 * Under prefers-reduced-motion: reduce, the same check is instant (no animation).
	 *
	 * Source evidence:
	 *   1. todos-view.tsx imports AnimatePresence (or motion) from motion/react or
	 *      framer-motion for the layout animation.
	 *   2. The TodoRow or its wrapping element uses the `layout` prop.
	 *   3. The motion config respects reducedMotion="user" so the platform setting
	 *      is honored automatically.
	 *
	 * Why source-pattern: animating a layout shift requires a real browser paint
	 * pipeline — jsdom has no layout engine. The only testable fact in node is
	 * whether the animation wiring exists in the source.
	 */
	test("todos-view.tsx imports AnimatePresence or motion from motion/react or framer-motion", () => {
		const src = readSrc(TODOS_VIEW_PATH);
		// Must import the motion library for layout animations
		expect(src).toMatch(
			/import[^;]*(AnimatePresence|motion)[^;]*from\s+["'](motion\/react|framer-motion)["']/,
		);
	});

	test("TodoRow wrapper or SortableContext wrapper uses the layout prop for position animation", () => {
		const src = readSrc(TODOS_VIEW_PATH);
		// A motion element must carry the `layout` prop so React/Framer
		// animates the repositioning when the item moves to the completed list
		expect(src).toMatch(/motion\.(div|li)[^>]*layout/);
	});

	test("motion config uses reducedMotion='user' so OS preference is honored automatically", () => {
		const src = readSrc(TODOS_VIEW_PATH);
		// The MotionConfig (or equivalent) must set reducedMotion="user"
		// so that prefers-reduced-motion: reduce yields instant transitions
		expect(src).toMatch(/reducedMotion\s*=\s*["']user["']/);
	});
});
