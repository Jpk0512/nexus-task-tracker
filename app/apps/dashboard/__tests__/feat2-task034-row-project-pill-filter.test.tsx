/// <reference path="./vitest-globals.d.ts" />
/**
 * TASK-034 — Todos view: clicking the project pill on a todo row must filter
 * the list to that project.
 *
 * Acceptance criterion (GWT):
 *   GIVEN TodosView is mounted with todos belonging to two different projects
 *   WHEN the user clicks the project pill on a todo row (the <Badge> showing
 *        the project name next to the todo content)
 *   THEN
 *     (a) the project pill is rendered as an interactive button (not a plain
 *         <Badge> / <span>) so it is keyboard-accessible and click-targetable
 *     (b) clicking it sets selectedProjectId, which narrows the list so only
 *         todos from that project are shown (the other project's todo
 *         disappears from the DOM)
 *
 * Current state of todos-view.tsx:273-278: the project name is rendered as a
 * static <Badge variant="outline"> with no onClick handler and no
 * onFilterByProject prop on TodoRow — so BOTH assertions fail correctly today.
 *
 * The test uses the TASK-026 jsdom render harness pattern:
 *   - Mock boundaries: @tanstack/react-query, @/utils/trpc, next/navigation,
 *     sonner, and heavy children (Editor, LibraryDetailView, BulkOpsBar,
 *     MetadataConflictBadge, JkHint, TaskToolbar).
 *   - Real data shapes (Todo + Project types from the production source).
 *   - No mock at the boundary under test: the row pill click path is NOT mocked.
 *
 * Run:
 *   cd app && bun vitest run apps/dashboard/__tests__/feat2-task034-row-project-pill-filter.test.tsx
 */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

// ─── Fixtures ────────────────────────────────────────────────────────────────
// Real shapes from todos-view.tsx: Todo + Project types.

type Todo = {
	id: string;
	content: string;
	projectId: string | null;
	projectName: string | null;
	projectPrefix: string | null;
	checked: boolean;
	checkedAt: string | null;
	tags: string[];
	order: number;
	attachmentCount: number;
};

type Project = { id: string; name: string };

const PROJECT_A: Project = { id: "proj-a", name: "Alpha Project" };
const PROJECT_B: Project = { id: "proj-b", name: "Beta Project" };

const TODO_A: Todo = {
	id: "todo-1",
	content: "Todo from Alpha",
	projectId: PROJECT_A.id,
	projectName: PROJECT_A.name,
	projectPrefix: "ALP",
	checked: false,
	checkedAt: null,
	tags: [],
	order: 0,
	attachmentCount: 0,
};

const TODO_B: Todo = {
	id: "todo-2",
	content: "Todo from Beta",
	projectId: PROJECT_B.id,
	projectName: PROJECT_B.name,
	projectPrefix: "BET",
	checked: false,
	checkedAt: null,
	tags: [],
	order: 1,
	attachmentCount: 0,
};

// ─── Mocks ────────────────────────────────────────────────────────────────────
// Registered before any dynamic import so vi.mock hoisting works correctly.

vi.mock("next/navigation", () => ({
	useParams: () => ({ team: "team-1" }),
	useRouter: () => ({ push: () => {} }),
	usePathname: () => "/",
	useSearchParams: () => new URLSearchParams(),
}));

vi.mock("sonner", () => ({
	toast: Object.assign(() => {}, { success: () => {}, error: () => {} }),
}));

// trpc proxy: every property-chain access returns an object with queryOptions /
// mutationOptions / queryKey — mirrors the TASK-026 harness pattern exactly.
vi.mock("@/utils/trpc", () => {
	const queryKey = () => ["mock"];
	const queryOptions = (input: unknown) => ({ queryKey: ["mock"], input });
	const mutationOptions = (opts: unknown) => ({ mock: true, opts });
	const handler: ProxyHandler<Record<string, unknown>> = {
		get(_target, prop) {
			if (prop === "queryOptions") return queryOptions;
			if (prop === "mutationOptions") return mutationOptions;
			if (prop === "queryKey") return queryKey;
			return new Proxy({}, handler);
		},
	};
	return { trpc: new Proxy({}, handler) };
});

// Track which queryOptions input has been called with so we can simulate
// filtering. selectedProjectId state inside TodosView drives this.
//
// The mock starts with both todos visible. Once the component re-renders after
// the click with projectId in the queryOptions input, the mock returns only
// TODO_A (filtered). We detect the filter by checking opts.input.projectId.
vi.mock("@tanstack/react-query", () => {
	return {
		useQueryClient: () => ({
			invalidateQueries: () => {},
			cancelQueries: () => Promise.resolve(),
			getQueriesData: (): Array<[unknown, unknown]> => [],
			setQueriesData: () => {},
			setQueryData: () => {},
		}),
		useMutation: () => ({
			mutate: () => {},
			isPending: false,
		}),
		useQuery: (opts: {
			queryKey?: unknown[];
			input?: { projectId?: string; pageSize?: number; tag?: string };
		}) => {
			const input = opts?.input;
			// projects.get call: has pageSize — return empty so the project filter
			// strip doesn't render and doesn't collide with the row pill button.
			if (input && "pageSize" in input) {
				return {
					data: { data: [] as Project[] },
					isLoading: false,
					isFetching: false,
				};
			}
			// todos.get filtered by projectId
			if (input?.projectId === PROJECT_A.id) {
				return { data: [TODO_A], isLoading: false, isFetching: false };
			}
			// todos.get unfiltered (or tag-source query)
			return {
				data: [TODO_A, TODO_B],
				isLoading: false,
				isFetching: false,
			};
		},
	};
});

