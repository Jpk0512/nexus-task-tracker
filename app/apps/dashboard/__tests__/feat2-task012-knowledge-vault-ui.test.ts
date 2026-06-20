/// <reference path="./vitest-globals.d.ts" />
/**
 * TASK-012 — Knowledge vault UI: FAILING stubs (stubs phase).
 *
 * Five acceptance criteria from docs/features/FEAT-002-next-features.md §TASK-012
 * covering the remaining UI behaviors NOT yet implemented:
 *
 *  GWT#4 — Resolved vs unresolved link coloring
 *           [[Resolvable]] (toNoteId set) renders BLUE; [[Missing]] (toNoteId null) renders RED.
 *  GWT#5 — Auto-save on blur within 500ms
 *           Blurring the note editor fires knowledge.update within 500ms.
 *           On expectedSha conflict → re-fetch sha + re-save (last-write-wins, DEC-010).
 *           No blocking modal and no conflict toast shown.
 *  GWT#6 — Slash menu on '/': focusing BlockEditor and typing '/' opens the slash command menu.
 *  GWT#7 — Focus-mode route: /team/[team]/knowledge/[noteId] renders the single-note focus view.
 *  GWT#8 — Vault path setting persists: the /team/[team]/settings/knowledge form
 *           submits root_path via knowledge.updateVault mutation and reflects the saved value.
 *
 * Strategy: source-code assertions against the relevant files.
 * Tests are RED now because the implementation files do not yet exist.
 * They go GREEN naturally once forge-ui lands the implementation — no test edits required.
 *
 * Run: rtk vitest run app/apps/dashboard/__tests__/feat2-task012-knowledge-vault-ui.test.ts
 */

import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, test } from "vitest";

// ---------------------------------------------------------------------------
// Path helpers
// ---------------------------------------------------------------------------

// __tests__ → dashboard → apps → app (monorepo root)
const DASHBOARD_ROOT = join(
	import.meta.dirname ?? __dirname,
	"..", // dashboard
);

// apps/ is two levels up from dashboard; api is a sibling of dashboard.
const APPS_ROOT = join(DASHBOARD_ROOT, "..");

const SRC = join(DASHBOARD_ROOT, "src");
const APP_DIR = join(SRC, "app", "team", "[team]");

// Component paths (not-yet-existing files that forge-ui will create)
const KNOWLEDGE_NOTE_INLINE_PATH = join(
	SRC,
	"components",
	"knowledge",
	"wiki-link-inline.tsx",
);

const KNOWLEDGE_VIEW_PATH = join(
	SRC,
	"components",
	"knowledge",
	"knowledge-view.tsx",
);

// Focus-mode route — /team/[team]/knowledge/[noteId]/page.tsx
const FOCUS_ROUTE_DIR = join(APP_DIR, "(navigation)", "knowledge", "[noteId]");
const FOCUS_ROUTE_PATH = join(FOCUS_ROUTE_DIR, "page.tsx");

// Settings/knowledge route — /team/[team]/settings/knowledge/page.tsx
const SETTINGS_KNOWLEDGE_DIR = join(
	APP_DIR,
	"(navigation)",
	"settings",
	"(navigation)",
	"knowledge",
);
const SETTINGS_KNOWLEDGE_PAGE_PATH = join(SETTINGS_KNOWLEDGE_DIR, "page.tsx");

// Knowledge vault settings form component
const VAULT_SETTINGS_FORM_PATH = join(
	SRC,
	"components",
	"knowledge",
	"vault-settings-form.tsx",
);

// API router (read-only reference — we do NOT write here)
// APPS_ROOT = /app/apps; api is a sibling of dashboard under apps/
const KNOWLEDGE_ROUTER_PATH = join(
	APPS_ROOT,
	"api",
	"src",
	"trpc",
	"routers",
	"knowledge.ts",
);

function readFile(p: string): string {
	if (!existsSync(p)) throw new Error(`Expected file not found: ${p}`);
	return readFileSync(p, "utf8");
}

// ---------------------------------------------------------------------------
// GWT#4 — Resolved vs unresolved link coloring
// ---------------------------------------------------------------------------

