/**
 * TASK-026 — AC-4 behavioral RENDER test for LibraryDetailView readOnly.
 *
 * This test actually mounts <LibraryDetailView readOnly /> into jsdom — it
 * reads no source text and runs no source regex — and asserts on the rendered
 * DOM that the readOnly boundary excises every mutation affordance while keeping
 * Badges. The runner is configured for this in vitest.config.ts
 * (environment:"jsdom", oxc.jsx.runtime:"automatic" to override the project's
 * jsx:"preserve", setupFiles wiring jest-dom + Radix/jsdom shims).
 *
 * Boundaries mocked: @tanstack/react-query (hook data), @/utils/trpc (option
 * proxies), next/navigation (useParams), sonner (toast), and the heavy
 * BlockEditor / BacklinksPanel children — so the unit under test is the
 * detail-view's readOnly branching, nothing else.
 */
import { render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

const ENTRY = {
	id: "entry-1",
	name: "My Prompt Entry",
	kind: "prompt",
	description: "a description",
	sourceLabel: "kbuddy",
	relativePath: "prompts/my.md",
	absolutePath: "/abs/prompts/my.md",
	fileSha: "sha-1",
	lastEditedAt: "2026-06-20",
	frontmatter: { name: "My Prompt Entry", description: "a description" },
	body: "body text",
	tags: ["alpha", "beta"],
	projects: [{ projectId: "proj-1" }],
};

const ALL_PROJECTS = {
	data: [
		{ id: "proj-1", name: "Linked Project" },
		{ id: "proj-2", name: "Other Project" },
	],
};

vi.mock("next/navigation", () => ({
	useParams: () => ({ team: "team-1" }),
}));

vi.mock("sonner", () => ({
	toast: Object.assign(() => {}, { success: () => {}, error: () => {} }),
}));

vi.mock("@/components/editor/block-editor", () => ({
	BlockEditor: (): null => null,
}));

vi.mock("@/components/backlinks/backlinks-panel", () => ({
	BacklinksPanel: (): null => null,
}));

// trpc proxy: every property access returns an object exposing queryOptions /
// mutationOptions that just echo a stable key — the mocked react-query hooks
// ignore the argument entirely.
vi.mock("@/utils/trpc", () => {
	const queryOptions = (input: unknown) => ({ queryKey: ["mock"], input });
	const mutationOptions = (opts: unknown) => ({ mock: true, opts });
	const handler: ProxyHandler<Record<string, unknown>> = {
		get(_target, prop) {
			if (prop === "queryOptions") return queryOptions;
			if (prop === "mutationOptions") return mutationOptions;
			return new Proxy({}, handler);
		},
	};
	return { trpc: new Proxy({}, handler) };
});

vi.mock("@tanstack/react-query", () => ({
	useQueryClient: () => ({ invalidateQueries: () => {} }),
	useMutation: () => ({ mutate: () => {}, isPending: false }),
	useQuery: (opts: { input?: { id?: string; pageSize?: number } }) => {
		// projects.get is called with { pageSize }, getById with { id }
		if (opts?.input && "pageSize" in opts.input) {
			return { data: ALL_PROJECTS, isLoading: false };
		}
		return { data: ENTRY, isLoading: false };
	},
}));

// Imported after mocks are registered.
let LibraryDetailView: typeof import("@/components/library/detail-view").LibraryDetailView;

beforeEach(async () => {
	({ LibraryDetailView } = await import("@/components/library/detail-view"));
});

afterEach(() => {
	vi.clearAllMocks();
});

describe("AC-4 — LibraryDetailView readOnly renders no mutation affordances", () => {
	test("display Badges for tags and projects ARE present", () => {
		render(<LibraryDetailView entryId="entry-1" readOnly />);

		// Tag display text is rendered.
		expect(screen.getByText("alpha")).toBeInTheDocument();
		expect(screen.getByText("beta")).toBeInTheDocument();
		// Linked project name (resolved from ALL_PROJECTS) is rendered.
		expect(screen.getByText("Linked Project")).toBeInTheDocument();
		// Entry heading rendered (proves the component truly mounted).
		expect(
			screen.getByRole("heading", { name: "My Prompt Entry" }),
		).toBeInTheDocument();
	});

	test("NO tag-remove button is in the DOM", () => {
		render(<LibraryDetailView entryId="entry-1" readOnly />);

		// Each tag Badge in non-readOnly mode contains a <button> (the X remover).
		// In readOnly mode the Badge holds only text — assert no button inside.
		const alphaBadge = screen.getByText("alpha");
		expect(within(alphaBadge).queryByRole("button")).toBeNull();
		const betaBadge = screen.getByText("beta");
		expect(within(betaBadge).queryByRole("button")).toBeNull();
	});

	test("NO tag-add input is in the DOM", () => {
		const { container } = render(
			<LibraryDetailView entryId="entry-1" readOnly />,
		);
		expect(container.querySelector('input[placeholder="+ tag"]')).toBeNull();
	});

	test("NO project-unlink button is in the DOM", () => {
		render(<LibraryDetailView entryId="entry-1" readOnly />);
		const projectBadge = screen.getByText("Linked Project");
		expect(within(projectBadge).queryByRole("button")).toBeNull();
	});

	test("NO project-link Select is in the DOM", () => {
		render(<LibraryDetailView entryId="entry-1" readOnly />);
		// The link Select renders a combobox trigger with this placeholder text.
		expect(screen.queryByText("+ link project")).toBeNull();
		expect(screen.queryByRole("combobox")).toBeNull();
	});

	test("control: non-readOnly DOES render the mutation affordances (test discriminates)", () => {
		const { container } = render(<LibraryDetailView entryId="entry-1" />);

		// tag-remove buttons present (one per tag).
		const alphaBadge = screen.getByText("alpha");
		expect(within(alphaBadge).queryByRole("button")).not.toBeNull();
		// tag-add input present.
		expect(
			container.querySelector('input[placeholder="+ tag"]'),
		).not.toBeNull();
		// project-unlink button present.
		const projectBadge = screen.getByText("Linked Project");
		expect(within(projectBadge).queryByRole("button")).not.toBeNull();
		// project-link Select present (proj-2 is unlinked → available).
		expect(screen.getByText("+ link project")).toBeInTheDocument();
	});
});