// Mock heavy children that pull in incompatible deps or cause render cycles.
vi.mock("@/components/editor", () => ({ Editor: (): null => null }));
vi.mock("@/components/library/detail-view", () => ({
	LibraryDetailView: (): null => null,
}));
vi.mock("@/components/tasks/bulk-ops-bar", () => ({
	BulkOpsBar: (): null => null,
	useBindBulkSelection: () => {},
}));
vi.mock("@/components/tasks/metadata-conflict-badge", () => ({
	MetadataConflictBadge: (): null => null,
}));
vi.mock("@/components/jk-hint", () => ({ JkHint: (): null => null }));
vi.mock("@/components/tasks/task-toolbar", () => ({
	TaskToolbar: (): null => null,
	useToolbarGroupBy: () => ["none", () => {}] as const,
}));
vi.mock("@/hooks/use-jk-navigation", () => ({
	useJkNavigation: () => ({
		focusedId: null as string | null,
		setFocusedId: () => {},
		isFocused: () => false,
	}),
}));
vi.mock("@/hooks/use-shortcuts", () => ({ useShortcut: () => {} }));
vi.mock("@/hooks/use-task-params", () => ({
	useTaskParams: () => ({ params: {}, setParams: () => {} }),
}));
vi.mock("@/stores/task-selection", () => ({
	useTaskSelection: (
		selector: (s: {
			selected: Set<string>;
			toggle: () => void;
			rangeTo: () => void;
			clear: () => void;
		}) => unknown,
	) =>
		selector({
			selected: new Set(),
			toggle: () => {},
			rangeTo: () => {},
			clear: () => {},
		}),
}));
vi.mock("./todo-dnd-provider", () => ({
	useTodoSortableHandler: (): null => null,
}));
vi.mock("@dnd-kit/sortable", () => ({
	SortableContext: ({ children }: { children: React.ReactNode }) => children,
	useSortable: () => ({
		attributes: {} as Record<string, unknown>,
		listeners: {} as Record<string, unknown>,
		setNodeRef: () => {},
		transform: null as null,
		transition: null as null,
		isDragging: false,
	}),
	verticalListSortingStrategy: "vertical",
	arrayMove: (arr: unknown[], from: number, to: number): unknown[] => {
		const next = [...arr];
		next.splice(to, 0, next.splice(from, 1)[0]);
		return next;
	},
}));
vi.mock("@dnd-kit/core", () => ({
	CSS: { Transform: { toString: (): string => "" } },
}));
vi.mock("@dnd-kit/utilities", () => ({
	CSS: { Transform: { toString: (): string => "" } },
}));
vi.mock("react-hotkeys-hook", () => ({ useHotkeys: () => {} }));

// ─── Component under test (imported after mocks) ──────────────────────────────
let TodosView: typeof import("@/components/todos/todos-view").TodosView;

beforeEach(async () => {
	({ TodosView } = await import("@/components/todos/todos-view"));
});

afterEach(() => {
	vi.clearAllMocks();
	vi.resetAllMocks();
});

// ─── Tests ────────────────────────────────────────────────────────────────────

describe("TASK-034 — row project pill is interactive and filters the todo list", () => {
	/**
	 * AC(a): The project pill on each todo row is rendered as a button
	 *        (keyboard-accessible, click-targetable) — NOT a static Badge.
	 *
	 * GIVEN TodosView mounted with a todo that has a projectName
	 * WHEN the rendered DOM is inspected
	 * THEN the project name appears as a role="button" element
	 */
	test("AC(a): row project pill is rendered as a button (not a static Badge)", () => {
		render(<TodosView />);

		// The project name "ALP · Alpha Project" (or similar) on the row must
		// be an interactive button — getByRole will fail if it's a plain <span>
		// or <div> without an explicit role.
		const pill = screen.getByRole("button", { name: /alpha project/i });
		expect(pill).toBeInTheDocument();
	});

	/**
	 * AC(b): Clicking the row project pill sets selectedProjectId, narrowing
	 *        the list so only that project's todos remain visible.
	 *
	 * GIVEN TodosView shows TODO_A (Alpha Project) and TODO_B (Beta Project)
	 * WHEN the user clicks the project pill on the Alpha Project row
	 * THEN TODO_B ("Todo from Beta") is no longer in the document
	 */
	test("AC(b): clicking the row project pill filters the list to that project", async () => {
		const user = userEvent.setup();
		render(<TodosView />);

		// Both todos should be initially visible.
		expect(screen.getByText("Todo from Alpha")).toBeInTheDocument();
		expect(screen.getByText("Todo from Beta")).toBeInTheDocument();

		// Click the project pill on the Alpha Project row.
		// Currently a static Badge — getByRole will throw because there is no
		// button with this label, so the test fails for the right reason.
		const alphaProjectPill = screen.getByRole("button", {
			name: /alpha project/i,
		});
		await user.click(alphaProjectPill);

		// After filtering, only Alpha todos should remain.
		await waitFor(() => {
			expect(screen.queryByText("Todo from Beta")).not.toBeInTheDocument();
		});
		expect(screen.getByText("Todo from Alpha")).toBeInTheDocument();
	});
});