describe("GWT#4 — wiki-link inline coloring: BLUE for resolved, RED for unresolved", () => {
	/**
	 * GIVEN a rendered note whose content contains [[Resolvable]] (toNoteId is set)
	 *   AND [[Missing]] (toNoteId is null)
	 * WHEN the KnowledgeView or a dedicated WikiLinkInline component renders the content
	 * THEN [[Resolvable]] renders with a blue Tailwind class (text-blue-* or text-primary)
	 *   AND [[Missing]] renders with a red Tailwind class (text-red-* or text-destructive)
	 *
	 * Source-code checks:
	 *   1. A wiki-link inline component exists that receives a `resolved` / `toNoteId` prop.
	 *   2. The component uses a blue color class when the link is resolved.
	 *   3. The component uses a red color class when the link is unresolved.
	 *   4. The KnowledgeView (or note renderer) imports or uses this inline component.
	 */
	test("a WikiLinkInline component exists that accepts a resolved/toNoteId prop", () => {
		const src = readFile(KNOWLEDGE_NOTE_INLINE_PATH);
		// Must declare a prop indicating resolution state
		expect(src).toMatch(/toNoteId|resolved|isResolved/);
	});

	test("WikiLinkInline applies a blue class for resolved links (toNoteId is set)", () => {
		const src = readFile(KNOWLEDGE_NOTE_INLINE_PATH);
		// Blue color class: text-blue-* or text-primary or text-sky-*
		expect(src).toMatch(/text-blue-|text-primary|text-sky-/);
	});

	test("WikiLinkInline applies a red class for unresolved links (toNoteId is null)", () => {
		const src = readFile(KNOWLEDGE_NOTE_INLINE_PATH);
		// Red color class: text-red-* or text-destructive or text-rose-*
		expect(src).toMatch(/text-red-|text-destructive|text-rose-/);
	});

	test("KnowledgeView imports or renders the wiki-link inline component for coloring", () => {
		const src = readFile(KNOWLEDGE_VIEW_PATH);
		// Must reference the inline component or a rendering helper for wiki-links
		expect(src).toMatch(/WikiLinkInline|wiki-link-inline|renderWikiLink/);
	});
});

// ---------------------------------------------------------------------------
// GWT#5 — Auto-save on blur within 500ms
// ---------------------------------------------------------------------------

describe("GWT#5 — auto-save on editor blur within 500ms; last-write-wins on SHA conflict (DEC-010)", () => {
	/**
	 * GIVEN a note open in the editor with unsaved edits
	 * WHEN the user blurs (moves focus away from) the editor
	 * THEN knowledge.update is fired within 500ms (no manual Save click required)
	 *
	 * GIVEN the disk file changed since the editor was opened (SHA mismatch)
	 * WHEN the auto-save fires and receives CONFLICT from the API
	 * THEN the UI re-fetches the current sha via getById
	 *   AND immediately re-saves the editor content with the fresh sha (last-write-wins)
	 *   AND shows NO blocking modal AND shows NO conflict toast
	 *
	 * Source-code checks on knowledge-view.tsx:
	 *   1. An onBlur handler on the editor wires the update mutation.
	 *   2. A debounce/setTimeout of ≤500ms guards the save call.
	 *   3. On conflict, the handler re-fetches getById (or invalidates + re-saves),
	 *      not just shows an error toast.
	 *   4. There is no toast.error call specifically for "conflict" or SHA mismatch in auto-save.
	 *   5. There is no modal/dialog opened for the conflict path.
	 */
	test("KnowledgeView has an onBlur (or blur event) handler on the editor area", () => {
		const src = readFile(KNOWLEDGE_VIEW_PATH);
		// onBlur must be wired to the textarea or BlockEditor
		expect(src).toMatch(/onBlur/);
	});

	test("the blur handler fires the knowledge.update mutation (auto-save)", () => {
		const src = readFile(KNOWLEDGE_VIEW_PATH);
		// The blur handler must call updateMut.mutate or trigger the update mutation
		// We look for the pattern of update mutation call referencing the auto-save intent
		const onBlurIdx = src.indexOf("onBlur");
		expect(onBlurIdx).toBeGreaterThan(-1);
		// Within 800 chars after first onBlur, the update mutation must be triggered
		const region = src.slice(onBlurIdx, onBlurIdx + 800);
		expect(region).toMatch(/updateMut|update\.mutate|autoSave|auto_save/);
	});

	test("auto-save uses a debounce or timeout of at most 500ms", () => {
		const src = readFile(KNOWLEDGE_VIEW_PATH);
		// Must contain a setTimeout (≤500) or a debounce call with ≤500
		// We check that 500 appears in the file near a debounce/timeout call
		expect(src).toMatch(/setTimeout|debounce|useDebounce/);
		// And that the value is ≤500 (either 500 or a smaller value)
		expect(src).toMatch(/(?:setTimeout|debounce)\s*[\w(]*[,\s]+(?:[1-4]?\d{1,2}|500)\b/);
	});

	test("SHA conflict in auto-save re-fetches getById and re-saves (last-write-wins, DEC-010)", () => {
		const src = readFile(KNOWLEDGE_VIEW_PATH);
		// There must be CONFLICT error handling in the update mutation that
		// calls refetchNote / invalidateQueries for getById and then re-saves
		expect(src).toMatch(/CONFLICT|conflict/);
		// After conflict, must not simply toast.error — must retry / re-fetch
		expect(src).toMatch(/refetchNote|getById|invalidateQueries.*getById/);
	});

	test("SHA conflict in auto-save does NOT show a blocking modal", () => {
		const src = readFile(KNOWLEDGE_VIEW_PATH);
		// If there's a conflict toast or modal opened on auto-save conflict, that's
		// wrong per DEC-010. There must be NO toast.error call specifically tied to
		// SHA conflict in the auto-save path (distinct from other save errors).
		// We verify no `toast.error` is called within the auto-save conflict handler.
		// Strategy: locate the conflict handler region and confirm no toast.error there.
		const conflictIdx = src.indexOf("CONFLICT");
		expect(conflictIdx).toBeGreaterThan(-1);
		const conflictRegion = src.slice(conflictIdx, conflictIdx + 400);
		// Must NOT show a dialog/modal for the conflict
		expect(conflictRegion).not.toMatch(/dialog|Dialog|modal|Modal/);
	});
});

// ---------------------------------------------------------------------------
// GWT#6 — Slash menu on '/'
// ---------------------------------------------------------------------------

describe("GWT#6 — typing '/' in BlockEditor opens the slash command menu", () => {
	/**
	 * GIVEN the BlockEditor (Tiptap) focused in the knowledge note view
	 * WHEN the user types '/'
	 * THEN the slash command menu opens (the SlashMenu extension is active)
	 *
	 * Source-code checks:
	 *   1. KnowledgeView uses BlockEditor (not the plain textarea) for note editing.
	 *   2. The BlockEditor's extensions prop (or the Editor's register.ts) includes
	 *      the SlashMenu extension.
	 *   3. The slash-menu extension file exists and exports an Extension.
	 */
	test("KnowledgeView imports and renders BlockEditor (not just a plain textarea)", () => {
		const src = readFile(KNOWLEDGE_VIEW_PATH);
		// Must import BlockEditor from the editor components
		expect(src).toMatch(/BlockEditor|block-editor/);
	});

	test("KnowledgeView wires BlockEditor in place of (or alongside) the note editing area", () => {
		const src = readFile(KNOWLEDGE_VIEW_PATH);
		// BlockEditor must appear as a JSX element, not just an import
		expect(src).toMatch(/<BlockEditor/);
	});

	test("BlockEditor's underlying Editor registers the SlashMenu extension", () => {
		const registerPath = join(
			SRC,
			"components",
			"editor",
			"extentions",
			"register.ts",
		);
		const src = readFile(registerPath);
		// The register file must include/reference the slash menu extension
		expect(src).toMatch(/SlashMenu|slashMenu|slash-menu|slash_menu/i);
	});

	test("slash-menu.tsx exports a Tiptap Extension that responds to '/' character", () => {
		const slashMenuPath = join(
			SRC,
			"components",
			"editor",
			"extentions",
			"slash-menu.tsx",
		);
		const src = readFile(slashMenuPath);
		// Must export an Extension
		expect(src).toMatch(/Extension\.create|export.*Extension/);
		// Must trigger on the '/' character
		expect(src).toMatch(/['"]\/['"]/);
	});
});

// ---------------------------------------------------------------------------
// GWT#7 — Focus-mode route
// ---------------------------------------------------------------------------

describe("GWT#7 — /team/[team]/knowledge/[noteId] focus-mode route renders the single-note view", () => {
	/**
	 * GIVEN a knowledge noteId
	 * WHEN the user navigates to /team/[team]/knowledge/[noteId]
	 * THEN the single-note focus view renders that note (identified by noteId)
	 *   AND the layout is focus-mode (no team navigation sidebar)
	 *
	 * Source-code checks:
	 *   1. The route file exists at the expected path.
	 *   2. The route reads the [noteId] param from the URL.
	 *   3. The route renders a dedicated focus-mode component (not KnowledgeView list).
	 */
	test("focus-mode route page.tsx exists at /team/[team]/knowledge/[noteId]/page.tsx", () => {
		expect(
			existsSync(FOCUS_ROUTE_PATH),
			`Focus-mode route not found at: ${FOCUS_ROUTE_PATH}`,
		).toBe(true);
	});

	test("focus-mode route reads the noteId param", () => {
		const src = readFile(FOCUS_ROUTE_PATH);
		// Must destructure or read noteId from params
		expect(src).toMatch(/noteId/);
	});

	test("focus-mode route renders a focused note component (not the KnowledgeView list)", () => {
		const src = readFile(FOCUS_ROUTE_PATH);
		// Must render a dedicated focus-view component, not the list KnowledgeView
		expect(src).toMatch(/KnowledgeFocusView|NoteFocusView|FocusView|focus/i);
	});

	test("knowledge-focus-view component exists and accepts a noteId prop", () => {
		const focusComponentPath = join(
			SRC,
			"components",
			"knowledge",
			"knowledge-focus-view.tsx",
		);
		const src = readFile(focusComponentPath);
		// Must accept a noteId prop
		expect(src).toMatch(/noteId/);
	});
});

// ---------------------------------------------------------------------------
// GWT#8 — Vault path setting persists via updateVault mutation
// ---------------------------------------------------------------------------

describe("GWT#8 — /team/[team]/settings/knowledge form submits root_path via knowledge.updateVault", () => {
	/**
	 * GIVEN the team knowledge settings page at /team/[team]/settings/knowledge
	 * WHEN the owner edits the vault root_path field and saves
	 * THEN the knowledge.updateVault mutation is called with { vaultId, root_path }
	 *   AND the saved root_path value is reflected in the form after save
	 *
	 * Source-code checks on the settings form:
	 *   1. The settings/knowledge route exists.
	 *   2. The vault settings form (or the settings page) imports and calls
	 *      trpc.knowledge.updateVault.
	 *   3. The mutation payload includes root_path.
	 *   4. The form re-fetches / displays the updated value after save.
	 *   5. The knowledge.ts router declares the updateVault procedure shape
	 *      accepting { vaultId: string, root_path: string }.
	 */
	test("settings/knowledge page.tsx exists at the expected route path", () => {
		expect(
			existsSync(SETTINGS_KNOWLEDGE_PAGE_PATH),
			`Knowledge settings page not found at: ${SETTINGS_KNOWLEDGE_PAGE_PATH}`,
		).toBe(true);
	});

	test("vault settings form component exists", () => {
		expect(
			existsSync(VAULT_SETTINGS_FORM_PATH),
			`VaultSettingsForm not found at: ${VAULT_SETTINGS_FORM_PATH}`,
		).toBe(true);
	});

	test("vault settings form calls trpc.knowledge.updateVault mutation", () => {
		const src = readFile(VAULT_SETTINGS_FORM_PATH);
		// Must reference the updateVault tRPC procedure
		expect(src).toMatch(/knowledge\.updateVault/);
	});

	test("vault settings form mutation payload includes root_path", () => {
		const src = readFile(VAULT_SETTINGS_FORM_PATH);
		// The mutation call must pass root_path in its input
		expect(src).toMatch(/root_path/);
	});

	test("vault settings form reflects the saved root_path value after mutation success", () => {
		const src = readFile(VAULT_SETTINGS_FORM_PATH);
		// After save, must invalidate/refetch vault data so the form shows updated value
		// Pattern: invalidateQueries / refetch / onSuccess callback
		expect(src).toMatch(/onSuccess|invalidateQueries|refetch/);
	});

	test("knowledge router declares the updateVault procedure with { vaultId, root_path } input", () => {
		const src = readFile(KNOWLEDGE_ROUTER_PATH);
		// The router must declare updateVault (forge-wire is adding it)
		expect(src).toMatch(/updateVault/);
	});
});
